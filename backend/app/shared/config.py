"""Process configuration (canonical names in ``app.contracts.env``).

Secrets have no default: the process refuses to start without them. Modes
that would need real provider credentials refuse to start when those are
missing, instead of failing on the first real message.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # .env.example: "vacío = valor por defecto"
        extra="ignore",
    )

    database_url: PostgresDsn

    ollama_base_url: HttpUrl | None = None
    ollama_chat_model: str | None = None
    ollama_embed_model: str | None = None
    embed_dim: int = Field(default=1024, gt=0)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    # Audit 2026-11-04 (REL-01): a turn keeps its DB connection while it waits for the model,
    # so the turns waiting for it are bounded (in flight + queued < DB pool of 15). Beyond
    # that the turn degrades at once (LLM_NO_DISPONIBLE) instead of exhausting the pool (500).
    llm_max_inflight: int = Field(default=4, gt=0)
    llm_max_queue: int = Field(default=8, ge=0)

    rag_top_k: int = Field(default=5, ge=1, le=20)
    confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    crit003_mode: Literal["claim_context", "literal"] = "claim_context"
    crit005_mode: Literal["incident_context", "literal"] = "incident_context"
    insurance_min_source: Literal["pdf", "csv"] = "pdf"
    handoff_wait_seconds: int = Field(default=45, ge=0)

    # Provisional (DEC-CH2): no source fixes them.
    session_ttl_minutes_web_chat: int = Field(default=30, gt=0)
    session_ttl_minutes_whatsapp: int = Field(default=1440, gt=0)
    session_ttl_minutes_pbx: int = Field(default=15, gt=0)
    idle_reminder_minutes_web_chat: int = Field(default=5, gt=0)

    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_mode: Literal["SIMULATED", "REAL"] = "SIMULATED"
    wa_max_body_chars: int = Field(default=4096, gt=0)  # validar contra la documentación de Meta
    pbx_mode: Literal["SIMULATED", "REAL"] = "SIMULATED"

    admin_token: SecretStr = Field(min_length=16)
    pii_hash_salt: SecretStr = Field(min_length=16)

    data_source_dir: Path = REPO_ROOT / "data" / "source"
    accounts_file: Path | None = REPO_ROOT / "data" / "fixtures" / "cuentas_simuladas.json"
    business_holidays_file: Path | None = None
    attachments_dir: Path = REPO_ROOT / "var" / "attachments"
    attachments_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    # Audit 2026-11-04 (SEC-04/05): any other body is cut at this size before it is buffered.
    max_request_body_bytes: int = Field(default=1024 * 1024, gt=0)

    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    chat_rate_limit_per_minute: int = Field(default=30, ge=0)  # 0 = sin límite
    # Bulk shipments (B2B API, contract 1.3.0).
    batch_max_items: int = Field(default=500, gt=0)
    batch_max_body_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    batch_rate_limit_per_minute: int = Field(default=10, ge=0)  # 0 = sin límite
    batch_lease_seconds: int = Field(default=60, gt=0)
    # Tariff profiles (contract 1.5.0): only profiles of this type are used.
    tariff_environment: Literal["TEST_ONLY", "COMMERCIAL"] = "TEST_ONLY"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    clock_override: AwareDatetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _split_origins(cls, data: object) -> object:
        if isinstance(data, dict):
            origins = data.get("cors_allow_origins")
            if isinstance(origins, str):
                data["cors_allow_origins"] = [o.strip() for o in origins.split(",") if o.strip()]
        return data

    @model_validator(mode="after")
    def _modes_need_their_credentials(self) -> Self:
        if self.whatsapp_mode == "REAL" and not (
            self.whatsapp_verify_token and self.whatsapp_app_secret
        ):
            raise ValueError(
                "WHATSAPP_MODE=REAL requires WHATSAPP_VERIFY_TOKEN and WHATSAPP_APP_SECRET"
            )
        if self.pbx_mode == "REAL":
            raise ValueError(
                "PBX_MODE=REAL is not implemented: no telephony provider is integrated"
            )
        if self.ollama_base_url and not (self.ollama_chat_model and self.ollama_embed_model):
            raise ValueError("OLLAMA_BASE_URL requires OLLAMA_CHAT_MODEL and OLLAMA_EMBED_MODEL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
