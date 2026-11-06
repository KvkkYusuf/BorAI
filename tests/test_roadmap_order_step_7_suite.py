"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 7 test paketi.

İzleme listesi (Watchlist) ve temel göstergeler (Indicators) kabul paketi.
Test kapsamı:
- RoadmapStepSevenGatekeeper 6/6 tam uyum doğrulaması.
- İzleme listesi ORM veri modelleri (Watchlist, WatchlistItem, Foreign Keys, Unique Constraint).
- Çok kiracılı veri izolasyonu ve sıfır portföy/maliyet sütunu güvencesi.
- İzleme listesi REST API yaşam döngüsü (/user/watchlist GET, POST, DELETE, WatchlistResponse).
- Temel teknik gösterge matematik motoru (RSI-14, SMA-20/50, EMA-20, MACD, ATR-14, Volatilite).
- Analitik göstergeler API ve önbellek katmanı (/analytics/indicators, /analytics/screener).
- Frontend izleme listesi ve teknik göstergeler (HTML5 semantik yapı, mobil tablo/kart).
- SPK mevzuatı yatırım danışmanlığı yasağı, tarafsızlık kalkanı ve X-Disclaimer başlığı.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analysis.api.schemas import (
    IndicatorsData,
    IndicatorsResponse,
    ScreenerMatch,
    ScreenerResponse,
    WatchlistActionResponse,
    WatchlistResponse,
)
from stock_analysis.db.models import Watchlist, WatchlistItem
from stock_analysis.indicators.technical import (
    calculate_all_indicators,
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_volatility,
    calculate_volume_sma,
    get_latest_indicator_summary,
)
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP7_DISCLAIMER,
    ROADMAP_STEP7_CHECKS_TOTAL,
    ROADMAP_STEP7_PASSED_TOTAL,
    ROADMAP_STEP7_STATUS,
    RoadmapStepSevenGatekeeper,
    RoadmapStepSevenStatus,
    generate_roadmap_step7_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """Teknik analiz gösterge testleri için sentetik OHLCV DataFrame üretir."""
    np.random.seed(42)
    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    base_price = 100.0
    returns = np.random.normal(0.001, 0.02, size=n)
    close_prices = base_price * np.cumprod(1 + returns)

    high_prices = close_prices * (1 + np.abs(np.random.normal(0.005, 0.005, size=n)))
    low_prices = close_prices * (1 - np.abs(np.random.normal(0.005, 0.005, size=n)))
    open_prices = low_prices + (high_prices - low_prices) * np.random.uniform(0.2, 0.8, size=n)
    volumes = np.random.uniform(100000, 500000, size=n)

    return pd.DataFrame(
        {
            "open": open_prices,
            "high": high_prices,
            "low": low_prices,
            "close": close_prices,
            "volume": volumes,
        },
        index=dates,
    )


def test_roadmap_step7_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 7 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepSevenStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step7_watchlist_models_and_zero_portfolio(workspace_root: Path) -> None:
    """Watchlist ve WatchlistItem ORM modellerini ve sıfır portföy kuralını doğrular."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    check = result.checks[0]

    assert check.check_id == "ROADMAP-S7-01-WATCHLIST-DATA-MODELS-AND-ISOLATION"
    assert check.passed is True
    assert check.details["has_watchlist_class"] is True
    assert check.details["has_watchlist_item_class"] is True
    assert check.details["has_user_fk"] is True
    assert check.details["has_instrument_fk"] is True
    assert check.details["has_unique_constraint"] is True
    assert check.details["no_forbidden_cols"] is True

    # ORM sınıf özelliklerini dinamik doğrula
    assert hasattr(Watchlist, "user_id")
    assert hasattr(Watchlist, "items")
    assert hasattr(WatchlistItem, "watchlist_id")
    assert hasattr(WatchlistItem, "instrument_id")

    # SPK Kırmızı Çizgisi: Hiçbir portföy/maliyet/kâr-zarar sütunu olamaz
    for forbidden in ["portfolio_value", "purchase_price", "buy_price", "profit_loss", "lot_count"]:
        assert not hasattr(Watchlist, forbidden)
        assert not hasattr(WatchlistItem, forbidden)


def test_roadmap_step7_watchlist_rest_api_lifecycle(workspace_root: Path) -> None:
    """İzleme listesi REST API rotaları ve şemalarını denetler."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    check = result.checks[1]

    assert check.check_id == "ROADMAP-S7-02-WATCHLIST-REST-API-LIFECYCLE"
    assert check.passed is True
    assert check.details["has_get_wl"] is True
    assert check.details["has_add_wl"] is True
    assert check.details["has_del_wl"] is True
    assert check.details["has_auth_dep"] is True
    assert check.details["has_user_id_scope"] is True
    assert check.details["has_symbol_clean"] is True

    # Pydantic Şemalarını doğrula
    resp = WatchlistResponse(watchlist=["THYAO", "ASELS", "KCHOL"])
    assert len(resp.watchlist) == 3
    assert resp.watchlist[0] == "THYAO"

    action = WatchlistActionResponse(message="Sembol eklendi", symbol="THYAO")
    assert action.symbol == "THYAO"
    assert "eklendi" in action.message


