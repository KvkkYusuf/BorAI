from pathlib import Path

from stock_analysis.config import Settings, get_settings


def test_default_settings() -> None:
    settings = get_settings()
    assert settings.app_env in {"development", "test", "production"}
    assert settings.market_data_provider == "mock"
    assert settings.forecast_enabled is False
    assert settings.llm_enabled is False


def test_env_example_matches_settings() -> None:
    env_example_path = Path(__file__).resolve().parent.parent / ".env.example"
    assert env_example_path.exists(), ".env.example file must exist"

    # Verify .env.example can be loaded by Settings without missing required fields or error
    settings = Settings(_env_file=str(env_example_path))  # type: ignore[call-arg]
    assert settings.app_env == "development"
    assert settings.database_url.startswith("postgresql://")
    assert settings.redis_url.startswith("redis://")
    assert settings.llm_enabled is False
