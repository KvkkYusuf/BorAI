"""Market data provider adapters and factory."""

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    DataStatus,
    MarketBarRecord,
    ProviderDataFormatError,
    ProviderError,
    ProviderHealth,
    ProviderQuotaExceededError,
    ProviderSchemaError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from stock_analysis.providers.cached_provider import (
    CachedMarketDataProvider,
    MarketDataCache,
    ProviderQuotaTracker,
    QuotaStats,
    generate_data_version,
)
from stock_analysis.providers.factory import get_market_data_provider
from stock_analysis.providers.licensed_base import (
    LicensedEODDataProvider,
    LicensedProviderAuthError,
)
from stock_analysis.providers.live_contract import (
    LIVE_DATA_CONTRACT_AUDITOR,
    ContractStatus,
    ContractTier,
    EntitlementCheckResult,
    LiveDataContractAuditor,
    LiveDataEntitlementError,
    ProviderContractMetadata,
    RedistributionScope,
)
from stock_analysis.providers.resilience import (
    ResilientMarketDataProvider,
    RetryPolicy,
    execute_with_retry,
)
from stock_analysis.providers.synthetic_provider import SyntheticMarketDataProvider

__all__ = [
    "BaseMarketDataProvider",
    "CachedMarketDataProvider",
    "ContractStatus",
    "ContractTier",
    "DataStatus",
    "EntitlementCheckResult",
    "LIVE_DATA_CONTRACT_AUDITOR",
    "LicensedEODDataProvider",
    "LicensedProviderAuthError",
    "LiveDataContractAuditor",
    "LiveDataEntitlementError",
    "MarketBarRecord",
    "MarketDataCache",
    "ProviderContractMetadata",
    "ProviderDataFormatError",
    "ProviderError",
    "ProviderHealth",
    "ProviderQuotaExceededError",
    "ProviderQuotaTracker",
    "ProviderSchemaError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "QuotaStats",
    "RedistributionScope",
    "ResilientMarketDataProvider",
    "RetryPolicy",
    "SyntheticMarketDataProvider",
    "execute_with_retry",
    "generate_data_version",
    "get_market_data_provider",
]
