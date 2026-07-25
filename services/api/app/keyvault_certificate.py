from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Protocol

from azure.core.exceptions import AzureError
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from app.config import Settings

logger = logging.getLogger(__name__)


class CertificateCredentialError(RuntimeError):
    pass


class CertificateCredentialProvider(Protocol):
    def get_client_credential(self) -> dict[str, str]:
        ...


class SecretClientProtocol(Protocol):
    def get_secret(self, name: str):  # type: ignore[no-untyped-def]
        ...


class EnvironmentCertificateCredentialProvider:
    """PEM certificate provider for explicit local development only."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_client_credential(self) -> dict[str, str]:
        missing = [
            name
            for name, value in (
                ("WORK_IQ_CLIENT_CERTIFICATE", self.settings.WORK_IQ_CLIENT_CERTIFICATE),
                (
                    "WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT",
                    self.settings.WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT,
                ),
            )
            if not value.strip()
        ]
        if missing:
            raise CertificateCredentialError(
                f"Local Work IQ certificate configuration is missing: {', '.join(missing)}"
            )
        credential = {
            "private_key": self.settings.WORK_IQ_CLIENT_CERTIFICATE,
            "thumbprint": self.settings.WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT,
        }
        if self.settings.WORK_IQ_CLIENT_PUBLIC_CERTIFICATE.strip():
            credential["public_certificate"] = self.settings.WORK_IQ_CLIENT_PUBLIC_CERTIFICATE
        return credential


class KeyVaultCertificateCredentialProvider:
    """Loads and caches the exportable PFX backing a Key Vault certificate via managed identity."""

    def __init__(
        self,
        settings: Settings,
        *,
        secret_client: SecretClientProtocol | None = None,
    ) -> None:
        self.settings = settings
        self._secret_client = secret_client
        self._credential: dict[str, str] | None = None
        self._refresh_at = 0.0
        self._last_error: CertificateCredentialError | None = None
        self._lock = threading.Lock()

    def get_client_credential(self) -> dict[str, str]:
        now = time.monotonic()
        if self._credential is not None and now < self._refresh_at:
            return dict(self._credential)
        if self._credential is None and self._last_error is not None and now < self._refresh_at:
            raise CertificateCredentialError(
                "Work IQ certificate retrieval is in retry backoff"
            ) from self._last_error
        with self._lock:
            now = time.monotonic()
            if self._credential is not None and now < self._refresh_at:
                return dict(self._credential)
            if self._credential is None and self._last_error is not None and now < self._refresh_at:
                raise CertificateCredentialError(
                    "Work IQ certificate retrieval is in retry backoff"
                ) from self._last_error
            try:
                refreshed = self._load()
            except CertificateCredentialError as exc:
                self._last_error = exc
                self._refresh_at = now + max(
                    1.0, self.settings.WORK_IQ_CERTIFICATE_REFRESH_RETRY_SECONDS
                )
                if self._credential is None:
                    raise
                logger.warning(
                    "Work IQ certificate refresh failed; serving the cached certificate until retry"
                )
                return dict(self._credential)
            self._credential = refreshed
            self._last_error = None
            self._refresh_at = now + max(0.0, self.settings.WORK_IQ_CERTIFICATE_CACHE_SECONDS)
            return dict(self._credential)

    def _load(self) -> dict[str, str]:
        missing = [
            name
            for name, value in (
                ("AZURE_CLIENT_ID", self.settings.AZURE_CLIENT_ID),
                ("WORK_IQ_KEY_VAULT_URL", self.settings.WORK_IQ_KEY_VAULT_URL),
                (
                    "WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME",
                    self.settings.WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME,
                ),
            )
            if not value.strip()
        ]
        if missing:
            raise CertificateCredentialError(
                f"Production Work IQ certificate configuration is missing: {', '.join(missing)}"
            )

        secret_value = self._get_secret_value()
        try:
            pfx = base64.b64decode(secret_value, validate=True)
            private_key, certificate, chain = pkcs12.load_key_and_certificates(pfx, None)
        except (ValueError, TypeError) as exc:
            raise CertificateCredentialError(
                "Work IQ Key Vault certificate secret is not a valid unencrypted PKCS12 value"
            ) from exc
        if private_key is None or certificate is None:
            raise CertificateCredentialError(
                "Work IQ Key Vault certificate must contain an exportable private key"
            )

        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        public_chain_pem = b"".join(
            item.public_bytes(serialization.Encoding.PEM)
            for item in (certificate, *(chain or ()))
        ).decode("ascii")
        return {
            "private_key": private_key_pem,
            "thumbprint": certificate.fingerprint(hashes.SHA1()).hex().upper(),
            "public_certificate": public_chain_pem,
        }

    def _get_secret_value(self) -> str:
        if self._secret_client is not None:
            secret = self._secret_client.get_secret(
                self.settings.WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME
            )
            if secret is None:
                raise CertificateCredentialError("Work IQ Key Vault certificate secret is missing")
            value = secret.value
            if not value:
                raise CertificateCredentialError("Work IQ Key Vault certificate secret is empty")
            return value

        credential = ManagedIdentityCredential(client_id=self.settings.AZURE_CLIENT_ID)
        client = SecretClient(
            vault_url=self.settings.WORK_IQ_KEY_VAULT_URL,
            credential=credential,
        )
        try:
            secret = client.get_secret(self.settings.WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME)
            if secret is None:
                raise CertificateCredentialError("Work IQ Key Vault certificate secret is missing")
            value = secret.value
            if not value:
                raise CertificateCredentialError("Work IQ Key Vault certificate secret is empty")
            return value
        except AzureError as exc:
            raise CertificateCredentialError(
                "Managed identity could not retrieve the Work IQ certificate from Key Vault"
            ) from exc
        finally:
            client.close()
            credential.close()


def create_certificate_credential_provider(settings: Settings) -> CertificateCredentialProvider:
    if settings.APP_ENVIRONMENT.casefold() != "production":
        return EnvironmentCertificateCredentialProvider(settings)
    return KeyVaultCertificateCredentialProvider(settings)
