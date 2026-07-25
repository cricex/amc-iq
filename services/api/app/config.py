from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsConfigurationError(RuntimeError):
    """Raised when live or production settings are incomplete or still use public placeholders."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_data_dir() -> Path:
    return _repo_root() / "data"


def _is_operator_value(value: str) -> bool:
    normalized = value.strip().casefold()
    return bool(
        normalized
        and "your-" not in normalized
        and "${" not in normalized
        and not normalized.startswith("<")
        and ".example.invalid" not in normalized
        and normalized != "00000000-0000-0000-0000-000000000000"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    USE_LIVE_FOUNDRY: bool = False
    USE_LIVE_FABRIC: bool = False
    USE_LIVE_WORK: bool = False
    USE_LIVE_WEB: bool = False
    USE_LIVE_AGENT: bool = False
    # Multi-agent (Foundry specialist team) orchestration. Off by default: the single-agent path is
    # unchanged. When on, the specialist team drives the Assessment Steps trace and deepening.
    USE_MULTI_AGENT: bool = False
    AGENT_MAX_DEPTH: int = 1
    AGENT_MAX_LEADS: int = 2
    # Real Foundry specialist agents. When on (with USE_MULTI_AGENT), the roles in
    # LIVE_SPECIALIST_ROLES are narrated by live hosted Foundry agents that reason over the facts
    # already established for their role (they call a tool only to fill a gap), while structured
    # findings stay grounded in the Fabric eligibility verdicts. Any live-agent failure falls back to
    # the grounded specialist summary. Narrowed to a few roles because narrating all 6 live (each doing
    # KB + Fabric retrieval + synthesis) fans out too far and times out on one F64 Data Agent.
    USE_LIVE_SPECIALISTS: bool = False
    LIVE_SPECIALIST_ROLES: str = "eligibility,renal_labs,genomics"
    SPECIALIST_ELIGIBILITY_AGENT: str = ""
    SPECIALIST_RENAL_AGENT: str = ""
    SPECIALIST_GENOMICS_AGENT: str = ""
    SPECIALIST_PROTOCOL_AGENT: str = ""
    SPECIALIST_WORKFLOW_AGENT: str = ""
    SPECIALIST_EVIDENCE_AGENT: str = ""
    SPECIALIST_TIMEOUT: float = 90.0
    # Outside the narrowly gated local mock mode, /api/ask evaluates criteria via the Fabric Data
    # Agent (fact retrieval) + the Foundry eligibility-evaluator agent (reasoning). Live failures
    # surface to the caller and never fall back to local eligibility.
    ELIGIBILITY_EVALUATOR_AGENT: str = ""
    CORS_ORIGINS: str = "http://localhost:5173"
    DATA_DIR: Path = Field(default_factory=_default_data_dir)

    FOUNDRY_ENDPOINT: str = ""
    SEARCH_ENDPOINT: str = ""
    FOUNDRY_KB_NAME: str = ""
    WEB_KB_NAME: str = ""
    SEARCH_API_VERSION: str = "2026-05-01-preview"
    FABRIC_WORKSPACE_ID: str = ""
    FABRIC_DATA_AGENT_ID: str = ""
    FABRIC_LAKEHOUSE_ID: str = ""
    # Real Fabric IQ Ontology item (type "Ontology") over the oncology cohort. Bound to the Lakehouse
    # Delta tables; consumed by the Fabric Data Agent (NL2Ontology) and portal-visible.
    FABRIC_ONTOLOGY_ID: str = ""
    FABRIC_ONTOLOGY_NAME: str = ""
    FABRIC_API_SCOPE: str = "https://api.fabric.microsoft.com/.default"
    # ARM coordinates for the backing Fabric F64 capacity, used to surface its running/paused state
    # (read-only) in the UI. Read via DefaultAzureCredential against the ARM management endpoint.
    AZURE_SUBSCRIPTION_ID: str = ""
    FABRIC_CAPACITY_RG: str = ""
    FABRIC_CAPACITY_NAME: str = ""
    FABRIC_ARM_API_VERSION: str = "2023-11-01"
    ARM_SCOPE: str = "https://management.azure.com/.default"
    ARM_ENDPOINT: str = "https://management.azure.com"
    AZURE_TENANT_ID: str = ""
    API_AUDIENCE: str = ""
    API_REQUIRED_SCOPE: str = "access_as_user"
    AUTH_HTTP_TIMEOUT_SECONDS: float = 10.0
    APP_ENVIRONMENT: str = "production"
    AZURE_CLIENT_ID: str = ""
    WORK_IQ_ENDPOINT: str = "https://workiq.svc.cloud.microsoft/a2a/"
    WORK_IQ_SCOPE: str = "api://workiq.svc.cloud.microsoft/.default"
    WORK_IQ_CLIENT_ID: str = ""
    WORK_IQ_KEY_VAULT_URL: str = ""
    WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME: str = ""
    WORK_IQ_CLIENT_CERTIFICATE: str = ""
    WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT: str = ""
    WORK_IQ_CLIENT_PUBLIC_CERTIFICATE: str = ""
    WORK_IQ_CERTIFICATE_CACHE_SECONDS: float = 300.0
    WORK_IQ_CERTIFICATE_REFRESH_RETRY_SECONDS: float = 30.0
    WORK_IQ_TIMEOUT_SECONDS: float = 60.0
    WORK_IQ_TIMEZONE: str = "America/Chicago"
    WORK_IQ_TIMEZONE_OFFSET_MINUTES: int = -300
    WEB_IQ_ENDPOINT: str = ""
    MODEL_DEPLOYMENT: str = ""
    AGENT_NAME: str = ""
    PROJECT_ENDPOINT: str = ""

    @property
    def live_components_enabled(self) -> bool:
        return any(
            (
                self.USE_LIVE_FOUNDRY,
                self.USE_LIVE_FABRIC,
                self.USE_LIVE_WORK,
                self.USE_LIVE_WEB,
                self.USE_LIVE_AGENT,
                self.USE_LIVE_SPECIALISTS,
            )
        )

    @property
    def anonymous_mock_enabled(self) -> bool:
        """Allow anonymous deterministic requests only in an explicitly local/test environment."""
        local_environment = self.APP_ENVIRONMENT.strip().casefold() in {
            "development",
            "dev",
            "local",
            "test",
        }
        return local_environment and not self.live_components_enabled

    @computed_field
    @property
    def mode(self) -> Literal["mock", "live"]:
        return "mock" if self.anonymous_mock_enabled else "live"

    def validate_runtime_configuration(self) -> None:
        """Fail before startup when a live/production process lacks explicit operator settings."""
        if self.anonymous_mock_enabled:
            return

        required = {
            "AZURE_TENANT_ID",
            "API_AUDIENCE",
            "API_REQUIRED_SCOPE",
            # Eligibility remains Fabric-native outside the narrowly gated local mock mode.
            "FABRIC_WORKSPACE_ID",
            "FABRIC_DATA_AGENT_ID",
            "PROJECT_ENDPOINT",
            "ELIGIBILITY_EVALUATOR_AGENT",
        }
        if self.USE_LIVE_FOUNDRY:
            required.update({"SEARCH_ENDPOINT", "FOUNDRY_KB_NAME"})
        if self.USE_LIVE_WEB:
            required.update({"SEARCH_ENDPOINT", "WEB_KB_NAME"})
        if self.USE_LIVE_AGENT:
            required.update({"PROJECT_ENDPOINT", "AGENT_NAME"})
        if self.USE_LIVE_SPECIALISTS:
            required.add("PROJECT_ENDPOINT")
            role_to_setting = {
                "eligibility": "SPECIALIST_ELIGIBILITY_AGENT",
                "renal_labs": "SPECIALIST_RENAL_AGENT",
                "genomics": "SPECIALIST_GENOMICS_AGENT",
                "protocol": "SPECIALIST_PROTOCOL_AGENT",
                "workflow": "SPECIALIST_WORKFLOW_AGENT",
                "evidence": "SPECIALIST_EVIDENCE_AGENT",
            }
            for role in (item.strip().casefold() for item in self.LIVE_SPECIALIST_ROLES.split(",")):
                setting_name = role_to_setting.get(role)
                if setting_name:
                    required.add(setting_name)
        if self.USE_LIVE_WORK:
            required.update(
                {
                    "WORK_IQ_CLIENT_ID",
                    "WORK_IQ_ENDPOINT",
                    "WORK_IQ_SCOPE",
                }
            )
            if self.APP_ENVIRONMENT.strip().casefold() == "production":
                required.update(
                    {
                        "AZURE_CLIENT_ID",
                        "WORK_IQ_KEY_VAULT_URL",
                        "WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME",
                    }
                )
            else:
                required.update(
                    {
                        "WORK_IQ_CLIENT_CERTIFICATE",
                        "WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT",
                    }
                )

        missing = sorted(
            name for name in required if not _is_operator_value(str(getattr(self, name)))
        )
        if missing:
            raise SettingsConfigurationError(
                "Live/production configuration is incomplete or still uses public placeholders: "
                + ", ".join(missing)
            )

    @property
    def registry_path(self) -> Path:
        return self.DATA_DIR / "registry" / "registry.yaml"

    @property
    def fabric_mcp_url(self) -> str:
        return (
            f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{self.FABRIC_WORKSPACE_ID}"
            f"/dataagents/{self.FABRIC_DATA_AGENT_ID}/agent"
        )

    @property
    def fabric_portal_url(self) -> str:
        if not self.FABRIC_WORKSPACE_ID.strip():
            return ""
        return f"https://app.fabric.microsoft.com/groups/{self.FABRIC_WORKSPACE_ID}"

    @property
    def fabric_capacity_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.AZURE_SUBSCRIPTION_ID}"
            f"/resourceGroups/{self.FABRIC_CAPACITY_RG}"
            f"/providers/Microsoft.Fabric/capacities/{self.FABRIC_CAPACITY_NAME}"
        )

    @property
    def fabric_capacity_arm_url(self) -> str:
        return f"{self.ARM_ENDPOINT}{self.fabric_capacity_resource_id}?api-version={self.FABRIC_ARM_API_VERSION}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def token_issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.AZURE_TENANT_ID}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.AZURE_TENANT_ID}/discovery/v2.0/keys"

    @property
    def obo_authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.AZURE_TENANT_ID}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime_configuration()
    return settings
