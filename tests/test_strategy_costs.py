"""İşlem maliyeti, spread ve kayma payı simülatörü testleri.

Test senaryoları:
1. Alışta ve satışta spread, kayma (slippage), komisyon, BSMV ve borsa payı hesaplamaları.
3. Sık işlem yapan bir stratejide brüt kârın işlem maliyetleri ile net zarara dönüşmesi.
4. Likiditeye bağlı kayma (slippage) büyüklüğünün pay adedi/hacim oranıyla artması.
5. SPK uyumu: Metin çıktılarında al/sat tavsiyesi, hedef fiyat gibi yasaklı ifadelerin olmaması.

6. Hatalı parametre (negatif fiyat, uyumsuz seri boyutu) doğrulamaları.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.cost_model import (
    ExecutionSimulator,
    MarketFrictionConfig,
    TradeAction,
)


def _create_sample_market_data(
    n: int = 100,
    base_price: float = 100.0,
    daily_volume: float = 100_000.0,
) -> pd.DataFrame:
    """Belirli boyut ve hacimde sentetik fiyat verisi üretir."""
    np.random.seed(42)
    start_date = datetime(2026, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(n)]

    prices = [base_price]
    for _ in range(1, n):
        ret = np.random.normal(0, 0.01)
        prices.append(max(1.0, prices[-1] * (1.0 + ret)))

    df = pd.DataFrame(index=dates)
    df["close"] = prices
    df["volume"] = [daily_volume for _ in range(n)]
    return df


class TestExecutionPriceCalculations:
    """Tekil işlem fiyatı ve sürtünme hesaplama testleri."""

    def test_buy_execution_applies_upward_friction(self) -> None:
        """Alış işleminde gerçekleşme fiyatı spread ve kayma ile nominalden yüksek olmalıdır."""
        simulator = ExecutionSimulator(
            MarketFrictionConfig(
                commission_rate=0.002,
                bsmv_rate=0.05,
                exchange_fee_rate=0.00005,
                half_spread_pct=0.001,
            )
        )
        detail = simulator.calculate_execution_price(
            nominal_price=100.0,
            action=TradeAction.ENTRY,
            order_shares=1_000.0,
            daily_volume=100_000.0,
        )

        assert detail.nominal_price == 100.0
        assert detail.effective_price > 100.0
        assert detail.commission_try > 0.0
        assert detail.bsmv_try > 0.0
        assert detail.exchange_fee_try > 0.0
        assert detail.total_friction_try > 0.0
        assert detail.effective_friction_pct > 0.2

    def test_sell_execution_applies_downward_friction(self) -> None:
        """Satış işleminde gerçekleşme fiyatı spread ve kayma ile nominalden düşük olmalıdır."""
        simulator = ExecutionSimulator()
        detail = simulator.calculate_execution_price(
            nominal_price=100.0,
            action=TradeAction.EXIT,
            order_shares=500.0,
            daily_volume=50_000.0,
        )

        assert detail.effective_price < 100.0
        assert detail.spread_cost_try > 0.0
        assert detail.slippage_cost_try > 0.0

    def test_higher_order_share_causes_greater_slippage(self) -> None:
        """Büyük emir büyüklüğü günlük hacme oranla daha yüksek kaymaya (slippage) yol açmalıdır."""
        simulator = ExecutionSimulator()

        small_order = simulator.calculate_execution_price(
            nominal_price=50.0,
            action=TradeAction.ENTRY,
            order_shares=100.0,
            daily_volume=100_000.0,
        )
        large_order = simulator.calculate_execution_price(
            nominal_price=50.0,
            action=TradeAction.ENTRY,
            order_shares=20_000.0,
            daily_volume=100_000.0,
        )

        assert large_order.slippage_cost_try > small_order.slippage_cost_try
        assert large_order.effective_price > small_order.effective_price

    def test_invalid_parameters_raise_error(self) -> None:
        """Sıfır veya negatif parametrelerde hata fırlatılmalıdır."""
        simulator = ExecutionSimulator()
        with pytest.raises(ValueError, match="pozitif olmalıdır"):
            simulator.calculate_execution_price(
                nominal_price=-10.0,
                action=TradeAction.ENTRY,
                order_shares=100.0,
            )

        with pytest.raises(ValueError, match="pozitif olmalıdır"):
            simulator.calculate_execution_price(
                nominal_price=10.0,
                action=TradeAction.ENTRY,
                order_shares=0.0,
            )


class TestStrategyFrictionEvaluation:
    """Strateji düzeyi maliyet ve gerçekçilik testleri."""

    def test_frictionless_results_are_marked_not_realistic(self) -> None:
        """İşlem maliyeti olmadan yapılan değerlendirme kesinlikle gerçekçi kabul edilemez."""
        df = _create_sample_market_data(n=50)
        signals = pd.Series([1 if i % 2 == 0 else 0 for i in range(50)], index=df.index)

        simulator = ExecutionSimulator()
        result = simulator.evaluate_strategy_friction(
            df=df,
            signals=signals,
            enable_frictions=False,
        )

        assert result.is_realistic is False
        assert result.frictions_applied is False
        assert any(
            "gerçekçi bir piyasa performansını temsil etmez" in n for n in result.validity_notes
        )

    def test_churn_trap_where_gross_profit_becomes_net_loss(self) -> None:
        """Sık işlem yapan stratejide brüt kârın maliyetler nedeniyle net zarara dönüşmesi."""
        # Her gün %0.15 yükselen ama sürekli al-sat yapılan bir seri kurgulayalım
        n = 40
        start_date = datetime(2026, 1, 1)
        dates = [start_date + timedelta(days=i) for i in range(n)]

        prices = [100.0 * (1.0015**i) for i in range(n)]
        df = pd.DataFrame({"close": prices, "volume": [100_000.0] * n}, index=dates)

        # Sürekli 1 bar gir 1 bar çık (aşırı al-sat / churning)
        signals = pd.Series([1 if i % 2 == 1 else 0 for i in range(n)], index=dates)

        simulator = ExecutionSimulator(
            MarketFrictionConfig(
                commission_rate=0.002,
                half_spread_pct=0.001,
            )
        )
        res = simulator.evaluate_strategy_friction(df, signals=signals)

        assert res.is_realistic is True
        assert res.frictions_applied is True
        assert res.friction_drag_pct > 0.0
        # Komisyon ve makas toplamı brüt getiriyi eritmiş olmalıdır
        assert res.net_return_pct < res.gross_return_pct
        assert res.total_trades > 5

    def test_mismatched_series_length_rejected(self) -> None:
        """Veri boyutu ve sinyal boyutu uyuşmadığında ValueError verilmelidir."""
        df = _create_sample_market_data(n=30)
        signals = pd.Series([1] * 20)

        simulator = ExecutionSimulator()
        with pytest.raises(ValueError, match="uzunlukları eşit olmalıdır"):
            simulator.evaluate_strategy_friction(df, signals=signals)


class TestComplianceAndDisclaimers:
    """SPK ve yasal uyum denetimleri."""

    def test_summary_and_notes_contain_no_prohibited_terms(self) -> None:
        """Rapor metinlerinde kesinlik ve yatırım tavsiyesi içeren yasaklı sözcük olmamalıdır."""
        df = _create_sample_market_data(n=40)
        signals = pd.Series([1] * 40, index=df.index)

        simulator = ExecutionSimulator()
        result = simulator.evaluate_strategy_friction(df, signals=signals)

        all_notes = " ".join(result.validity_notes)
        combined_text = f"{result.summary_assessment} {all_notes} {result.disclaimer}"
        lower_text = combined_text.lower()

        forbidden_list = [
            " al ",
            " sat ",
            "al-sat tavsiyesi ver",
            "hedef fiyat",
            "garanti",
            "kesin getiri",
        ]
        for term in forbidden_list:
            assert term not in lower_text
        has_disclaimer = (
            "genel bilgilendirme" in result.disclaimer
            or "yalnızca işlem maliyetlerinin" in result.disclaimer
        )
        assert has_disclaimer
