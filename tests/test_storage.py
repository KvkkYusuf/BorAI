"""Tests for immutable raw market data storage and derived dataset pipeline."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from stock_analysis.pipeline.corporate_actions import CorporateAction, CorporateActionType
from stock_analysis.pipeline.storage import (
    DerivedMarketDataStore,
    DerivedMarketSeries,
    ImmutableDataError,
    ImmutableRawStore,
    IngestionResult,
    MarketDataPipelineService,
    RawBarRecord,
)
from stock_analysis.providers.base import MarketBarRecord


def _make_bar(
    trade_date: date,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    symbol: str = "THYAO",
    volume: int = 1000,
    turnover: str = "50000.00",
) -> MarketBarRecord:
    """Helper to construct a standard MarketBarRecord."""
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        raw_open=Decimal(open_p),
        raw_high=Decimal(high_p),
        raw_low=Decimal(low_p),
        raw_close=Decimal(close_p),
        adj_open=Decimal(open_p),
        adj_high=Decimal(high_p),
        adj_low=Decimal(low_p),
        adj_close=Decimal(close_p),
        volume=volume,
        turnover_try=Decimal(turnover),
        source_name="test_feed",
    )


class TestRawBarRecord:
    """Tests for RawBarRecord model immutability and hashing."""

    def test_raw_bar_immutability(self) -> None:
        bar = _make_bar(date(2026, 1, 5), "100.00", "105.00", "98.00", "102.00")
        raw_rec = RawBarRecord.from_market_bar(bar)

        assert raw_rec.symbol == "THYAO"
        assert raw_rec.raw_close == Decimal("102.00")
        assert len(raw_rec.raw_payload_hash) == 64

        # Mutation must be blocked (frozen Pydantic model)
        with pytest.raises(ValidationError):
            raw_rec.raw_close = Decimal("103.00")

    def test_hash_determinism_and_sensitivity(self) -> None:
        bar1 = _make_bar(date(2026, 1, 5), "100.00", "105.00", "98.00", "102.00")
        bar2 = _make_bar(date(2026, 1, 5), "100.00", "105.00", "98.00", "102.00")
        bar3 = _make_bar(date(2026, 1, 5), "100.00", "105.00", "98.00", "102.01")

        rec1 = RawBarRecord.from_market_bar(bar1)
        rec2 = RawBarRecord.from_market_bar(bar2)
        rec3 = RawBarRecord.from_market_bar(bar3)

        assert rec1.raw_payload_hash == rec2.raw_payload_hash
        assert rec1.raw_payload_hash != rec3.raw_payload_hash


class TestImmutableRawStore:
    """Tests for append-only raw store immutability constraints."""

    def test_append_and_idempotence(self) -> None:
        store = ImmutableRawStore()
        bar = _make_bar(date(2026, 1, 5), "100.00", "105.00", "98.00", "102.00")
        rec = RawBarRecord.from_market_bar(bar)

        # Initial insert succeeds
        assert store.append(rec) is True
        assert store.count("THYAO") == 1

        # Identical insert is an idempotent no-op (returns False)
        assert store.append(rec) is False
        assert store.count("THYAO") == 1

    def test_overwrite_mutation_prevention(self) -> None:
        store = ImmutableRawStore()
        d = date(2026, 1, 5)
        bar1 = _make_bar(d, "100.00", "105.00", "98.00", "102.00")
        bar2 = _make_bar(d, "100.00", "105.00", "98.00", "103.00")  # Tampered close price

        rec1 = RawBarRecord.from_market_bar(bar1)
        rec2 = RawBarRecord.from_market_bar(bar2)

        store.append(rec1)

        # Mutating existing date with different data must raise ImmutableDataError
        with pytest.raises(ImmutableDataError) as exc_info:
            store.append(rec2)

        assert "Cannot overwrite immutable raw bar for THYAO" in str(exc_info.value)
        # Verify original bar remained completely intact
        bars = store.get_bars("THYAO")
        assert len(bars) == 1
        assert bars[0].raw_close == Decimal("102.00")

    def test_query_filtering(self) -> None:
        store = ImmutableRawStore()
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        d3 = date(2026, 1, 7)

        store.append(RawBarRecord.from_market_bar(_make_bar(d1, "100", "105", "98", "102")))
        store.append(RawBarRecord.from_market_bar(_make_bar(d2, "102", "106", "101", "104")))
        store.append(RawBarRecord.from_market_bar(_make_bar(d3, "104", "108", "103", "107")))
        kchol_bar = _make_bar(d1, "50", "52", "49", "51", symbol="KCHOL")
        store.append(RawBarRecord.from_market_bar(kchol_bar))

        assert store.get_symbols() == ["KCHOL", "THYAO"]
        assert store.count() == 4

        # Range query
        mid = store.get_bars("THYAO", start_date=d2, end_date=d2)
        assert len(mid) == 1
        assert mid[0].trade_date == d2


class TestMarketDataPipelineService:
    """Tests for pipeline coordination, validation, and corporate action derivation."""

    def test_derive_series_with_split_action(self) -> None:
        raw_store = ImmutableRawStore()
        derived_store = DerivedMarketDataStore()
        service = MarketDataPipelineService(raw_store=raw_store, derived_store=derived_store)

        # Monday 2026-01-05 and Tuesday 2026-01-06
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        b1 = _make_bar(d1, "100.00", "110.00", "95.00", "100.00")
        b2 = _make_bar(d2, "50.00", "55.00", "48.00", "52.00")

        # 1. Ingest raw bars
        count = service.ingest_raw_bars([b1, b2])
        assert count == 2
        assert raw_store.count("THYAO") == 2

        # 2. Define a 2:1 stock split effective on 2026-01-06
        split = CorporateAction(
            symbol="THYAO",
            ex_date=d2,
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
        )

        # 3. Derive series
        series = service.derive_series(
            symbol="THYAO",
            corporate_actions=[split],
        )

        assert isinstance(series, DerivedMarketSeries)
        assert series.symbol == "THYAO"
        assert len(series.bars) == 2
        assert series.integrity_report.is_valid is True

        # Check raw price preservation in raw store
        raw_b1 = raw_store.get_bars("THYAO", start_date=d1, end_date=d1)[0]
        assert raw_b1.raw_close == Decimal("100.00")

        # Check derived adjusted prices
        derived_b1 = series.bars[0]
        derived_b2 = series.bars[1]

        # Prior bar (b1) before 2:1 split: raw price is 100, adjusted price is 50
        assert derived_b1.raw_close == Decimal("100.00")
        assert derived_b1.adj_close == Decimal("50.0000")
        assert derived_b1.split_factor == Decimal("1.0")
        assert derived_b1.raw_record_hash == raw_b1.raw_payload_hash

        # Current bar (b2, ex_date): adjusted price equals raw price, split_factor is 2.0
        assert derived_b2.raw_close == Decimal("52.00")
        assert derived_b2.adj_close == Decimal("52.00")
        assert derived_b2.split_factor == Decimal("2.0")

        # Check retrieval from derived store
        retrieved = derived_store.get("THYAO")
        assert retrieved is not None
        assert retrieved.derivation_version == series.derivation_version

    def test_derivation_version_determinism(self) -> None:
        service = MarketDataPipelineService()
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        service.ingest_raw_bars(
            [
                _make_bar(d1, "100.00", "110.00", "95.00", "100.00"),
                _make_bar(d2, "102.00", "108.00", "101.00", "105.00"),
            ]
        )

        split = CorporateAction(
            symbol="THYAO",
            ex_date=d2,
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
        )

        series1 = service.derive_series("THYAO", [split])
        series2 = service.derive_series("THYAO", [split])

        # Version must be completely deterministic
        assert series1.derivation_version == series2.derivation_version

        # Changing action must change version
        dividend = CorporateAction(
            symbol="THYAO",
            ex_date=d2,
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("2.50"),
        )
        series3 = service.derive_series("THYAO", [dividend])
        assert series3.derivation_version != series1.derivation_version

    def test_idempotent_ingestion_same_day(self) -> None:
        """Running ingestion multiple times on the same day produces no duplicate records."""
        service = MarketDataPipelineService()
        d = date(2026, 1, 5)
        bar = _make_bar(d, "100.00", "105.00", "98.00", "102.00")

        # First ingestion run
        res1 = service.ingest_raw_bars([bar])
        assert isinstance(res1, IngestionResult)
        assert res1.total_received == 1
        assert res1.inserted_count == 1
        assert res1.duplicate_count == 0
        assert res1.is_fully_inserted is True
        assert res1.is_idempotent_noop is False
        assert int(res1) == 1
        assert res1 == 1
        assert service.raw_store.count("THYAO") == 1

        # Second identical ingestion run (same day re-run)
        res2 = service.ingest_raw_bars([bar])
        assert res2.total_received == 1
        assert res2.inserted_count == 0
        assert res2.duplicate_count == 1
        assert res2.is_fully_inserted is False
        assert res2.is_idempotent_noop is True
        assert int(res2) == 0
        assert res2 == 0
        # Store count must strictly remain 1 (no double-counting)
        assert service.raw_store.count("THYAO") == 1
        assert len(service.raw_store.get_bars("THYAO")) == 1

    def test_intra_batch_duplicate_handling(self) -> None:
        """Batch containing duplicate records for same symbol and date is handled idempotently."""
        service = MarketDataPipelineService()
        d = date(2026, 1, 5)
        bar = _make_bar(d, "100.00", "105.00", "98.00", "102.00")

        # Batch with duplicate identical bar
        res = service.ingest_raw_bars([bar, bar])
        assert res.total_received == 2
        assert res.inserted_count == 1
        assert res.duplicate_count == 1
        assert service.raw_store.count("THYAO") == 1
