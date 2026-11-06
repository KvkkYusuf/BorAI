"""Market data freshness, delay categorization, and UI/API provenance presentation.

Ensures:
- Timestamps stored in UTC are cleanly localized to Europe/Istanbul (TSI) for user display.
- Delay levels (EOD official, 15-minute delayed, realtime, synthetic) are explicitly categorized.
- Staleness is deterministically evaluated against the exchange trading calendar and session hours.
- Legal/compliance provenance notices accompany every display state.
"""

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.calendar import BaseExchangeCalendar, BISTCalendar
from stock_analysis.pipeline.storage import DerivedBarRecord
from stock_analysis.providers.base import MarketBarRecord

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
# BIST EOD bar publication threshold: typically 18:40 TRT (UTC+3)
BIST_EOD_RELEASE_TIME = time(18, 40)


class DelayCategory(StrEnum):
    """Categorization of market data feed delay."""

    EOD_OFFICIAL = "eod_official"  # End-of-Day consolidated closing bar
    DELAYED_15MIN = "delayed_15min"  # 15-minute delayed feed
    REALTIME = "realtime"  # Realtime live authorized feed
    SYNTHETIC = "synthetic"  # Synthetic test/mock feed


class DataFreshnessInfo(BaseModel):
    """Structured data freshness and delay metadata for UI presentation and API responses."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Ticker symbol (e.g. THYAO)")
    trade_date: date = Field(description="Trade session date of the record")
    last_updated_utc: datetime = Field(description="Ingestion/update timestamp in UTC")
    last_updated_trt: datetime = Field(description="Ingestion/update timestamp in Europe/Istanbul")
    delay_category: DelayCategory = Field(description="Data delay category")
    delay_minutes: int = Field(ge=0, description="Nominal feed delay in minutes")
    is_market_open: bool = Field(description="Whether the exchange is actively in session")
    is_stale: bool = Field(description="True if the data is older than expected latest session")
    stale_reason: str | None = Field(default=None, description="Explanation if data is stale")
    display_timestamp: str = Field(description="Human-readable timestamp in Turkish local time")
    display_badge: str = Field(
        description="Compact badge label for UI headers (e.g. 'GÜN SONU (EOD)')"
    )
    freshness_label: str = Field(description="Freshness status label (e.g. 'Güncel', 'Eski Veri')")
    provenance_notice: str = Field(description="Mandatory compliance and source attribution notice")


def format_timestamp_trt(dt_utc: datetime) -> str:
    """Format a UTC datetime into an Europe/Istanbul readable string."""
    if dt_utc.tzinfo is None:
        dt_aware = dt_utc.replace(tzinfo=UTC)
    else:
        dt_aware = dt_utc.astimezone(UTC)
    dt_trt = dt_aware.astimezone(ISTANBUL_TZ)
    return f"{dt_trt.strftime('%d.%m.%Y %H:%M:%S')} TSI"


def get_latest_expected_trading_date(
    calendar: BaseExchangeCalendar,
    as_of_utc: datetime | None = None,
    eod_release_time: time = BIST_EOD_RELEASE_TIME,
) -> date:
    """Find the most recent trading date whose EOD bar should already be published.

    If today is an open trading day and current time is past EOD release threshold,
    today is expected. Otherwise, step back to the most recent completed trading day.
    """
    now = as_of_utc or datetime.now(UTC)
    local_now = now.astimezone(calendar.timezone)
    current_date = local_now.date()

    if calendar.is_trading_day(current_date):
        # If current local time is at or after release time, today's EOD is expected
        if local_now.time() >= eod_release_time:
            return current_date

    # Otherwise look back to the latest completed trading day
    candidate = current_date
    for _ in range(15):  # Look back up to 15 days (covers long holiday breaks)
        candidate = date.fromordinal(candidate.toordinal() - 1)
        if calendar.is_trading_day(candidate):
            return candidate

    return current_date


def compute_data_freshness(
    record: MarketBarRecord | DerivedBarRecord,
    *,
    calendar: BaseExchangeCalendar | None = None,
    as_of_utc: datetime | None = None,
    override_delay_category: DelayCategory | None = None,
) -> DataFreshnessInfo:
    """Compute structured freshness and delay presentation info for a market data bar.

    Args:
        record: MarketBarRecord or DerivedBarRecord.
        calendar: Exchange calendar (defaults to BISTCalendar).
        as_of_utc: Evaluation timestamp in UTC (defaults to current UTC time).
        override_delay_category: Explicit category override if feed type is known.

    Returns:
        Populated DataFreshnessInfo model.
    """
    cal = calendar or BISTCalendar()
    now_utc = as_of_utc or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    # Ingested/derived timestamp handling
    if isinstance(record, DerivedBarRecord):
        updated_utc = record.derived_at
    else:
        updated_utc = record.ingested_at

    if updated_utc.tzinfo is None:
        updated_utc = updated_utc.replace(tzinfo=UTC)
    last_updated_trt = updated_utc.astimezone(cal.timezone)

    # Determine delay category
    if override_delay_category is not None:
        delay_cat = override_delay_category
    elif "synthetic" in record.source_name.lower() or "mock" in record.source_name.lower():
        delay_cat = DelayCategory.SYNTHETIC
    else:
        delay_cat = DelayCategory.EOD_OFFICIAL

    delay_minutes = 15 if delay_cat == DelayCategory.DELAYED_15MIN else 0

    # Market session state
    is_open = cal.is_session_open_at(now_utc)

    # Staleness check
    expected_latest_date = get_latest_expected_trading_date(cal, as_of_utc=now_utc)
    is_stale = record.trade_date < expected_latest_date
    stale_reason = (
        f"En güncel seans barı ({expected_latest_date.isoformat()}) henüz hatta yansımadı."
        if is_stale
        else None
    )

    # Badges and UI text
    if delay_cat == DelayCategory.SYNTHETIC:
        badge = "SENTETİK VERİ"
        fresh_label = "Simülasyon"
        notice = "Veri simülasyon amaçlı sentetik üretilmiştir; resmi işlem verisi değildir."
    elif delay_cat == DelayCategory.DELAYED_15MIN:
        badge = "15 DK GECİKMELİ"
        fresh_label = "Eski Veri" if is_stale else "Gecikmeli Yayın"
        notice = "Piyasa verileri BIST kuralları gereği 15 dakika gecikmelidir."
    elif delay_cat == DelayCategory.REALTIME:
        badge = "CANLI (GERÇEK ZAMANLI)"
        fresh_label = "Eski Veri" if is_stale else "Canlı"
        notice = "Lisanslı gerçek zamanlı veri akışı."
    else:  # EOD_OFFICIAL
        badge = "GÜN SONU (EOD)"
        fresh_label = "Eski Veri" if is_stale else "Güncel (EOD)"
        notice = "Resmi BIST gün sonu kapanış verisidir; bilgilendirme amaçlıdır."

    display_ts = format_timestamp_trt(updated_utc)

    return DataFreshnessInfo(
        symbol=record.symbol.upper(),
        trade_date=record.trade_date,
        last_updated_utc=updated_utc,
        last_updated_trt=last_updated_trt,
        delay_category=delay_cat,
        delay_minutes=delay_minutes,
        is_market_open=is_open,
        is_stale=is_stale,
        stale_reason=stale_reason,
        display_timestamp=display_ts,
        display_badge=badge,
        freshness_label=fresh_label,
        provenance_notice=notice,
    )


class StockDetailAttribution(BaseModel):
    """Structured market data source attribution and delay visibility for stock detail views."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Ticker symbol in uppercase (e.g. THYAO)")
    source_name: str = Field(description="Technical or provider source identifier")
    source_display_name: str = Field(
        description="User-facing provider name (e.g. 'Borsa İstanbul A.Ş. (BIST EOD)')"
    )
    delay_category: DelayCategory = Field(description="Data delay category")
    delay_label: str = Field(description="Human-readable delay label (e.g. 'Gün Sonu (EOD)')")
    delay_minutes: int = Field(ge=0, description="Nominal feed delay in minutes")
    last_updated_trt_str: str = Field(
        description="Localized last update timestamp in TSI (e.g. '07.01.2026 18:40:00 TSI')"
    )
    display_badge: str = Field(description="Compact badge label for UI headers")
    is_stale: bool = Field(description="Whether data is older than expected latest session")
    stale_reason: str | None = Field(default=None, description="Reason if data is stale")
    disclaimer_text: str = Field(
        description="Mandatory SPK legal disclaimer with source and delay attribution"
    )


