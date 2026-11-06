"""Base class and common contracts for licensed external market data providers."""

from abc import abstractmethod

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    ProviderError,
    ProviderHealth,
)


class LicensedProviderAuthError(ProviderError):
    """Raised when provider API key or authentication fails."""


class LicensedEODDataProvider(BaseMarketDataProvider):
    """Abstract base adapter for officially licensed commercial EOD data providers.

    Enforces API key presence, provides structured quota/health checking,
    and establishes boundaries for licensed BIST data integration.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key or api_key.strip() in ("", "changeme"):
            raise LicensedProviderAuthError(
                f"Valid API key is required for licensed provider '{self.name}'. "
                "Configure MARKET_DATA_API_KEY."
            )
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    @property
    def is_licensed(self) -> bool:
        """Commercial providers are official licensed feeds."""
        return True

    @property
    def base_url(self) -> str:
        """API base endpoint URL."""
        return self._base_url

    @abstractmethod
    def get_daily_quota_status(self) -> tuple[int | None, int | None]:
        """Return (quota_remaining, quota_limit) for current billing/daily window."""

    def health_check(self) -> ProviderHealth:
        """Check provider connectivity and report quota status."""
        try:
            remaining, limit = self.get_daily_quota_status()
            return ProviderHealth(
                is_healthy=True,
                provider_name=self.name,
                is_licensed=True,
                quota_remaining=remaining,
                quota_limit=limit,
                message="Connected to licensed data provider",
            )
        except Exception as exc:
            return ProviderHealth(
                is_healthy=False,
                provider_name=self.name,
                is_licensed=True,
                message=f"Provider health check failed: {exc}",
            )
