"""Tests for ModelPublishingGate and baseline/friction publishing constraints."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.cost_model import MarketFrictionConfig
from stock_analysis.models.gatekeeper import (
    GatekeeperConfig,
    ModelNotApprovedForPublishingError,
    ModelPublishingGate,
)
from stock_analysis.models.strategy_backtest import (
    StrategyBacktestConfig,
)


@pytest.fixture
def sample_market_bars() -> pd.DataFrame:
    """Simülasyon için 100 barlık sentetik fiyat ve hacim serisi."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    # Yükselen ve dalgalı fiyat serisi
    trend = np.linspace(100.0, 150.0, n)
    noise = np.random.normal(0, 1.5, n)
    close = trend + noise
    volume = np.random.uniform(50_000, 200_000, n)
    return pd.DataFrame(
        {
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


def test_gatekeeper_config_validation() -> None:
    """Yapılandırma doğrulama testleri."""
    # Varsayılan yapılandırma geçerli olmalı
    config = GatekeeperConfig()
    assert config.require_baseline_beat is True
    assert config.require_positive_net_return is True
    assert config.require_positive_sharpe is True
    assert config.max_allowed_drawdown_pct == 35.0

    # Geçersiz maksimum düşüş
    with pytest.raises(ValueError, match="Geçersiz azami düşüş oranı"):
        GatekeeperConfig(max_allowed_drawdown_pct=0.0)

    with pytest.raises(ValueError, match="Geçersiz azami düşüş oranı"):
        GatekeeperConfig(max_allowed_drawdown_pct=105.0)

    # Negatif asgari işlem
    with pytest.raises(ValueError, match="Asgari işlem sayısı negatif olamaz"):
        GatekeeperConfig(min_trade_count=-1)


def test_gatekeeper_approves_superior_model(sample_market_bars: pd.DataFrame) -> None:
    """Hem baseline'ı hem de friksiyon kıyasını geçen model onaylanmalıdır."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)

    # Mükemmel/başarılı model tahminleri (gerçek değere çok yakın)
    y_true = list(close[1:])
    y_baseline = list(close[:-1])  # Naif son değer baseline'ı
    y_model = [t + 0.05 for t in y_true]  # Çok düşük hata

    # Başarılı alım sinyali: düzenli kârlı işlemler
    signals = [1.0 if i % 20 < 10 else 0.0 for i in range(n)]

    gate = ModelPublishingGate(
        GatekeeperConfig(
            require_baseline_beat=True,
            require_positive_net_return=True,
            require_positive_sharpe=True,
            min_trade_count=2,
        )
    )

    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="THYAO",
        model_name="TrendMaster-v1",
        baseline_name="last_value",
        last_known_prices=list(close[:-1]),
    )

    assert decision.is_approved is True
    assert decision.beats_baseline is True
    assert decision.beats_friction_cost is True
    assert len(decision.rejection_reasons) == 0
    assert decision.recommended_forecast_enabled is True
    assert "Yayına Uygun" in decision.user_facing_status
    assert decision.baseline_report is not None
    assert decision.backtest_report is not None
    assert decision.simulation_result is not None

    # enforce_approval hata fırlatmamalı
    gate.enforce_approval(decision)

    # create_safe_serving_fallback onaylı dönmeli
    fallback = ModelPublishingGate.create_safe_serving_fallback(decision)
    assert fallback["forecast_enabled"] is True
    assert fallback["status"] == "APPROVED"


def test_gatekeeper_rejects_model_failing_naive_baseline(
    sample_market_bars: pd.DataFrame,
) -> None:
    """Naif baseline'ı geçemeyen model yayından men edilmelidir."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)

    y_true = list(close[1:])
    y_baseline = list(close[:-1])
    # Kötü model: Hataları baseline'dan çok daha büyük
    y_model = [t + 25.0 for t in y_true]

    signals = [1.0 if i % 20 < 10 else 0.0 for i in range(n)]

    gate = ModelPublishingGate()
    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="ASELS",
        model_name="BadModel-v1",
    )

    assert decision.is_approved is False
    assert decision.beats_baseline is False
    assert decision.recommended_forecast_enabled is False
    assert any("naif baseline'ı" in r for r in decision.rejection_reasons)
    assert "Tahmin Kapalı" in decision.user_facing_status

    # enforce_approval kesinlikle istisna fırlatmalıdır
    with pytest.raises(ModelNotApprovedForPublishingError, match="BadModel-v1.*yayına alınamaz"):
        gate.enforce_approval(decision)

    # create_safe_serving_fallback sessiz uydurma yapmamalı, açıkça kapalı dönmeli
    fallback = ModelPublishingGate.create_safe_serving_fallback(decision)
    assert fallback["forecast_enabled"] is False
    assert fallback["status"] == "FORECAST_DISABLED"
    assert "Tahmin şu an kapalı" in fallback["message"]
    assert len(fallback["reasons"]) > 0


def test_gatekeeper_rejects_model_failing_friction_cost(
    sample_market_bars: pd.DataFrame,
) -> None:
    """Baseline'ı geçse bile friksiyon sonrası zarar eden model reddedilmelidir."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)

    y_true = list(close[1:])
    y_baseline = list(close[:-1])
    # Model baseline'dan biraz daha iyi MAE'ye sahip olsun
    y_model = [t + 0.01 for t in y_true]

    # Sürekli her bar al-sat yapan aşırı işlem stratejisi (komisyon ve kayma sermayeyi eritir)
    signals = [1.0 if i % 2 == 0 else 0.0 for i in range(n)]

    gate = ModelPublishingGate(
        GatekeeperConfig(
            require_baseline_beat=True,
            require_positive_net_return=True,
            require_positive_sharpe=True,
            # Yüksek komisyonlu zorlayıcı friksiyon
            backtest_config=StrategyBacktestConfig(
                execution_lag_bars=1,
                friction_config=MarketFrictionConfig(
                    commission_rate=0.005,  # %0.5 yüksek komisyon
                    slippage_impact_factor=0.002,
                ),
            ),
        )
    )

    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="GARAN",
        model_name="HighTurnoverModel",
    )

    assert decision.beats_baseline is True
    assert decision.beats_friction_cost is False
    assert decision.is_approved is False
    assert any(
        "net getiri" in r.lower() or "sharpe" in r.lower() for r in decision.rejection_reasons
    )

    with pytest.raises(ModelNotApprovedForPublishingError):
        gate.enforce_approval(decision)


def test_gatekeeper_insufficient_trade_count(sample_market_bars: pd.DataFrame) -> None:
    """Yeterli işlem sayısına ulaşmayan strateji şans faktörü nedeniyle reddedilmelidir."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)

    y_true = list(close[1:])
    y_baseline = list(close[:-1])
    y_model = [t + 0.01 for t in y_true]

    # Sıfır işlem: sürekli nakitte kalıp hiç işlem açmamak
    signals = [0.0] * n

    gate = ModelPublishingGate(
        GatekeeperConfig(
            min_trade_count=3,
        )
    )

    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="EREGL",
        model_name="OneTradeModel",
    )

    assert decision.beats_friction_cost is False
    assert decision.is_approved is False
    assert any("işlem sayısı" in r for r in decision.rejection_reasons)