class StockDetailView(BaseModel):
    """Unified view model representing a stock detail page header, freshness, and attribution."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Ticker symbol in uppercase")
    attribution: StockDetailAttribution = Field(
        description="Data provider attribution and delay metadata"
    )
    freshness: DataFreshnessInfo = Field(
        description="Detailed freshness assessment and exchange status"
    )
    latest_bar: DerivedBarRecord | MarketBarRecord | None = Field(
        default=None, description="Latest market bar record associated with this view"
    )

    def render_header_badge(self) -> str:
        """Render compact badge text for UI stock detail headers."""
        return self.attribution.display_badge

    def render_disclaimer(self) -> str:
        """Render legal disclaimer notice to be placed under charts and tables."""
        return self.attribution.disclaimer_text

    def to_display_dict(self) -> dict[str, Any]:
        """Produce dictionary optimized for API serialization and frontend rendering."""
        return {
            "symbol": self.symbol,
            "badge": self.attribution.display_badge,
            "source": self.attribution.source_display_name,
            "delay": self.attribution.delay_label,
            "delay_minutes": self.attribution.delay_minutes,
            "last_updated_tsi": self.attribution.last_updated_trt_str,
            "is_stale": self.attribution.is_stale,
            "stale_reason": self.attribution.stale_reason,
            "disclaimer": self.attribution.disclaimer_text,
        }


def _resolve_source_display_name(source_name: str, custom_name: str | None = None) -> str:
    """Format technical source name into human-readable provider name."""
    if custom_name is not None and custom_name.strip():
        return custom_name.strip()

    name_lower = source_name.lower()
    if "synthetic" in name_lower or "mock" in name_lower:
        return "Sentetik Simülasyon Motoru"
    if "bist" in name_lower or "licensed" in name_lower:
        return "BIST / Lisanslı Veri Sağlayıcı"
    return source_name


def _format_delay_text_for_disclaimer(category: DelayCategory) -> str:
    """Generate localized delay description for legal disclaimer."""
    if category == DelayCategory.EOD_OFFICIAL:
        return "gün sonu kapanış (EOD)"
    if category == DelayCategory.DELAYED_15MIN:
        return "15 dakika"
    if category == DelayCategory.REALTIME:
        return "0 dakika (canlı / gerçek zamanlı)"
    if category == DelayCategory.SYNTHETIC:
        return "simülasyon (sentetik veri)"
    return "belirtilmemiş"


def build_stock_detail_attribution(
    record: MarketBarRecord | DerivedBarRecord,
    *,
    calendar: BaseExchangeCalendar | None = None,
    as_of_utc: datetime | None = None,
    override_delay_category: DelayCategory | None = None,
    custom_source_display_name: str | None = None,
) -> StockDetailAttribution:
    """Build a StockDetailAttribution instance for displaying source and delay in stock details.

    Args:
        record: MarketBarRecord or DerivedBarRecord.
        calendar: Exchange calendar (defaults to BISTCalendar).
        as_of_utc: Evaluation timestamp in UTC.
        override_delay_category: Explicit category override if feed type is known.
        custom_source_display_name: Optional override for provider display name.

    Returns:
        Populated StockDetailAttribution model with compliant SPK disclaimer.
    """
    freshness = compute_data_freshness(
        record,
        calendar=calendar,
        as_of_utc=as_of_utc,
        override_delay_category=override_delay_category,
    )

    source_display = _resolve_source_display_name(
        record.source_name, custom_name=custom_source_display_name
    )

    if freshness.delay_category == DelayCategory.EOD_OFFICIAL:
        delay_label = "Gün Sonu (EOD)"
    elif freshness.delay_category == DelayCategory.DELAYED_15MIN:
        delay_label = "15 Dakika Gecikmeli"
    elif freshness.delay_category == DelayCategory.REALTIME:
        delay_label = "Canlı (Gerçek Zamanlı)"
    else:
        delay_label = "Sentetik Simülasyon"

    delay_disclaimer_str = _format_delay_text_for_disclaimer(freshness.delay_category)
    disclaimer = (
        f"Bu sayfadaki bilgiler genel bilgilendirme amaçlıdır; yatırım danışmanlığı veya al-sat "
        f"tavsiyesi değildir. Veriler {source_display} kaynaklıdır, "
        f"{delay_disclaimer_str} gecikmelidir. "
        f"Geçmiş performans gelecekteki sonuçların göstergesi değildir."
    )

    return StockDetailAttribution(
        symbol=freshness.symbol,
        source_name=record.source_name,
        source_display_name=source_display,
        delay_category=freshness.delay_category,
        delay_label=delay_label,
        delay_minutes=freshness.delay_minutes,
        last_updated_trt_str=freshness.display_timestamp,
        display_badge=freshness.display_badge,
        is_stale=freshness.is_stale,
        stale_reason=freshness.stale_reason,
        disclaimer_text=disclaimer,
    )


def build_stock_detail_view(
    record: MarketBarRecord | DerivedBarRecord,
    *,
    calendar: BaseExchangeCalendar | None = None,
    as_of_utc: datetime | None = None,
    override_delay_category: DelayCategory | None = None,
    custom_source_display_name: str | None = None,
) -> StockDetailView:
    """Build a unified StockDetailView encompassing header attribution and data freshness.

    Args:
        record: MarketBarRecord or DerivedBarRecord.
        calendar: Exchange calendar (defaults to BISTCalendar).
        as_of_utc: Evaluation timestamp in UTC.
        override_delay_category: Explicit category override if feed type is known.
        custom_source_display_name: Optional override for provider display name.

    Returns:
        Populated StockDetailView.
    """
    freshness = compute_data_freshness(
        record,
        calendar=calendar,
        as_of_utc=as_of_utc,
        override_delay_category=override_delay_category,
    )
    attribution = build_stock_detail_attribution(
        record,
        calendar=calendar,
        as_of_utc=as_of_utc,
        override_delay_category=override_delay_category,
        custom_source_display_name=custom_source_display_name,
    )

    return StockDetailView(
        symbol=record.symbol.upper(),
        attribution=attribution,
        freshness=freshness,
        latest_bar=record,
    )
