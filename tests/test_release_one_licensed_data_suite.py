"""Unit and integration test suite for Release 1 (Licensed Data MVP) Gatekeeper."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    DataStatus,
    MarketBarRecord,
    ProviderHealth,
)
from stock_analysis.providers.live_contract import (
    ContractTier,
    LiveDataContractAuditor,
    ProviderContractMetadata,
    RedistributionScope,
)
from stock_analysis.release.release_one_licensed_data import (
    MAX_MVP_SYMBOL_LIMIT,
    MVP_ALLOWED_SYMBOLS,
    RELEASE_ONE_LICENSED_DATA_STATUS,
    ReleaseOneGateReport,
    ReleaseOneGateStatus,
    ReleaseOneLicensedDataGatekeeper,
    SymbolEntitlementError,
)


class MockLicensedDataProvider(BaseMarketDataProvider):
    """Mock licensed commercial market data provider for Release 1 testing."""

    def __init__(self, healthy: bool = True, return_empty: bool = False) -> None:
        self._healthy = healthy
        self._return_empty = return_empty

    @property
    def name(self) -> str:
        return "mock_matriks_licensed"

    @property
    def is_licensed(self) -> bool:
        return True

    def get_supported_symbols(self) -> list[str]:
        return sorted(MVP_ALLOWED_SYMBOLS)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            is_healthy=self._healthy,
            provider_name=self.name,
            is_licensed=True,
            quota_remaining=950,
            quota_limit=1000,
            message="Licensed feed active" if self._healthy else "Connection lost",
        )

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        bars = self.fetch_daily_bars(symbol, date(2026, 1, 1), date(2026, 1, 2))
        return bars[-1] if bars else None

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        if self._return_empty:
            return []
        return [
            MarketBarRecord(
                symbol=symbol,
                exchange="BIST",
                trade_date=date(2026, 1, 5),
                timezone="Europe/Istanbul",
                session_time_utc=datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC),
                currency="TRY",
                data_status=DataStatus.VERIFIED,
                raw_open=Decimal("120.00"),
                raw_high=Decimal("125.00"),
                raw_low=Decimal("118.00"),
                raw_close=Decimal("123.50"),
                adj_open=Decimal("120.00"),
                adj_high=Decimal("125.00"),
                adj_low=Decimal("118.00"),
                adj_close=Decimal("123.50"),
                volume=450_000,
                turnover_try=Decimal("55575000.00"),
                source_name="mock_matriks_licensed",
            )
        ]


def _build_test_contract_auditor() -> LiveDataContractAuditor:
    auditor = LiveDataContractAuditor()
    meta = ProviderContractMetadata(
        contract_id="CONTRACT-2026-MATRIKS-001",
        provider_name="mock_matriks_licensed",
        vendor_license_number="LIC-BIST-2026-0099",
        feed_tiers=[ContractTier.BIST_EQUITY_DELAYED_15MIN],
        redistribution_scopes=[RedistributionScope.DISPLAY, RedistributionScope.DERIVED_DATA],
        effective_date=date(2026, 1, 1),
        expiration_date=date(2027, 1, 1),
        commercial_use_approved=True,
        disclaimer_required=True,
    )
    auditor.register_contract(meta)
    return auditor


def test_release_one_licensed_gate_pass() -> None:
    """Verify that a compliant licensed provider passes all 6 Release 1 checks."""
    provider = MockLicensedDataProvider(healthy=True)
    auditor = _build_test_contract_auditor()
    gatekeeper = ReleaseOneLicensedDataGatekeeper(
        provider=provider,
        contract_auditor=auditor,
    )

    report = gatekeeper.evaluate()
    assert isinstance(report, ReleaseOneGateReport)
    assert report.status == ReleaseOneGateStatus.PASS
    assert report.total_checks == 6
    assert report.passed_checks == 6
    assert report.failed_checks == 0
    assert len(report.results) == 6

    # Verify Prometheus gauge was updated
    assert RELEASE_ONE_LICENSED_DATA_STATUS.get() == 1.0

    # Markdown format validation
    md = report.generate_markdown()
    assert "# Sürüm 1 (Veri ve Grafik MVP)" in md
    assert "CHECK-R1-01" in md
    assert "CHECK-R1-06" in md
    assert "THYAO" in md


def test_symbol_access_entitlement_and_restriction() -> None:
    """Verify pilot symbols pass, while unauthorized tickers raise SymbolEntitlementError."""
    provider = MockLicensedDataProvider()
    gatekeeper = ReleaseOneLicensedDataGatekeeper(provider=provider)

    # Valid whitelisted symbols
    for sym in ["THYAO", "ASELS", "GARAN", "thyao"]:
        gatekeeper.validate_symbol_access(sym)
        bars = gatekeeper.fetch_pilot_bars(sym)
        assert len(bars) == 1
        assert bars[0].symbol.upper() == sym.upper()

    # Unauthorized non-pilot symbols
    for unauthorized in ["AAPL", "TSLA", "BTCUSDT", "XYZCORP"]:
        with pytest.raises(SymbolEntitlementError, match="sınırlı veri kapsamına dahil değildir"):
            gatekeeper.validate_symbol_access(unauthorized)

        with pytest.raises(SymbolEntitlementError):
            gatekeeper.fetch_pilot_bars(unauthorized)


def test_quota_exceeded_check_failure() -> None:
    """Verify CHECK-R1-02 fails if allowed symbol count exceeds MAX_MVP_SYMBOL_LIMIT."""
    provider = MockLicensedDataProvider()
    excessive_symbols = frozenset({f"SYM{i}" for i in range(MAX_MVP_SYMBOL_LIMIT + 5)})

    gatekeeper = ReleaseOneLicensedDataGatekeeper(
        provider=provider,
        allowed_symbols=excessive_symbols,
        max_symbols_limit=MAX_MVP_SYMBOL_LIMIT,
    )

    report = gatekeeper.evaluate()
    assert report.status == ReleaseOneGateStatus.FAIL
    check_r1_02 = next(r for r in report.results if r.check_id == "CHECK-R1-02")
    assert check_r1_02.passed is False
    assert "Sembol kotası kural ihlali" in check_r1_02.evidence


def test_provider_health_check_failure() -> None:
    """Verify CHECK-R1-06 fails if provider health check returns unhealthy."""
    provider = MockLicensedDataProvider(healthy=False)
    gatekeeper = ReleaseOneLicensedDataGatekeeper(provider=provider)

    report = gatekeeper.evaluate()
    assert report.status == ReleaseOneGateStatus.FAIL
    check_r1_06 = next(r for r in report.results if r.check_id == "CHECK-R1-06")
    assert check_r1_06.passed is False
    assert "Sağlayıcı sağlıksız" in check_r1_06.evidence