def test_roadmap_step7_technical_indicators_engine(
    workspace_root: Path, sample_ohlcv_df: pd.DataFrame
) -> None:
    """Temel teknik gösterge fonksiyonlarının matematiksel doğruluğunu test eder."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    check = result.checks[2]

    assert check.check_id == "ROADMAP-S7-03-TECHNICAL-INDICATORS-ENGINE-AND-REFERENCES"
    assert check.passed is True

    close = sample_ohlcv_df["close"]
    high = sample_ohlcv_df["high"]
    low = sample_ohlcv_df["low"]
    volume = sample_ohlcv_df["volume"]

    # 1. SMA & EMA
    sma20 = calculate_sma(close, 20)
    assert len(sma20) == len(close)
    assert pd.notna(sma20.iloc[-1])
    assert pd.isna(sma20.iloc[18])  # min_periods 20

    ema20 = calculate_ema(close, 20)
    assert len(ema20) == len(close)
    assert pd.notna(ema20.iloc[-1])

    # 2. RSI (14)
    rsi14 = calculate_rsi(close, 14)
    assert len(rsi14) == len(close)
    last_rsi = float(rsi14.iloc[-1])
    assert 0.0 <= last_rsi <= 100.0

    # 3. MACD (12, 26, 9)
    macd_res = calculate_macd(close, 12, 26, 9)
    assert len(macd_res.macd_line) == len(close)
    assert len(macd_res.signal_line) == len(close)
    assert len(macd_res.histogram) == len(close)
    assert np.isclose(
        float(macd_res.histogram.iloc[-1]),
        float(macd_res.macd_line.iloc[-1] - macd_res.signal_line.iloc[-1]),
    )

    # 4. ATR (14)
    atr14 = calculate_atr(high, low, close, 14)
    assert len(atr14) == len(close)
    assert float(atr14.iloc[-1]) > 0.0

    # 5. Volatilite & Hacim SMA
    vol20 = calculate_volatility(close, 20)
    assert len(vol20) == len(close)
    vol_sma = calculate_volume_sma(volume, 20)
    assert len(vol_sma) == len(volume)

    # 6. Toplu Hesaplama ve Özet
    full_df = calculate_all_indicators(sample_ohlcv_df, symbol="TEST")
    assert "sma_20" in full_df.columns
    assert "rsi_14" in full_df.columns
    assert "macd_line" in full_df.columns
    assert "atr_14" in full_df.columns

    summary = get_latest_indicator_summary(full_df, symbol="TEST")
    assert summary.symbol == "TEST"
    assert summary.rsi_14 is not None
    assert summary.sma_20 is not None

    # Hatalı parametre denetimi
    with pytest.raises(ValueError):
        calculate_sma(close, period=0)
    with pytest.raises(ValueError):
        calculate_rsi(close, period=-1)
    with pytest.raises(ValueError):
        calculate_macd(close, fast_period=26, slow_period=12)


def test_roadmap_step7_indicators_api_and_caching(workspace_root: Path) -> None:
    """Analitik göstergeler API uçlarını, screener filtresini ve Pydantic modellerini denetler."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    check = result.checks[3]

    assert check.check_id == "ROADMAP-S7-04-INDICATORS-API-AND-CACHING-LAYER"
    assert check.passed is True
    assert check.details["has_indicators_endpoint"] is True
    assert check.details["has_screener_endpoint"] is True
    assert check.details["has_cache_usage"] is True
    assert check.details["has_rate_limiter"] is True
    assert check.details["has_neutral_summary"] is True

    # Pydantic Şema doğrulaması
    data = IndicatorsData(rsi_14=55.5, sma_20=310.0, sma_50=295.0)
    from datetime import UTC, datetime

    resp = IndicatorsResponse(
        symbol="THYAO",
        calculated_at=datetime.now(UTC),
        indicators=data,
        neutral_summary="RSI 55.5 ve fiyat ortalamanın üzerinde.",
    )
    assert resp.symbol == "THYAO"
    assert resp.indicators.rsi_14 == 55.5

    screener_resp = ScreenerResponse(
        filter_applied={"rsi_max": 40.0},
        matches=[ScreenerMatch(symbol="EKGYO", close=9.42, rsi_14=31.2)],
        total=1,
        page=1,
        page_size=50,
    )
    assert screener_resp.total == 1
    assert screener_resp.matches[0].symbol == "EKGYO"