def test_gatekeeper_drawdown_limit_violation(sample_market_bars: pd.DataFrame) -> None:
    """Maksimum düşüş limitini aşan model reddedilmelidir."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)

    y_true = list(close[1:])
    y_baseline = list(close[:-1])
    y_model = [t + 0.01 for t in y_true]
    signals = [1.0 if i % 10 < 5 else 0.0 for i in range(n)]

    # Çok katı %0.5 MDD limiti koyuyoruz (dalgalı piyasa bunu kesin aşar)
    gate = ModelPublishingGate(
        GatekeeperConfig(
            max_allowed_drawdown_pct=0.5,
        )
    )

    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="KCHOL",
        model_name="HighDrawdownModel",
    )

    assert decision.beats_friction_cost is False
    assert decision.is_approved is False
    assert any("Maksimum düşüş oranı" in r for r in decision.rejection_reasons)


def test_gatekeeper_buy_and_hold_benchmark_check(sample_market_bars: pd.DataFrame) -> None:
    """İsteğe bağlı buy-and-hold benchmark şartı denetimi."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)

    y_true = list(close[1:])
    y_baseline = list(close[:-1])
    y_model = [t + 0.01 for t in y_true]

    # Strateji kârlı ama yükselen piyasada az pozisyon alıp BnH'nin gerisinde kalıyor
    signals = [0.1 if i % 10 == 0 else 0.0 for i in range(n)]

    gate = ModelPublishingGate(
        GatekeeperConfig(
            require_beat_buy_and_hold=True,
            min_trade_count=1,
            require_positive_net_return=False,
            require_positive_sharpe=False,
        )
    )

    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="BIMAS",
        model_name="LaggardModel",
    )

    assert decision.beats_friction_cost is False
    assert any("Buy & Hold kıyas getirisinden" in r for r in decision.rejection_reasons)


def test_publishing_decision_to_dict_and_summary(sample_market_bars: pd.DataFrame) -> None:
    """Karar raporunun JSON sözlüğü ve formatlı özeti SPK uyumlu olmalıdır."""
    close = sample_market_bars["close"].to_numpy()
    n = len(close)
    y_true = list(close[1:])
    y_baseline = list(close[:-1])
    y_model = [t + 0.02 for t in y_true]
    signals = [1.0 if i % 15 < 8 else 0.0 for i in range(n)]

    gate = ModelPublishingGate()
    decision = gate.evaluate(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_baseline,
        bars=sample_market_bars,
        signals=signals,
        symbol="SISE",
        model_name="ReportingModel",
    )

    res_dict = decision.to_dict()
    assert res_dict["model_name"] == "ReportingModel"
    assert res_dict["symbol"] == "SISE"
    assert "disclaimer" in res_dict
    assert "SPK mevzuatı" in res_dict["disclaimer"]

    summary_text = decision.format_summary()
    assert "# Model Yayınlama Kapısı Değerlendirmesi: ReportingModel (SISE)" in summary_text
    assert "FORECAST_ENABLED Önerisi:" in summary_text
    assert "Yasal Uyarı" in summary_text
    assert "SPK mevzuatı" in summary_text
