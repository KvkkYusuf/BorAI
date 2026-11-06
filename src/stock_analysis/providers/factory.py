"""Factory function to instantiate the configured market data provider."""

from stock_analysis.config import Settings, get_settings
from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    ProviderUnavailableError,
)
from stock_analysis.providers.synthetic_provider import SyntheticMarketDataProvider


def get_market_data_provider(
    settings: Settings | None = None,
    provider_name: str | None = None,
) -> BaseMarketDataProvider:
    """Return the market data provider instance based on application configuration.

    Args:
        settings: Application Settings instance (defaults to get_settings())
        provider_name: Explicit override for provider name.

    Returns:
        Configured BaseMarketDataProvider instance.
    """
    if settings is None:
        settings = get_settings()

    target_name = (provider_name or settings.market_data_provider).strip().lower()

    if target_name in ("mock", "synthetic", "synthetic_generator"):
        return SyntheticMarketDataProvider()

    raise ProviderUnavailableError(
        f"Market data provider '{target_name}' is not configured or licensed in this environment. "
        "Use 'mock' / 'synthetic' for development or configure a licensed provider."
    )
