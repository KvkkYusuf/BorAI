"""Model strateji simülasyonu, işlem maliyeti, gecikme ve pozisyon sınırları testleri."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.cost_model import MarketFrictionConfig, TradeAction
from stock_analysis.models.strategy_backtest import (
    BacktestPerformanceReport,
    PositionLimitViolationError,
    StrategyBacktestConfig,
    StrategySimulator,
    ZeroLagLeakageError,
    calculate_backtest_performance,
)


def _create_sample_bars(
    n: int = 50, start_price: float = 100.0, volume: float = 100_000.0
) -> pd.DataFrame:
    """Tekrarlanabilir sentetik fiyat ve hacim serisi oluşturur."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [start_price]
    for _ in range(1, n):
        ret = float(np.random.normal(0.001, 0.015))
        prices.append(max(1.0, prices[-1] * (1.0 + ret)))
    return pd.DataFrame({"close": prices, "volume": [volume] * n}, index=dates)


class TestStrategySimulationConfig:
    """Yapılandırma ve kısıt doğrulama testleri."""

    def test_zero_lag_raises_error_unless_explicitly_allowed(self) -> None:
        """Gecikmesiz (sıfır bar) simülasyon gelecek sızıntısı sayılmalı ve engellenmelidir."""
        with pytest.raises(ZeroLagLeakageError):
            StrategyBacktestConfig(execution_lag_bars=0, allow_zero_lag=False)

        # Açıkça izin verildiğinde hata vermemeli
        cfg = StrategyBacktestConfig(execution_lag_bars=0, allow_zero_lag=True)
        assert cfg.execution_lag_bars == 0

    def test_invalid_position_weight_raises_error(self) -> None:
        """Pozisyon ağırlığı 0 ile 1 aralığı dışında olamaz."""
        with pytest.raises(PositionLimitViolationError):
            StrategyBacktestConfig(max_position_weight=0.0)

        with pytest.raises(PositionLimitViolationError):
            StrategyBacktestConfig(max_position_weight=1.5)

    def test_invalid_initial_capital_raises_error(self) -> None:
        """Başlangıç sermayesi pozitif olmalıdır."""
        with pytest.raises(ValueError, match="Başlangıç sermayesi"):
            StrategyBacktestConfig(initial_capital=-500.0)


class TestStrategyExecutionLag:
    """Sinyal icra gecikmesi (execution lag) testleri."""

    def test_signal_execution_is_delayed_by_lag_bars(self) -> None:
        """t anındaki sinyal ancak t + lag anında işleme dönüşmelidir."""
        bars = _create_sample_bars(n=10)
        # 2. barda (index 2) alış sinyali üretilsin
        signals = np.zeros(len(bars))
        signals[2] = 1.0

        config = StrategyBacktestConfig(
            execution_lag_bars=1,
            max_position_weight=1.0,
            allow_short=False,
        )
        simulator = StrategySimulator(config)
        result = simulator.simulate(bars, signals, symbol="THYAO")

        assert len(result.trades) >= 1
        first_trade = result.trades[0]
        # Sinyal index 2'de üretildi, icra index 3'te olmalıdır
        assert first_trade.bar_index == 3
        assert first_trade.action == TradeAction.ENTRY

    def test_multi_bar_lag(self) -> None:
        """2 barlık gecikmede t anındaki sinyal t+2 anında icra edilmelidir."""
        bars = _create_sample_bars(n=10)
        signals = np.zeros(len(bars))
        signals[1] = 1.0

        config = StrategyBacktestConfig(
            execution_lag_bars=2,
            max_position_weight=1.0,
        )
        simulator = StrategySimulator(config)
        result = simulator.simulate(bars, signals, symbol="GARAN")

        assert len(result.trades) >= 1
        assert result.trades[0].bar_index == 3


