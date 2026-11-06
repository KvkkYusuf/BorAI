"""Immutable raw market data storage and derived series generation.

Ensures that ingested raw EOD data is preserved immutably (append-only, write-once,
tamper-evident via SHA-256 hashes) while cleaned, validated, and corporate-action-adjusted
market data is derived into a separate, reproducible layer.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_analysis.pipeline.calendar import BISTCalendar
from stock_analysis.pipeline.corporate_actions import CorporateAction, apply_corporate_actions
from stock_analysis.pipeline.validator import DataIntegrityReport, validate_market_data_series
from stock_analysis.providers.base import DataStatus, MarketBarRecord


class ImmutableDataError(Exception):
    """Raised when an attempt is made to overwrite, mutate or delete immutable raw data."""


class IngestionResult(BaseModel):
    """Summary of an idempotent raw market data ingestion run."""

    model_config = ConfigDict(frozen=True)

    total_received: int = Field(
        ge=0, description="Total number of bar records received in the batch"
    )
    inserted_count: int = Field(ge=0, description="Number of newly inserted unique raw records")
    duplicate_count: int = Field(ge=0, description="Number of duplicate/identical records skipped")
    symbols_affected: tuple[str, ...] = Field(
        default_factory=tuple, description="Distinct symbols touched by this ingestion run"
    )

    def __int__(self) -> int:
        """Allow backwards-compatible casting or integer equality against inserted_count."""
        return self.inserted_count

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.inserted_count == other
        return super().__eq__(other)

    @property
    def is_fully_inserted(self) -> bool:
        """True if all submitted records were newly inserted."""
        return self.inserted_count == self.total_received

    @property
    def is_idempotent_noop(self) -> bool:
        """True if all submitted records were duplicates and safely skipped with 0 new inserts."""
        return self.total_received > 0 and self.duplicate_count == self.total_received


class RawBarRecord(BaseModel):
    """Immutable representation of an untouched market bar directly from the data source."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1, max_length=10, description="BIST ticker symbol (e.g., THYAO)")
    trade_date: date = Field(description="Trading session date")
    raw_open: Decimal = Field(gt=Decimal("0"))
    raw_high: Decimal = Field(gt=Decimal("0"))
    raw_low: Decimal = Field(gt=Decimal("0"))
    raw_close: Decimal = Field(gt=Decimal("0"))
    volume: int = Field(ge=0, description="Trading volume in shares/lots")
    turnover_try: Decimal = Field(ge=Decimal("0"), description="Total turnover amount in TRY")
    source_name: str = Field(description="Identifier of the origin data provider")
    raw_payload_hash: str = Field(
        description="SHA-256 checksum of raw bar fields for integrity verification"
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when record was first ingested",
    )

    @classmethod
    def compute_hash(
        cls,
        symbol: str,
        trade_date: date,
        raw_open: Decimal,
        raw_high: Decimal,
        raw_low: Decimal,
        raw_close: Decimal,
        volume: int,
        turnover_try: Decimal,
        source_name: str,
    ) -> str:
        """Compute deterministic SHA-256 checksum over the raw bar attributes."""
        payload = (
            f"{symbol.upper()}|{trade_date.isoformat()}|{raw_open}|{raw_high}|"
            f"{raw_low}|{raw_close}|{volume}|{turnover_try}|{source_name}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_market_bar(cls, bar: MarketBarRecord) -> "RawBarRecord":
        """Construct an immutable RawBarRecord from a provider MarketBarRecord."""
        raw_hash = cls.compute_hash(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            raw_open=bar.raw_open,
            raw_high=bar.raw_high,
            raw_low=bar.raw_low,
            raw_close=bar.raw_close,
            volume=bar.volume,
            turnover_try=bar.turnover_try,
            source_name=bar.source_name,
        )
        return cls(
            symbol=bar.symbol.upper(),
            trade_date=bar.trade_date,
            raw_open=bar.raw_open,
            raw_high=bar.raw_high,
            raw_low=bar.raw_low,
            raw_close=bar.raw_close,
            volume=bar.volume,
            turnover_try=bar.turnover_try,
            source_name=bar.source_name,
            raw_payload_hash=raw_hash,
            ingested_at=bar.ingested_at,
        )

    def to_market_bar(self) -> MarketBarRecord:
        """Convert back to a baseline MarketBarRecord before corporate action adjustments."""
        return MarketBarRecord(
            symbol=self.symbol,
            trade_date=self.trade_date,
            raw_open=self.raw_open,
            raw_high=self.raw_high,
            raw_low=self.raw_low,
            raw_close=self.raw_close,
            adj_open=self.raw_open,
            adj_high=self.raw_high,
            adj_low=self.raw_low,
            adj_close=self.raw_close,
            volume=self.volume,
            turnover_try=self.turnover_try,
            source_name=self.source_name,
            data_status=DataStatus.PROVISIONAL,
            is_verified=False,
            ingested_at=self.ingested_at,
        )


class DerivedBarRecord(BaseModel):
    """Validated, cleansed, and corporate-action-adjusted bar record."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1, max_length=10)
    trade_date: date = Field(description="Trading session date")

    # Raw prices (immutable historical record)
    raw_open: Decimal = Field(gt=Decimal("0"))
    raw_high: Decimal = Field(gt=Decimal("0"))
    raw_low: Decimal = Field(gt=Decimal("0"))
    raw_close: Decimal = Field(gt=Decimal("0"))

    # Adjusted prices (derived via corporate actions)
    adj_open: Decimal = Field(gt=Decimal("0"))
    adj_high: Decimal = Field(gt=Decimal("0"))
    adj_low: Decimal = Field(gt=Decimal("0"))
    adj_close: Decimal = Field(gt=Decimal("0"))

    volume: int = Field(ge=0)
    turnover_try: Decimal = Field(ge=Decimal("0"))

    split_factor: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    dividend_try: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"))

    data_status: DataStatus = Field(default=DataStatus.VERIFIED)
    is_verified: bool = Field(default=True)
    source_name: str = Field()
    raw_record_hash: str = Field(
        description="Provenance checksum linking to the immutable raw record"
    )
    derived_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when this record was derived",
    )

    @classmethod
    def from_adjusted_bar(
        cls,
        bar: MarketBarRecord,
        raw_hash: str,
        derived_at: datetime | None = None,
    ) -> "DerivedBarRecord":
        """Create a DerivedBarRecord from an adjusted MarketBarRecord."""
        return cls(
            symbol=bar.symbol.upper(),
            trade_date=bar.trade_date,
            raw_open=bar.raw_open,
            raw_high=bar.raw_high,
            raw_low=bar.raw_low,
            raw_close=bar.raw_close,
            adj_open=bar.adj_open,
            adj_high=bar.adj_high,
            adj_low=bar.adj_low,
            adj_close=bar.adj_close,
            volume=bar.volume,
            turnover_try=bar.turnover_try,
            split_factor=bar.split_factor,
            dividend_try=bar.dividend_try,
            data_status=bar.data_status,
            is_verified=bar.is_verified,
            source_name=bar.source_name,
            raw_record_hash=raw_hash,
            derived_at=derived_at or datetime.now(UTC),
        )

    def to_market_bar(self) -> MarketBarRecord:
        """Convert derived bar to the standard MarketBarRecord schema."""
        return MarketBarRecord(
            symbol=self.symbol,
            trade_date=self.trade_date,
            raw_open=self.raw_open,
            raw_high=self.raw_high,
            raw_low=self.raw_low,
            raw_close=self.raw_close,
            adj_open=self.adj_open,
            adj_high=self.adj_high,
            adj_low=self.adj_low,
            adj_close=self.adj_close,
            volume=self.volume,
            turnover_try=self.turnover_try,
            split_factor=self.split_factor,
            dividend_try=self.dividend_try,
            data_status=self.data_status,
            is_verified=self.is_verified,
            source_name=self.source_name,
        )


class DerivedMarketSeries(BaseModel):
    """Derived dataset for a symbol containing cleansed, adjusted bars and audit metadata."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    derivation_version: str = Field(
        description="Deterministic hash of raw data + corporate actions applied"
    )
    bars: tuple[DerivedBarRecord, ...]
    integrity_report: DataIntegrityReport
    applied_actions: tuple[CorporateAction, ...]
    derived_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_series_consistency(self) -> Self:
        """Ensure bars belong to the declared symbol and are chronologically sorted."""
        for b in self.bars:
            if b.symbol != self.symbol:
                raise ValueError(
                    f"Bar symbol '{b.symbol}' does not match series symbol '{self.symbol}'"
                )
        dates = [b.trade_date for b in self.bars]
        if dates != sorted(dates):
            raise ValueError("Derived bars must be strictly ordered ascending by trade_date")
        return self


