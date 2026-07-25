from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import Settings, get_settings


class ApiAuthError(Exception):
    def __init__(self, message: str, *, status_code: int = 401, code: str = "invalid_token") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AuthConfigurationError(ApiAuthError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503, code="auth_configuration_error")


@dataclass(frozen=True)
class AuthenticatedUser:
    access_token: str
    tenant_id: str
    subject: str
    scopes: frozenset[str]
    claims: dict[str, Any]


class AccessTokenValidator:
    def __init__(self, settings: Settings, jwks_client: PyJWKClient | None = None) -> None:
        self.settings = settings
        self._validate_configuration()
        self.jwks_client = jwks_client or PyJWKClient(
            settings.jwks_url,
            cache_keys=True,
            timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
        )

    def _validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("AZURE_TENANT_ID", self.settings.AZURE_TENANT_ID),
                ("API_AUDIENCE", self.settings.API_AUDIENCE),
                ("API_REQUIRED_SCOPE", self.settings.API_REQUIRED_SCOPE),
            )
            if not value.strip()
        ]
        if missing:
            raise AuthConfigurationError(f"API authentication is not configured: {', '.join(missing)}")

    def validate(self, token: str) -> AuthenticatedUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise ApiAuthError("Bearer token is malformed") from exc
        if header.get("alg") != "RS256":
            raise ApiAuthError("Bearer token must use RS256")

        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.API_AUDIENCE,
                issuer=self.settings.token_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "tid", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ApiAuthError("Bearer token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise ApiAuthError("Bearer token audience is invalid") from exc
        except jwt.InvalidIssuerError as exc:
            raise ApiAuthError("Bearer token issuer is invalid") from exc
        except jwt.PyJWTError as exc:
            raise ApiAuthError("Bearer token validation failed") from exc

        tenant_id = str(claims.get("tid", ""))
        if tenant_id.casefold() != self.settings.AZURE_TENANT_ID.casefold():
            raise ApiAuthError("Bearer token tenant is invalid")

        scopes = frozenset(str(claims.get("scp", "")).split())
        if self.settings.API_REQUIRED_SCOPE not in scopes:
            raise ApiAuthError(
                f"Bearer token is missing required scope {self.settings.API_REQUIRED_SCOPE}",
                status_code=403,
                code="insufficient_scope",
            )

        return AuthenticatedUser(
            access_token=token,
            tenant_id=tenant_id,
            subject=str(claims["sub"]),
            scopes=scopes,
            claims=claims,
        )


_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_access_token_validator() -> AccessTokenValidator:
    return AccessTokenValidator(get_settings())


def require_api_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthenticatedUser | None:
    settings = get_settings()
    if credentials is None or credentials.scheme.casefold() != "bearer":
        if request.headers.get("Authorization") is None and settings.anonymous_mock_enabled:
            return None
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return get_access_token_validator().validate(credentials.credentials)
    except ApiAuthError as exc:
        authenticate = f'Bearer error="{exc.code}"'
        if exc.status_code == 403:
            authenticate += f', scope="{settings.API_REQUIRED_SCOPE}"'
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
            headers={"WWW-Authenticate": authenticate},
        ) from exc
