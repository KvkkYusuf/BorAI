"""Piyasa rejimleri, kriz senaryoları ve sembol grupları stres testi motoru testleri."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.cost_model import MarketFrictionConfig
from stock_analysis.models.stress_testing import (
    MarketRegimeType,
    StressScenarioConfig,
    StressScenarioType,
    StressTestReport,
    SymbolGroupConfig,
    SymbolGroupType,
    apply_stress_to_dataframe,
    classify_market_regime,
    detect_regimes_in_series,
    get_default_stress_scenarios,
    get_default_symbol_groups,
    run_stress_test,
    run_symbol_basket_stress_test,
)


def _create_sample_bars(
    n: int = 50, start_price: float = 100.0, volume: float = 100_000.0, trend: float = 0.001
) -> pd.DataFrame:
    """Tekrarlanabilir fiyat ve hacim DataFrame'i üretir."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [start_price]
    for _ in range(1, n):
        ret = float(np.random.normal(trend, 0.01))
        prices.append(max(1.0, prices[-1] * (1.0 + ret)))
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [volume] * n,
        },
        index=dates,
    )


class TestMarketRegimeDetection:
    """Piyasa rejimlerinin tespiti ve sınıflandırılması testleri."""

    def test_classify_market_regime_bull(self) -> None:
        """Pozitif getirili ve düşük varyanslı seri boğa (bull) olarak sınıflandırılmalıdır."""
        bull_returns = [0.005, 0.003, 0.004, 0.002, 0.006, 0.003]
        regime = classify_market_regime(bull_returns, threshold_vol=0.03, threshold_trend=0.001)
        assert regime == MarketRegimeType.BULL

    def test_classify_market_regime_bear(self) -> None:
        """Negatif getirili seri ayı (bear) olarak sınıflandırılmalıdır."""
        bear_returns = [-0.005, -0.003, -0.004, -0.002, -0.006]
        regime = classify_market_regime(bear_returns, threshold_vol=0.03, threshold_trend=0.001)
        assert regime == MarketRegimeType.BEAR

    def test_classify_market_regime_high_volatility(self) -> None:
        """Yüksek oynaklıklı seri 'high_volatility' olarak etiketlenmelidir."""
        volatile_returns = [0.05, -0.06, 0.07, -0.08, 0.06, -0.05]
        regime = classify_market_regime(volatile_returns, threshold_vol=0.02)
        assert regime == MarketRegimeType.HIGH_VOLATILITY

    def test_classify_market_regime_sideways(self) -> None:
        """Sıfıra yakın getiri ve düşük oynaklık yatay (sideways) olmalıdır."""
        sideways_returns = [0.0001, -0.0001, 0.0002, -0.0002, 0.0000]
        regime = classify_market_regime(sideways_returns)
        assert regime == MarketRegimeType.SIDEWAYS

    def test_detect_regimes_in_series(self) -> None:
        """Zaman serisi üzerinde pencereleme ile rejim tespiti test edilir."""
        bars = _create_sample_bars(n=40)
        regimes = detect_regimes_in_series(bars["close"], window=10)
        assert len(regimes) == 40
        assert isinstance(regimes, pd.Series)
        # İlk window bar boyunca varsayılan 'sideways' olmalı
        assert regimes.iloc[0] == MarketRegimeType.SIDEWAYS.value

    def test_detect_regimes_in_series_short_raises_error(self) -> None:
        """Seri uzunluğu pencere boyutundan küçükse ValueError fırlatılmalıdır."""
        bars = _create_sample_bars(n=10)
        with pytest.raises(ValueError, match="küçük olamaz"):
            detect_regimes_in_series(bars["close"], window=15)


class TestStressScenarioConfiguration:
    """Stres senaryosu parametre ve doğrulama testleri."""

    def test_invalid_volume_multiplier_raises_error(self) -> None:
        """Hacim çarpanı pozitif olmalıdır."""
        with pytest.raises(ValueError, match="Hacim çarpanı"):
            StressScenarioConfig(
                scenario_type=StressScenarioType.LIQUIDITY_CRUNCH,
                name="Test",
                description="Test",
                volume_multiplier=-0.5,
            )

    def test_invalid_spread_multiplier_raises_error(self) -> None:
        """Spread çarpanı pozitif olmalıdır."""
        with pytest.raises(ValueError, match="Spread çarpanı"):
            StressScenarioConfig(
                scenario_type=StressScenarioType.LIQUIDITY_CRUNCH,
                name="Test",
                description="Test",
                spread_multiplier=0.0,
            )

    def test_invalid_duration_bars_raises_error(self) -> None:
        """Şok süresi asgari 1 bar olmalıdır."""
        with pytest.raises(ValueError, match="Şok süresi"):
            StressScenarioConfig(
                scenario_type=StressScenarioType.FLASH_CRASH,
                name="Test",
                description="Test",
                shock_duration_bars=0,
            )

    def test_default_scenarios_and_groups_count(self) -> None:
        """Varsayılan senaryolar ve sembol grupları eksiksiz tanımlanmalıdır."""
        scenarios = get_default_stress_scenarios()
        assert len(scenarios) >= 4
        scenario_types = {s.scenario_type for s in scenarios}
        assert StressScenarioType.FLASH_CRASH in scenario_types
        assert StressScenarioType.LIQUIDITY_CRUNCH in scenario_types
        assert StressScenarioType.PROLONGED_BEAR in scenario_types
        assert StressScenarioType.VOLATILITY_EXPLOSION in scenario_types

        groups = get_default_symbol_groups()
        assert len(groups) >= 3
        group_types = {g.group_type for g in groups}
        assert SymbolGroupType.BIST30_LIQUID in group_types
        assert SymbolGroupType.SMALL_CAP_ILLIQUID in group_types