class ImmutableRawStore:
    """In-memory append-only immutable store for raw market bars.

    Guarantees:
    - Write-once: Cannot overwrite an existing record with different values.
    - Idempotent: Writing identical data for the same symbol and date is a safe no-op.
    - Read-only queries: Returns immutable tuples to prevent external mutation.
    """

    def __init__(self) -> None:
        # Internal structure: symbol -> dict[trade_date, RawBarRecord]
        self._store: dict[str, dict[date, RawBarRecord]] = {}

    def append(self, record: RawBarRecord) -> bool:
        """Append a raw bar record immutably.

        Returns:
            True if a new record was added, False if identical record already existed (idempotent).

        Raises:
            ImmutableDataError: If a different record for the same symbol and date already exists.
        """
        symbol = record.symbol.upper()
        symbol_records = self._store.setdefault(symbol, {})

        if record.trade_date in symbol_records:
            existing = symbol_records[record.trade_date]
            if existing.raw_payload_hash == record.raw_payload_hash:
                # Idempotent write: exactly identical raw data
                return False
            raise ImmutableDataError(
                f"Cannot overwrite immutable raw bar for {symbol} on {record.trade_date}. "
                f"Existing: {existing.raw_payload_hash[:8]}, new: {record.raw_payload_hash[:8]}."
            )

        symbol_records[record.trade_date] = record
        return True

    def append_batch_detailed(self, records: Sequence[RawBarRecord]) -> tuple[int, int]:
        """Append multiple raw bar records with duplicate tracking.

        Returns:
            Tuple of (newly_inserted_count, duplicate_skipped_count).
        """
        inserted_count = 0
        duplicate_count = 0
        for rec in records:
            if self.append(rec):
                inserted_count += 1
            else:
                duplicate_count += 1
        return inserted_count, duplicate_count

    def append_batch(self, records: Sequence[RawBarRecord]) -> int:
        """Append multiple raw bar records.

        Returns:
            Number of newly inserted records.
        """
        inserted_count, _ = self.append_batch_detailed(records)
        return inserted_count

    def get_bars(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[RawBarRecord, ...]:
        """Fetch raw bar records for a symbol within date bounds, sorted ascending."""
        symbol_records = self._store.get(symbol.upper(), {})
        matched: list[RawBarRecord] = []
        for d, rec in symbol_records.items():
            if start_date is not None and d < start_date:
                continue
            if end_date is not None and d > end_date:
                continue
            matched.append(rec)

        matched.sort(key=lambda r: r.trade_date)
        return tuple(matched)

    def count(self, symbol: str | None = None) -> int:
        """Return total number of raw records in the store."""
        if symbol is not None:
            return len(self._store.get(symbol.upper(), {}))
        return sum(len(bars) for bars in self._store.values())

    def get_symbols(self) -> list[str]:
        """Return list of distinct symbols present in raw store."""
        return sorted(self._store.keys())


class DerivedMarketDataStore:
    """Store for derived (cleansed, validated, adjusted) market series.

    Maintains derived views separate from raw data, keyed by (symbol, derivation_version).
    """

    def __init__(self) -> None:
        # (symbol, derivation_version) -> DerivedMarketSeries
        self._series_by_version: dict[tuple[str, str], DerivedMarketSeries] = {}
        # Latest version for symbol
        self._latest_version: dict[str, str] = {}

    def put(self, series: DerivedMarketSeries) -> None:
        """Store a derived series."""
        key = (series.symbol.upper(), series.derivation_version)
        self._series_by_version[key] = series
        self._latest_version[series.symbol.upper()] = series.derivation_version

    def get(self, symbol: str, version: str | None = None) -> DerivedMarketSeries | None:
        """Retrieve a derived series by symbol and optional version (defaults to latest)."""
        sym = symbol.upper()
        target_version = version or self._latest_version.get(sym)
        if target_version is None:
            return None
        return self._series_by_version.get((sym, target_version))


class MarketDataPipelineService:
    """Service managing raw ingestion, immutable storage, and derived dataset derivation."""

    def __init__(
        self,
        raw_store: ImmutableRawStore | None = None,
        derived_store: DerivedMarketDataStore | None = None,
        calendar: BISTCalendar | None = None,
    ) -> None:
        self.raw_store = raw_store or ImmutableRawStore()
        self.derived_store = derived_store or DerivedMarketDataStore()
        self.calendar = calendar or BISTCalendar()

    def ingest_raw_bars(self, bars: Sequence[MarketBarRecord]) -> IngestionResult:
        """Ingest raw bars into the immutable raw store idempotently.

        Guarantees:
        - Re-running ingestion for the same trading day / symbol produces no duplicate records.
        - Exactly identical bars are safely treated as idempotent no-ops.
        - Returns a structured IngestionResult detailing inserted vs duplicate-skipped records.
        """
        raw_records = [RawBarRecord.from_market_bar(bar) for bar in bars]
        inserted, duplicates = self.raw_store.append_batch_detailed(raw_records)
        symbols = tuple(sorted({b.symbol.upper() for b in bars}))
        return IngestionResult(
            total_received=len(bars),
            inserted_count=inserted,
            duplicate_count=duplicates,
            symbols_affected=symbols,
        )

    def derive_series(
        self,
        symbol: str,
        corporate_actions: Sequence[CorporateAction] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> DerivedMarketSeries:
        """Derive cleansed, validated, and adjusted market data series from immutable raw store.

        Steps:
        1. Query immutable raw bars.
        2. Convert raw bars into MarketBarRecord baseline.
        3. Validate series integrity (calendar, OHLC, gaps, volumes).
        4. Apply corporate action adjustments (splits, dividends, bonus/rights).
        5. Build deterministic derivation_version hash from raw hashes + action hashes.
        6. Package into DerivedMarketSeries and cache in DerivedMarketDataStore.
        """
        sym = symbol.upper()
        raw_bars = self.raw_store.get_bars(sym, start_date=start_date, end_date=end_date)
        if not raw_bars:
            raise ValueError(f"No raw data found for symbol '{sym}' in requested range")

        # 2. Baseline market bars
        baseline_bars = [r.to_market_bar() for r in raw_bars]

        # 3. Validate integrity
        integrity_report = validate_market_data_series(baseline_bars, calendar=self.calendar)

        # 4. Apply corporate actions
        actions = tuple(corporate_actions or ())
        adjusted_bars = apply_corporate_actions(baseline_bars, actions)

        # Map adjusted bars back to their respective raw hash
        raw_hash_map = {r.trade_date: r.raw_payload_hash for r in raw_bars}

        derived_at = datetime.now(UTC)
        derived_bar_records: list[DerivedBarRecord] = []
        for adj_bar in adjusted_bars:
            # Mark status as VERIFIED if valid, or FLAGGED if integrity report has severe issues
            status = DataStatus.VERIFIED if integrity_report.is_valid else DataStatus.FLAGGED
            adj_with_status = adj_bar.model_copy(
                update={
                    "data_status": status,
                    "is_verified": integrity_report.is_valid,
                }
            )
            r_hash = raw_hash_map[adj_bar.trade_date]
            derived_bar_records.append(
                DerivedBarRecord.from_adjusted_bar(
                    adj_with_status,
                    raw_hash=r_hash,
                    derived_at=derived_at,
                )
            )

        # 5. Compute derivation version hash
        derivation_version = self._compute_derivation_version(raw_bars, actions)

        derived_series = DerivedMarketSeries(
            symbol=sym,
            derivation_version=derivation_version,
            bars=tuple(derived_bar_records),
            integrity_report=integrity_report,
            applied_actions=actions,
            derived_at=derived_at,
        )

        # 6. Store derived series
        self.derived_store.put(derived_series)
        return derived_series

    @staticmethod
    def _compute_derivation_version(
        raw_bars: Sequence[RawBarRecord],
        actions: Sequence[CorporateAction],
    ) -> str:
        """Compute deterministic hash of the exact combination of raw data and actions."""
        hasher = hashlib.sha256()
        for b in raw_bars:
            hasher.update(b.raw_payload_hash.encode("utf-8"))
        for a in sorted(actions, key=lambda x: (x.ex_date, x.action_type)):
            action_sig = (
                f"{a.ex_date}|{a.action_type}|{a.ratio}|{a.amount_try}|{a.subscription_price_try}"
            )
            hasher.update(action_sig.encode("utf-8"))
        return hasher.hexdigest()
