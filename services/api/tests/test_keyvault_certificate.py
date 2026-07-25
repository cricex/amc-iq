from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from app.config import Settings
from app.keyvault_certificate import (
    CertificateCredentialError,
    EnvironmentCertificateCredentialProvider,
    KeyVaultCertificateCredentialProvider,
    create_certificate_credential_provider,
)


class _SecretClient:
    def __init__(self, *values: str) -> None:
        self.values = list(values)
        self.names: list[str] = []

    def get_secret(self, name: str) -> SimpleNamespace:
        self.names.append(name)
        index = min(len(self.names) - 1, len(self.values) - 1)
        return SimpleNamespace(value=self.values[index])


class _FailingAfterFirstSecretClient(_SecretClient):
    def get_secret(self, name: str) -> SimpleNamespace:
        if self.names:
            self.names.append(name)
            return SimpleNamespace(value=base64.b64encode(b"invalid pfx").decode("ascii"))
        return super().get_secret(name)


def _certificate_secret() -> tuple[str, x509.Certificate]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "amciq-workiq-obo")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(private_key, hashes.SHA256())
    )
    pfx = pkcs12.serialize_key_and_certificates(
        b"amciq-workiq-obo",
        private_key,
        certificate,
        None,
        serialization.NoEncryption(),
    )
    return base64.b64encode(pfx).decode("ascii"), certificate


def _production_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_ENVIRONMENT="production",
        AZURE_CLIENT_ID="managed-identity-client",
        WORK_IQ_KEY_VAULT_URL="https://test-key-vault.vault.azure.net/",
        WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME="amciq-workiq-obo",
    )


def test_key_vault_provider_converts_pfx_for_msal_and_caches_it() -> None:
    secret_value, certificate = _certificate_secret()
    secret_client = _SecretClient(secret_value)
    provider = KeyVaultCertificateCredentialProvider(
        _production_settings(),
        secret_client=secret_client,
    )

    first = provider.get_client_credential()
    second = provider.get_client_credential()

    assert first == second
    assert first is not second
    assert first["private_key"].startswith("-----BEGIN PRIVATE KEY-----")
    assert first["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert first["thumbprint"] == certificate.fingerprint(hashes.SHA1()).hex().upper()
    assert secret_client.names == ["amciq-workiq-obo"]


def test_key_vault_provider_refreshes_rotated_certificate_after_cache_ttl() -> None:
    first_secret, first_certificate = _certificate_secret()
    second_secret, second_certificate = _certificate_secret()
    settings = _production_settings()
    settings.WORK_IQ_CERTIFICATE_CACHE_SECONDS = 0
    secret_client = _SecretClient(first_secret, second_secret)
    provider = KeyVaultCertificateCredentialProvider(settings, secret_client=secret_client)

    first = provider.get_client_credential()
    second = provider.get_client_credential()

    assert first["thumbprint"] == first_certificate.fingerprint(hashes.SHA1()).hex().upper()
    assert second["thumbprint"] == second_certificate.fingerprint(hashes.SHA1()).hex().upper()
    assert first["thumbprint"] != second["thumbprint"]
    assert secret_client.names == ["amciq-workiq-obo", "amciq-workiq-obo"]


def test_refresh_failure_serves_cached_certificate_with_retry_backoff() -> None:
    secret_value, _ = _certificate_secret()
    settings = _production_settings()
    settings.WORK_IQ_CERTIFICATE_CACHE_SECONDS = 0
    settings.WORK_IQ_CERTIFICATE_REFRESH_RETRY_SECONDS = 60
    secret_client = _FailingAfterFirstSecretClient(secret_value)
    provider = KeyVaultCertificateCredentialProvider(settings, secret_client=secret_client)

    cached = provider.get_client_credential()
    stale = provider.get_client_credential()
    during_backoff = provider.get_client_credential()

    assert stale == cached
    assert during_backoff == cached
    assert secret_client.names == ["amciq-workiq-obo", "amciq-workiq-obo"]


def test_key_vault_provider_rejects_invalid_certificate_secret() -> None:
    provider = KeyVaultCertificateCredentialProvider(
        _production_settings(),
        secret_client=_SecretClient(base64.b64encode(b"not a pfx").decode("ascii")),
    )
    with pytest.raises(CertificateCredentialError, match="PKCS12"):
        provider.get_client_credential()


def test_initial_failure_uses_retry_backoff_instead_of_requerying_key_vault() -> None:
    settings = _production_settings()
    settings.WORK_IQ_CERTIFICATE_REFRESH_RETRY_SECONDS = 60
    secret_client = _SecretClient(base64.b64encode(b"not a pfx").decode("ascii"))
    provider = KeyVaultCertificateCredentialProvider(settings, secret_client=secret_client)

    with pytest.raises(CertificateCredentialError, match="PKCS12"):
        provider.get_client_credential()
    with pytest.raises(CertificateCredentialError, match="retry backoff"):
        provider.get_client_credential()

    assert secret_client.names == ["amciq-workiq-obo"]


def test_production_always_selects_managed_identity_key_vault_provider() -> None:
    settings = _production_settings()
    settings.WORK_IQ_CLIENT_CERTIFICATE = "local-private-key"
    settings.WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT = "local-thumbprint"

    provider = create_certificate_credential_provider(settings)

    assert isinstance(provider, KeyVaultCertificateCredentialProvider)
    assert not hasattr(settings, "WORK_IQ_CLIENT_SECRET")


def test_development_can_use_explicit_local_certificate_without_client_secret() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENVIRONMENT="development",
        WORK_IQ_CLIENT_CERTIFICATE="local-private-key",
        WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT="local-thumbprint",
    )

    provider = create_certificate_credential_provider(settings)

    assert isinstance(provider, EnvironmentCertificateCredentialProvider)
    assert provider.get_client_credential() == {
        "private_key": "local-private-key",
        "thumbprint": "local-thumbprint",
    }
