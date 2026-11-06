"""Tests for market data freshness, delay categorization, and TSI localization."""

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from stock_analysis.pipeline.calendar import BISTCalendar
from stock_analysis.pipeline.freshness import (
    DataFreshnessInfo,
    DelayCategory,
    StockDetailAttribution,
    StockDetailView,
    build_stock_detail_attribution,
    build_stock_detail_view,
    compute_data_freshness,
    format_timestamp_trt,
    get_latest_expected_trading_date,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


def _make_sample_bar(
    trade_date: date,
    source_name: str = "licensed_bist_eod",
    ingested_at: datetime | None = None,
) -> MarketBarRecord:
    return MarketBarRecord(
        symbol="THYAO",
        trade_date=trade_date,
        data_status=DataStatus.VERIFIED,
        raw_open=Decimal("100.0"),
        raw_high=Decimal("105.0"),
        raw_low=Decimal("98.0"),
        raw_close=Decimal("102.0"),
        adj_open=Decimal("100.0"),
        adj_high=Decimal("105.0"),
        adj_low=Decimal("98.0"),
        adj_close=Decimal("102.0"),
        volume=1000,
        turnover_try=Decimal("102000.0"),
        source_name=source_name,
        ingested_at=ingested_at or datetime(2026, 1, 5, 15, 45, 0, tzinfo=UTC),
    )


def test_format_timestamp_trt() -> None:
    """Verify UTC datetime is accurately converted to Europe/Istanbul formatted text."""
    # 15:45 UTC is 18:45 in UTC+3 (TSI)
    dt_utc = datetime(2026, 1, 5, 15, 45, 30, tzinfo=UTC)
    formatted = format_timestamp_trt(dt_utc)
    assert formatted == "05.01.2026 18:45:30 TSI"


def test_get_latest_expected_trading_date_before_and_after_release() -> None:
    """Verify threshold logic: before 18:40 TRT expects previous day, after expects today."""
    calendar = BISTCalendar()

    # Wednesday 2026-01-07 14:00 TRT (11:00 UTC) -> Session ongoing, EOD not yet published
    as_of_midday = datetime(2026, 1, 7, 11, 0, 0, tzinfo=UTC)
    expected_midday = get_latest_expected_trading_date(calendar, as_of_utc=as_of_midday)
    # Expected latest published EOD is Tuesday 2026-01-06
    assert expected_midday == date(2026, 1, 6)

    # Wednesday 2026-01-07 19:00 TRT (16:00 UTC) -> Past 18:40, today's EOD is expected
    as_of_evening = datetime(2026, 1, 7, 16, 0, 0, tzinfo=UTC)
    expected_evening = get_latest_expected_trading_date(calendar, as_of_utc=as_of_evening)
    assert expected_evening == date(2026, 1, 7)


def test_get_latest_expected_trading_date_weekend() -> None:
    """Verify Sunday evaluates to the preceding Friday."""
    calendar = BISTCalendar()
    # Sunday 2026-01-11 12:00 UTC
    as_of_sunday = datetime(2026, 1, 11, 12, 0, 0, tzinfo=UTC)
    expected = get_latest_expected_trading_date(calendar, as_of_utc=as_of_sunday)
    assert expected == date(2026, 1, 9)  # Friday


def test_compute_data_freshness_eod_fresh() -> None:
    """Verify fresh EOD bar evaluation and formatting."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="licensed_eod")
    # Evaluating on Wednesday 2026-01-07 at 19:30 TRT (16:30 UTC)
    now_utc = datetime(2026, 1, 7, 16, 30, 0, tzinfo=UTC)

    info = compute_data_freshness(bar, as_of_utc=now_utc)
    assert isinstance(info, DataFreshnessInfo)
    assert info.symbol == "THYAO"
    assert info.trade_date == date(2026, 1, 7)
    assert info.delay_category == DelayCategory.EOD_OFFICIAL
    assert info.delay_minutes == 0
    assert info.is_stale is False
    assert info.stale_reason is None
    assert info.display_badge == "GÜN SONU (EOD)"
    assert info.freshness_label == "Güncel (EOD)"
    assert "bilgilendirme" in info.provenance_notice.lower()


def test_compute_data_freshness_stale_detection() -> None:
    """Verify stale detection when bar date is older than expected latest session."""
    # Bar is from Friday 2026-01-02
    bar = _make_sample_bar(trade_date=date(2026, 1, 2), source_name="licensed_eod")
    # Evaluating on Wednesday 2026-01-07 19:30 TRT (when 2026-01-07 is expected)
    now_utc = datetime(2026, 1, 7, 16, 30, 0, tzinfo=UTC)

    info = compute_data_freshness(bar, as_of_utc=now_utc)
    assert info.is_stale is True
    assert info.stale_reason is not None
    assert "2026-01-07" in info.stale_reason
    assert info.freshness_label == "Eski Veri"


def test_compute_data_freshness_synthetic_category() -> None:
    """Verify synthetic source automatically receives SYNTHETIC category and warning notice."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="synthetic_generator")
    info = compute_data_freshness(bar)
    assert info.delay_category == DelayCategory.SYNTHETIC
    assert info.display_badge == "SENTETİK VERİ"
    assert "simülasyon" in info.provenance_notice.lower()


def test_compute_data_freshness_delayed_override() -> None:
    """Verify explicit DELAYED_15MIN override sets 15 minutes delay and badge."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="licensed_feed")
    info = compute_data_freshness(
        bar,
        override_delay_category=DelayCategory.DELAYED_15MIN,
    )
    assert info.delay_category == DelayCategory.DELAYED_15MIN
    assert info.delay_minutes == 15
    assert info.display_badge == "15 DK GECİKMELİ"
    assert "15 dakika gecikmelidir" in info.provenance_notice


def test_compute_data_freshness_derived_bar() -> None:
    """Verify DerivedBarRecord works seamlessly with compute_data_freshness."""
    from stock_analysis.pipeline.storage import DerivedBarRecord

    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="licensed_feed")
    derived = DerivedBarRecord.from_adjusted_bar(
        bar,
        raw_hash="a" * 64,
        derived_at=datetime(2026, 1, 7, 16, 0, 0, tzinfo=UTC),
    )
    info = compute_data_freshness(derived, as_of_utc=datetime(2026, 1, 7, 19, 0, 0, tzinfo=UTC))
    assert info.symbol == "THYAO"
    assert info.last_updated_utc == datetime(2026, 1, 7, 16, 0, 0, tzinfo=UTC)
    assert "19:00:00 TSI" in info.display_timestamp


def test_build_stock_detail_attribution_eod() -> None:
    """Verify stock detail attribution produces correct EOD labels and legal disclaimer."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="licensed_bist_eod")
    now_utc = datetime(2026, 1, 7, 16, 0, 0, tzinfo=UTC)

    attribution = build_stock_detail_attribution(bar, as_of_utc=now_utc)
    assert isinstance(attribution, StockDetailAttribution)
    assert attribution.symbol == "THYAO"
    assert attribution.delay_category == DelayCategory.EOD_OFFICIAL
    assert attribution.delay_label == "Gün Sonu (EOD)"
    assert attribution.delay_minutes == 0
    assert attribution.display_badge == "GÜN SONU (EOD)"
    assert "BIST / Lisanslı Veri Sağlayıcı" in attribution.source_display_name
    assert "gün sonu kapanış (EOD) gecikmelidir" in attribution.disclaimer_text
    assert "yatırım danışmanlığı veya al-sat tavsiyesi değildir" in attribution.disclaimer_text


def test_build_stock_detail_attribution_delayed_15min() -> None:
    """Verify 15-minute delayed market feed disclaimer and badge."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="bist_feed")
    attribution = build_stock_detail_attribution(
        bar, override_delay_category=DelayCategory.DELAYED_15MIN
    )
    assert attribution.delay_category == DelayCategory.DELAYED_15MIN
    assert attribution.delay_label == "15 Dakika Gecikmeli"
    assert attribution.delay_minutes == 15
    assert attribution.display_badge == "15 DK GECİKMELİ"
    assert "15 dakika gecikmelidir" in attribution.disclaimer_text


def test_build_stock_detail_attribution_realtime() -> None:
    """Verify realtime market feed disclaimer and badge."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="licensed_live")
    attribution = build_stock_detail_attribution(
        bar, override_delay_category=DelayCategory.REALTIME
    )
    assert attribution.delay_category == DelayCategory.REALTIME
    assert attribution.delay_label == "Canlı (Gerçek Zamanlı)"
    assert attribution.delay_minutes == 0
    assert attribution.display_badge == "CANLI (GERÇEK ZAMANLI)"
    assert "0 dakika (canlı / gerçek zamanlı) gecikmelidir" in attribution.disclaimer_text


def test_build_stock_detail_attribution_synthetic() -> None:
    """Verify synthetic feed attribution indicates simulation provider and disclaimer."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="synthetic_engine")
    attribution = build_stock_detail_attribution(bar)
    assert attribution.delay_category == DelayCategory.SYNTHETIC
    assert attribution.delay_label == "Sentetik Simülasyon"
    assert attribution.source_display_name == "Sentetik Simülasyon Motoru"
    assert "simülasyon (sentetik veri) gecikmelidir" in attribution.disclaimer_text


def test_build_stock_detail_view_and_rendering() -> None:
    """Verify StockDetailView combines header badges, disclaimers, and serialization dictionary."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 7), source_name="bist_provider")
    now_utc = datetime(2026, 1, 7, 16, 0, 0, tzinfo=UTC)

    view = build_stock_detail_view(
        bar,
        as_of_utc=now_utc,
        custom_source_display_name="Matriks / BIST Resmi Lisanslı",
    )
    assert isinstance(view, StockDetailView)
    assert view.symbol == "THYAO"
    assert view.attribution.source_display_name == "Matriks / BIST Resmi Lisanslı"
    assert view.render_header_badge() == "GÜN SONU (EOD)"
    assert "Matriks / BIST Resmi Lisanslı kaynaklıdır" in view.render_disclaimer()

    display_dict = view.to_display_dict()
    assert display_dict["symbol"] == "THYAO"
    assert display_dict["badge"] == "GÜN SONU (EOD)"
    assert display_dict["source"] == "Matriks / BIST Resmi Lisanslı"
    assert display_dict["delay"] == "Gün Sonu (EOD)"
    assert display_dict["is_stale"] is False
    assert "yatırım danışmanlığı" in display_dict["disclaimer"]


def test_stock_detail_attribution_stale_data() -> None:
    """Verify stale condition is captured in stock detail attribution."""
    bar = _make_sample_bar(trade_date=date(2026, 1, 2), source_name="licensed_bist")
    now_utc = datetime(2026, 1, 7, 16, 30, 0, tzinfo=UTC)

    attribution = build_stock_detail_attribution(bar, as_of_utc=now_utc)
    assert attribution.is_stale is True
    assert attribution.stale_reason is not None
    assert "2026-01-07" in attribution.stale_reason