def test_roadmap_step7_frontend_watchlist_and_indicator_ui(workspace_root: Path) -> None:
    """Ön yüz HTML ve JS bileşenlerinin entegrasyonunu doğrular."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    check = result.checks[4]

    assert check.check_id == "ROADMAP-S7-05-FRONTEND-WATCHLIST-AND-INDICATOR-UI"
    assert check.passed is True
    assert check.details["has_watchlist_screen"] is True
    assert check.details["has_watchlist_table"] is True
    assert check.details["has_empty_state"] is True
    assert check.details["has_count_badge"] is True
    assert check.details["has_toggle_btn"] is True
    assert check.details["has_tech_cards"] is True
    assert check.details["has_js_watchlist_state"] is True


def test_roadmap_step7_spk_neutrality_and_disclaimer(workspace_root: Path) -> None:
    """SPK yasal uyarı ve tarafsızlık kontrolünü denetler."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    check = result.checks[5]

    assert check.check_id == "ROADMAP-S7-06-SPK-NEUTRALITY-AND-DISCLAIMER-COMPLIANCE"
    assert check.passed is True
    assert check.details["disclaimer_valid"] is True
    assert check.details["has_spk_banner"] is True
    assert check.details["has_disclaimer_hdr"] is True
    assert check.details["is_strictly_neutral"] is True

    # Geçersiz disclaimer durumunda hata üretildiğini doğrula
    with pytest.raises(ValueError):
        RoadmapStepSevenGatekeeper(
            workspace_root=workspace_root,
            disclaimer="Bu hisseyi kesinlikle almalısınız, hedef fiyat 500 TL",
        )


def test_roadmap_step7_report_generation_and_metrics(workspace_root: Path) -> None:
    """Markdown denetim raporu üretimini ve telemetri metriklerini test eder."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    report = generate_roadmap_step7_report(result)
    assert "# Yol Haritası Uygulama Sırası — Adım 7 Denetim Raporu" in report
    assert "✅ PASS" in report
    assert "ROADMAP-S7-01-WATCHLIST-DATA-MODELS-AND-ISOLATION" in report
    assert "ROADMAP-S7-06-SPK-NEUTRALITY-AND-DISCLAIMER-COMPLIANCE" in report
    assert DEFAULT_ROADMAP_STEP7_DISCLAIMER in report

    # Prometheus metrik doğrulaması
    assert ROADMAP_STEP7_STATUS.get() == 1.0
    assert ROADMAP_STEP7_CHECKS_TOTAL.get() >= 6.0
    assert ROADMAP_STEP7_PASSED_TOTAL.get() >= 6.0


def test_roadmap_step7_missing_workspace_failure(tmp_path: Path) -> None:
    """Boş geçici dizinde Adım 7 değerlendirmesinin FAIL döndürdüğünü doğrular."""
    gatekeeper = RoadmapStepSevenGatekeeper(workspace_root=tmp_path)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepSevenStatus.FAIL
    assert result.failed_checks > 0
    assert result.passed_checks == 0