class TestPositionLimitsAndShortRestiction:
    """Pozisyon sınırları ve açığa satış engeli testleri."""

    def test_short_selling_is_restricted_by_default(self) -> None:
        """BIST kuralları gereği allow_short=False iken negatif sinyaller short açmamalıdır."""
        bars = _create_sample_bars(n=15)
        # Negatif (short) sinyal verilsin
        signals = np.array([-1.0] * len(bars))

        config = StrategyBacktestConfig(execution_lag_bars=1, allow_short=False)
        simulator = StrategySimulator(config)
        result = simulator.simulate(bars, signals, symbol="KCHOL")

        # Short açılamayacağı için hiç işlem olmamalı
        assert result.total_trades == 0
        assert result.final_capital == config.initial_capital

    def test_max_position_weight_limits_capital_allocation(self) -> None:
        """Portföy ağırlığı max_position_weight tavanını aşmamalıdır."""
        bars = _create_sample_bars(n=20)
        signals = np.ones(len(bars))  # %100 alış sinyali

        # Portföyün azami %40'ı ayrılabilsin
        config = StrategyBacktestConfig(
            execution_lag_bars=1,
            max_position_weight=0.40,
            cash_buffer_pct=0.01,
        )
        simulator = StrategySimulator(config)
        result = simulator.simulate(bars, signals, symbol="ASELS")

        assert result.total_trades >= 1
        # Gözlenen azami pozisyon ağırlığı %40'ı geçmemelidir
        # (küçük fiyat dalgalanması hariç bar başında)
        assert result.max_position_weight_observed <= 0.45


class TestTransactionFrictionAndSlippage:
    """İşlem maliyeti, spread ve kayma payı testleri."""

    def test_frictions_create_drag_between_gross_and_net_return(self) -> None:
        """Piyasa sürtünmeleri net getiriyi brüt getirinin altına düşürmelidir."""
        bars = _create_sample_bars(n=30)
        # Dalgalı al-sat sinyalleri (sık işlem)
        signals = np.array([1.0 if i % 2 == 1 else 0.0 for i in range(len(bars))])

        config = StrategyBacktestConfig(
            execution_lag_bars=1,
            friction_config=MarketFrictionConfig(
                commission_rate=0.002,
                bsmv_rate=0.05,
                exchange_fee_rate=0.00005,
                half_spread_pct=0.001,
                slippage_impact_factor=0.05,
            ),
        )
        simulator = StrategySimulator(config)
        result = simulator.simulate(bars, signals, symbol="SISE")

        assert result.total_trades > 2
        assert result.total_friction_try > 0.0
        assert result.net_return_pct < result.gross_return_pct
        assert result.friction_drag_pct > 0.0
        assert result.is_realistic is True

    def test_low_liquidity_increases_slippage(self) -> None:
        """Düşük hacimli barlarda kayma (slippage) payı belirgin şekilde yüksek olmalıdır."""
        # Yüksek hacimli tahta
        bars_liquid = _create_sample_bars(n=10, volume=1_000_000.0)
        # Düşük hacimli tahta
        bars_illiquid = _create_sample_bars(n=10, volume=5_000.0)

        signals = np.zeros(10)
        signals[0] = 1.0  # Alım

        config = StrategyBacktestConfig(execution_lag_bars=1, initial_capital=200_000.0)
        sim = StrategySimulator(config)

        res_liquid = sim.simulate(bars_liquid, signals)
        res_illiquid = sim.simulate(bars_illiquid, signals)

        assert len(res_liquid.trades) >= 1
        assert len(res_illiquid.trades) >= 1

        trade_liq = res_liquid.trades[0]
        trade_illiq = res_illiquid.trades[0]

        # Sığ tahtada slippage tutarı derin tahtadan yüksek olmalı
        assert trade_illiq.slippage_cost_try > trade_liq.slippage_cost_try


class TestSPKComplianceAndValidation:
    """Yasal uyum ve girdi doğrulama testleri."""

    def test_disclaimer_contains_compliant_wording(self) -> None:
        """Simülasyon çıktısı SPK uyumlu yasal uyarı içermelidir."""
        bars = _create_sample_bars(n=10)
        signals = np.ones(10)
        simulator = StrategySimulator()
        result = simulator.simulate(bars, signals)

        assert "yatırım danışmanlığı veya getiri vaadi niteliğinde değildir" in result.disclaimer
        assert "Geçmiş performanslar gelecekteki sonuçların göstergesi olamaz" in result.disclaimer

    def test_mismatched_signal_length_raises_error(self) -> None:
        """Fiyat serisi ile sinyal uzunluğu uyuşmazlığında hata vermelidir."""
        bars = _create_sample_bars(n=15)
        signals = np.ones(10)
        simulator = StrategySimulator()
        with pytest.raises(ValueError, match="eşleşmiyor"):
            simulator.simulate(bars, signals)