class TestDataStressApplication:
    """Veri çerçevesine stres senaryosu uygulama testleri."""

    def test_apply_flash_crash_stress(self) -> None:
        """Flash crash uygulandığında fiyat düşmeli ve hacim artmalıdır."""
        bars = _create_sample_bars(n=30, start_price=100.0, volume=50_000.0)
        scenario = StressScenarioConfig(
            scenario_type=StressScenarioType.FLASH_CRASH,
            name="Flash Crash Test",
            description="Test ani çöküş",
            price_shock_pct=-0.20,
            shock_duration_bars=2,
            volume_multiplier=3.0,
            spread_multiplier=2.5,
            slippage_multiplier=3.0,
            start_bar_index=10,
        )

        stressed_df, friction = apply_stress_to_dataframe(bars, scenario)

        # Fiyat şok uygulanan bar sonrasında belirgin düşmüş olmalı
        assert stressed_df["close"].iloc[12] < bars["close"].iloc[9]
        # Hacim şok barında artmış olmalı
        assert stressed_df["volume"].iloc[10] >= 150_000.0
        # Friksiyon spread ve kayması genişlemiş olmalı
        assert friction.half_spread_pct > 0.0005
        assert friction.slippage_impact_factor > 0.00010

    def test_apply_stress_too_short_df_raises_error(self) -> None:
        """5 bardan kısa DataFrame stres testine sokulamaz."""
        bars = _create_sample_bars(n=3)
        scenario = get_default_stress_scenarios()[0]
        with pytest.raises(ValueError, match="en az 5 bar"):
            apply_stress_to_dataframe(bars, scenario)


class TestStressTestRunner:
    """Stres testi motoru ve raporlama testleri."""

    def test_run_stress_test_generates_valid_report(self) -> None:
        """run_stress_test tüm senaryoları koşmalı ve denetlenebilir rapor üretmelidir."""
        bars = _create_sample_bars(n=40, start_price=100.0)
        # Basit bir al ve tut / %50 pozisyon sinyali
        signals = [0.5] * len(bars)

        report = run_stress_test(
            bars=bars,
            signals=signals,
            symbol="THYAO",
            strategy_name="Simple-Half-Alloc",
        )

        assert isinstance(report, StressTestReport)
        assert report.total_scenarios_count == 4
        assert len(report.scenario_results) == 4
        assert 0.0 <= report.overall_worst_drawdown_pct <= 100.0
        assert 0.0 < report.overall_min_capital_ratio <= 1.5

        # Rapor sözlük ve özet metin çıktıları
        r_dict = report.to_dict()
        assert "overall_worst_drawdown_pct" in r_dict
        assert "scenarios" in r_dict
        scenarios_val = r_dict["scenarios"]
        assert isinstance(scenarios_val, list) and len(scenarios_val) == 4

        summary_text = report.format_summary()
        assert "Stres Özeti" in summary_text
        assert "Yasal Uyarı" in summary_text
        assert "DAYANIKLI (PASS)" in summary_text or "RİSKLİ (FAIL)" in summary_text

    def test_run_symbol_basket_stress_test(self) -> None:
        """Farklı likidite sepetleri için sembol grubu testi doğrulanmalıdır."""
        bars_thyao = _create_sample_bars(n=35, start_price=250.0, volume=500_000.0)
        bars_asels = _create_sample_bars(n=35, start_price=60.0, volume=300_000.0)

        basket_data = {"THYAO": bars_thyao, "ASELS": bars_asels}
        basket_signals = {
            "THYAO": [0.6] * len(bars_thyao),
            "ASELS": [0.4] * len(bars_asels),
        }

        group = SymbolGroupConfig(
            group_type=SymbolGroupType.BIST30_LIQUID,
            name="BIST30 Likit Test Grubu",
            symbols=("THYAO", "ASELS"),
            friction_config=MarketFrictionConfig(
                commission_rate=0.0008,
                bsmv_rate=0.05,
                exchange_fee_rate=0.00004,
                half_spread_pct=0.0003,
                slippage_impact_factor=0.00005,
            ),
            description="Likit sepet testi.",
        )

        report = run_symbol_basket_stress_test(
            basket_data=basket_data,
            basket_signals=basket_signals,
            symbol_group=group,
            strategy_name="Dual-Asset-Alloc",
        )

        assert report.total_scenarios_count == 2
        assert report.scenario_results[0].symbol == "THYAO"
        assert report.scenario_results[1].symbol == "ASELS"
        assert report.resilient_scenarios_count >= 1

    def test_non_compliant_disclaimer_raises_error(self) -> None:
        """Yasaklı veya vaat içeren yasal metin uyum kuralından geçmemelidir."""
        bars = _create_sample_bars(n=20)
        signals = [0.5] * len(bars)
        forbidden_disclaimer = "Bu strateji kesin kâr garantisi vermektedir."

        with pytest.raises(ValueError):
            run_stress_test(
                bars=bars,
                signals=signals,
                disclaimer=forbidden_disclaimer,
            )
