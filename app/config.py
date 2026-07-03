from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        "postgresql+asyncpg://edi:edipass@localhost:5432/edi_middleware"
    )
    database_sync_url: str = Field(
        "postgresql+psycopg2://edi:edipass@localhost:5432/edi_middleware"
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── App ───────────────────────────────────────────────────────────────────
    secret_key: str = "change-me"
    environment: Literal["local", "staging", "production"] = "local"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ── Sentry ────────────────────────────────────────────────────────────────
    sentry_dsn: str = ""

    # ── SAP Business One ─────────────────────────────────────────────────────
    b1_service_layer_url: str = ""
    b1_company_db: str = ""
    b1_username: str = ""
    b1_password: str = ""
    b1_session_pool_size: int = 2
    b1_verify_ssl: bool = True

    # ── Gmail ─────────────────────────────────────────────────────────────────
    gmail_credentials_path: str = "./credentials/gmail_credentials.json"
    gmail_token_path: str = "./credentials/gmail_token.json"

    # ── Seller entity ─────────────────────────────────────────────────────────
    seller_gstin: str = ""
    seller_name: str = "Let's Try Foods Private Limited"
    seller_b1_company_db: str = ""

    # ── Blinkit ───────────────────────────────────────────────────────────────
    blinkit_api_key: str = ""
    blinkit_vendor_id: str = "18309"
    blinkit_base_url: str = "https://dev.partnersbiz.com"
    blinkit_path_asn: str = "webhook/public/v1/asn"
    blinkit_path_po_ack: str = "webhook/public/v1/po/acknowledgement"

    # ── Zepto ─────────────────────────────────────────────────────────────────
    zepto_client_id: str = ""
    zepto_client_secret: str = ""
    zepto_base_url: str = ""

    # ── Swiggy ────────────────────────────────────────────────────────────────
    swiggy_api_key: str = ""

    # ── LLM fallback ─────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    llm_fallback_model: str = "claude-sonnet-4-5"

    # ── Render proxy (local dev) ──────────────────────────────────────────────
    render_url: str = ""

    # ── Attachments ───────────────────────────────────────────────────────────
    attachment_base_path: str = "./data/attachments"


@lru_cache
def get_settings() -> Settings:
    return Settings()