class TestBacktestPerformanceMetrics:
    """Sharpe, maksimum düşüş, getiri ve varsayım raporlama testleri."""

    def test_metrics_calculation_basic(self) -> None:
        """Standart simülasyon çıktısından metriklerin doğru hesaplandığını doğrular."""
        bars = _create_sample_bars(n=50)
        signals = np.ones(50)  # Sürekli pozisyon

        simulator = StrategySimulator(StrategyBacktestConfig(execution_lag_bars=1))
        sim_result = simulator.simulate(bars, signals, symbol="EREGL")

        report = sim_result.generate_performance_report(risk_free_rate_annual=0.10)

        assert isinstance(report, BacktestPerformanceReport)
        assert report.symbol == "EREGL"
        assert report.assumptions.total_bars == 50
        assert report.assumptions.risk_free_rate_annual == 0.10
        assert report.assumptions.execution_lag_bars == 1
        assert report.max_drawdown_pct >= 0.0
        assert report.peak_bar_index <= report.trough_bar_index
        assert isinstance(report.sharpe_ratio, float)
        assert isinstance(report.sortino_ratio, float)

        # Sözlük ve metin özetini kontrol et
        rep_dict = report.to_dict()
        assert rep_dict["symbol"] == "EREGL"
        assert "sharpe_ratio" in rep_dict
        assert "max_drawdown_pct" in rep_dict
        assert "assumptions" in rep_dict

        summary_text = report.format_summary()
        assert "Strateji Performans Raporu: EREGL" in summary_text
        assert "Sharpe Oranı" in summary_text
        assert "Maksimum Düşüş" in summary_text
        assert "Örneklem ve Model Varsayımları" in summary_text

    def test_max_drawdown_calculation_precision(self) -> None:
        """Bilinen fiyat düşüşünde Max DD, tepe ve dip bar tespitini doğrular."""
        # Portföy 100 -> 120 -> 90 -> 110 olsun
        # Tepe: 120 (index 1), Dip: 90 (index 2)
        # DD = (120 - 90) / 120 = 25.0%
        dates = pd.date_range("2026-01-01", periods=4, freq="B")
        bars = pd.DataFrame(
            {"close": [100.0, 120.0, 90.0, 110.0], "volume": [100_000.0] * 4},
            index=dates,
        )
        # Sıfır gecikme ve maliyetsiz ayarla
        cfg = StrategyBacktestConfig(
            execution_lag_bars=0,
            allow_zero_lag=True,
            initial_capital=100_000.0,
            friction_config=MarketFrictionConfig(
                commission_rate=0.0,
                bsmv_rate=0.0,
                exchange_fee_rate=0.0,
                half_spread_pct=0.0,
                slippage_impact_factor=0.0,
            ),
        )
        sim = StrategySimulator(cfg)
        signals = np.array([1.0, 1.0, 1.0, 1.0])
        sim_res = sim.simulate(bars, signals, symbol="TEST")

        report = calculate_backtest_performance(sim_res)

        assert pytest.approx(report.max_drawdown_pct, rel=1e-2) == 25.0
        assert report.peak_bar_index == 1
        assert report.trough_bar_index == 2
        assert report.max_drawdown_duration_bars >= 1

    def test_zero_volatility_yields_zero_sharpe(self) -> None:
        """İşlem yapılmayan ve getiri oynamayan durumda Sharpe 0 dönmelidir."""
        bars = _create_sample_bars(n=10)
        signals = np.zeros(10)  # Tamamı nakit, sıfır işlem

        sim = StrategySimulator()
        res = sim.simulate(bars, signals)
        report = res.generate_performance_report()

        assert report.total_return_pct == 0.0
        assert report.annualized_volatility_pct == 0.0
        assert report.sharpe_ratio == 0.0
        assert report.max_drawdown_pct == 0.0

    def test_performance_disclaimer_compliance(self) -> None:
        """Performans raporu SPK uyumlu yasal uyarı içermelidir."""
        bars = _create_sample_bars(n=10)
        signals = np.ones(10)
        sim = StrategySimulator()
        res = sim.simulate(bars, signals)
        report = res.generate_performance_report()

        expected_phrase = (
            "Geçmiş sonuçlar gelecekteki performans için bir vaat veya gösterge teşkil etmez"
        )
        assert expected_phrase in report.disclaimer
        assert "yatırım danışmanlığı kapsamında olmayıp" in report.disclaimer
