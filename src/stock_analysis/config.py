from typing import Any

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """Application configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql://user:password@localhost:5432/borsa_dev",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
    )
    market_data_provider: str = Field(default="mock", alias="MARKET_DATA_PROVIDER")
    market_data_api_key: str = Field(default="changeme", alias="MARKET_DATA_API_KEY")
    session_secret: str = Field(
        default="changeme-generate-long-random",
        alias="SESSION_SECRET",
    )
    email_provider_api_key: str = Field(default="changeme", alias="EMAIL_PROVIDER_API_KEY")
    email_from_address: str = Field(
        default="noreply@borsaanaliz.local",
        alias="EMAIL_FROM_ADDRESS",
    )
    email_sender_domain: str = Field(
        default="borsaanaliz.local",
        alias="EMAIL_SENDER_DOMAIN",
    )
    email_dmarc_policy: str = Field(
        default="reject",
        alias="EMAIL_DMARC_POLICY",
    )
    email_dkim_selector: str = Field(
        default="default",
        alias="EMAIL_DKIM_SELECTOR",
    )

    # LLM Settings (optional flags)
    llm_enabled: bool = Field(default=False, alias="LLM_ENABLED")
    llm_api_key: str = Field(default="changeme", alias="LLM_API_KEY")
    llm_model: str = Field(default="configure-me", alias="LLM_MODEL")
    llm_max_output_tokens: int = Field(default=300, alias="LLM_MAX_OUTPUT_TOKENS")
    llm_daily_budget_usd: float = Field(default=1.0, alias="LLM_DAILY_BUDGET_USD")
    llm_user_daily_calls: int = Field(default=10, alias="LLM_USER_DAILY_CALLS")

    # Forecast / Prediction Settings
    forecast_enabled: bool = Field(default=False, alias="FORECAST_ENABLED")

    # Database Performance & Monitoring Settings
    slow_query_logging_enabled: bool = Field(default=True, alias="SLOW_QUERY_LOGGING_ENABLED")
    slow_query_threshold_ms: float = Field(default=200.0, alias="SLOW_QUERY_THRESHOLD_MS")

    # Security, TLS, CORS & CSP Settings
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        alias="CORS_ALLOWED_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    cors_allowed_methods: list[str] = Field(
        default=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        alias="CORS_ALLOWED_METHODS",
    )
    cors_allowed_headers: list[str] = Field(
        default=["*"],
        alias="CORS_ALLOWED_HEADERS",
    )
    cors_exposed_headers: list[str] = Field(
        default=[
            "X-Disclaimer",
            "ETag",
            "Sunset",
            "Deprecation",
            "Link",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
        alias="CORS_EXPOSED_HEADERS",
    )
    enable_hsts: bool = Field(default=True, alias="ENABLE_HSTS")
    hsts_max_age_seconds: int = Field(default=31536000, alias="HSTS_MAX_AGE_SECONDS")
    hsts_include_subdomains: bool = Field(default=True, alias="HSTS_INCLUDE_SUBDOMAINS")
    hsts_preload: bool = Field(default=False, alias="HSTS_PRELOAD")
    csp_enabled: bool = Field(default=True, alias="CSP_ENABLED")
    csp_report_only: bool = Field(default=False, alias="CSP_REPORT_ONLY")
    custom_csp_directives: str | None = Field(default=None, alias="CUSTOM_CSP_DIRECTIVES")

    @field_validator(
        "cors_allowed_origins",
        "cors_allowed_methods",
        "cors_allowed_headers",
        "cors_exposed_headers",
        mode="before",
    )
    @classmethod
    def _parse_comma_separated_list(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                import json

                try:
                    return json.loads(value)
                except Exception:
                    pass
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customise settings sources to gracefully fall back on non-JSON complex strings."""

        class TolerantEnvSettingsSource(EnvSettingsSource):
            def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
                try:
                    return super().decode_complex_value(field_name, field, value)
                except Exception:
                    return value

        class TolerantDotEnvSettingsSource(DotEnvSettingsSource):
            def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
                try:
                    return super().decode_complex_value(field_name, field, value)
                except Exception:
                    return value

        dotenv_file = settings_cls.model_config.get("env_file")
        return (
            init_settings,
            TolerantEnvSettingsSource(settings_cls),
            TolerantDotEnvSettingsSource(settings_cls, env_file=dotenv_file),
            file_secret_settings,
        )


def get_settings() -> Settings:
    """Return an application settings instance."""
    return Settings()
