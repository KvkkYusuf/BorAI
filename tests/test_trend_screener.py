"""Trend motoru, çoklu gösterge teyidi ve filtreleme testleri.

Test senaryoları:
1. Yetersiz bar sayısı veya eksik sütunda filtreleme hatası.
2. Düşük likidite ve yetersiz TL hacminde tarama dışı bırakma.
3. Aşırı oynaklık durumunda filtreleme.
4. Tek bir gösterge kuralı sağlandığında sinyal üretmeme (tek gösterge engeli / savunması).
5. Çoklu bağımsız gösterge teyidi sağlandığında ölçülü eğilim sinyali üretimi.
6. Yasaklı ifadelerin ("al", "sat", "hedef fiyat", vb.) ASLA üretilmediğinin doğrulanması.
7. Sinyalin geçersizleşme kriterlerinin açıkça raporlanması.
8. Deterministik ve tekrar üretilebilir sonuç hash doğrulaması.
9. Cooldown mekanizmasının çalışması.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.trend import (
    QualityLiquidityFilterConfig,
    TrendDirection,
    TrendEngine,
    validate_compliant_text,
)


def _generate_synthetic_bars(
    n: int = 100,
    base_price: float = 100.0,
    trend_type: str = "uptrend",
    daily_volume: float = 50_000.0,
    volatility: float = 0.01,
) -> pd.DataFrame:
    """Belirli trend ve oynaklık özellikleriyle sentetik OHLCV serisi üretir."""
    np.random.seed(42)
    start_date = datetime(2026, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(n)]

    prices = [base_price]
    for _ in range(1, n):
        drift = 0.005 if trend_type == "uptrend" else (-0.005 if trend_type == "downtrend" else 0.0)
        ret = drift + np.random.normal(0, volatility)
        prices.append(max(1.0, prices[-1] * (1.0 + ret)))

    df = pd.DataFrame(index=dates)
    df["close"] = prices
    df["open"] = [p * (1.0 + np.random.normal(0, 0.002)) for p in prices]
    df["high"] = [
        max(o, c) * (1.0 + abs(np.random.normal(0, 0.003)))
        for o, c in zip(df["open"], df["close"], strict=True)
    ]
    df["low"] = [
        min(o, c) * (1.0 - abs(np.random.normal(0, 0.003)))
        for o, c in zip(df["open"], df["close"], strict=True)
    ]
    df["volume"] = [max(100.0, daily_volume * (1.0 + np.random.normal(0, 0.1))) for _ in range(n)]

    return df


class TestTrendFilters:
    """Likidite, oynaklık ve veri bütünlüğü filtre testleri."""

    def test_insufficient_bars_rejection(self) -> None:
        """Yetersiz bar sayısında filtre reddetmelidir."""
        df = _generate_synthetic_bars(n=30)
        engine = TrendEngine()
        result = engine.evaluate(df, symbol="THYAO")

        assert not result.filter_result.passed
        assert result.direction == TrendDirection.NEUTRAL
        assert any("Yetersiz bar sayısı" in f for f in result.filter_result.failures)

    def test_low_volume_and_turnover_rejection(self) -> None:
        """Düşük işlem hacmi ve TL büyüklüğünde filtreden geçmemelidir."""
        df = _generate_synthetic_bars(n=60, daily_volume=500.0, base_price=10.0)
        engine = TrendEngine(
            filter_config=QualityLiquidityFilterConfig(
                min_average_volume_20=10_000.0,
                min_average_turnover_try=500_000.0,
            )
        )
        result = engine.evaluate(df, symbol="SPEC")

        assert not result.filter_result.passed
        assert result.direction == TrendDirection.NEUTRAL
        assert any("Yetersiz işlem hacmi" in f for f in result.filter_result.failures)
        assert any("Yetersiz TL işlem büyüklüğü" in f for f in result.filter_result.failures)

    def test_excessive_volatility_rejection(self) -> None:
        """Aşırı oynaklık eşiğini aşan hisseler elenmelidir."""
        df = _generate_synthetic_bars(n=60, volatility=0.15)  # Çok yüksek oynaklık
        engine = TrendEngine(filter_config=QualityLiquidityFilterConfig(max_volatility=0.50))
        result = engine.evaluate(df, symbol="VOLA")

        assert not result.filter_result.passed
        assert any("Aşırı oynaklık" in f for f in result.filter_result.failures)

    def test_invalid_ohlc_rejection(self) -> None:
        """Tutarsız OHLC serisi (high < low) reddedilmelidir."""
        df = _generate_synthetic_bars(n=60)
        last_idx = df.index[-1]
        df.loc[last_idx, "high"] = float(df.loc[last_idx, "low"]) - 1.0  # Hatalı veri
        engine = TrendEngine()
        result = engine.evaluate(df, symbol="ERR")

        assert not result.filter_result.passed
        assert any("Veri kalitesi hatası" in f for f in result.filter_result.failures)


class TestSingleIndicatorDefense:
    """Tek göstergeyi sinyal saymama prensibinin testleri."""

    def test_single_or_insufficient_rules_produces_no_signal(self) -> None:
        """En az 4 kural ve 3 bağımsız kategori sağlanmadığında sinyal üretilmemelidir."""
        df = _generate_synthetic_bars(n=60, trend_type="flat")
        engine = TrendEngine(min_rules_required=5, min_categories_required=3)
        result = engine.evaluate(df, symbol="TEST")

        assert result.direction == TrendDirection.NEUTRAL
        assert "Yetersiz teyit" in result.headline or "Belirgin bir trend" in result.headline
        assert (
            "Tek veya sınırlı gösterge sinyal sayılmaz" in result.headline
            or "Belirgin bir trend" in result.headline
        )

    def test_single_category_confirmation_is_rejected(self) -> None:
        """Birden çok kural sağlansa bile tek kategoriden geliyorsa sinyal reddedilmelidir."""
        df = _generate_synthetic_bars(n=80, trend_type="uptrend")
        engine = TrendEngine(min_rules_required=2, min_categories_required=3)
        result = engine.evaluate(df, symbol="TEST_CAT")

        if len(result.met_categories) < 3:
            assert result.direction == TrendDirection.NEUTRAL
            assert "Yetersiz teyit" in result.headline


class TestMultiIndicatorTrendEvaluation:
    """Çoklu kural sağlandığında ölçülü eğilim üretimi."""

    def test_bullish_tendency_generation(self) -> None:
        """Yükseliş trendinde çoklu gösterge teyidi ile BULLISH_TENDENCY üretilmelidir."""
        df = _generate_synthetic_bars(
            n=80, trend_type="uptrend", daily_volume=100_000.0, base_price=50.0
        )
        engine = TrendEngine(min_rules_required=3, min_categories_required=2)
        result = engine.evaluate(df, symbol="GARAN")

        assert result.filter_result.passed
        assert result.direction == TrendDirection.BULLISH_TENDENCY
        assert "olasılığı arttı" in result.headline
        assert len(result.met_rules) >= 3
        assert len(result.met_categories) >= 2
        assert len(result.invalidation_criteria) > 0

    def test_bearish_tendency_generation(self) -> None:
        """Düşüş trendi verisinde BEARISH_TENDENCY üretilmelidir."""
        df = _generate_synthetic_bars(
            n=80, trend_type="downtrend", daily_volume=100_000.0, base_price=200.0
        )
        engine = TrendEngine(min_rules_required=3, min_categories_required=2)
        result = engine.evaluate(df, symbol="AKBNK")

        assert result.filter_result.passed
        assert result.direction == TrendDirection.BEARISH_TENDENCY
        assert "düşüş eğilimi olasılığı arttı" in result.headline
        assert len(result.met_rules) >= 3
        assert len(result.invalidation_criteria) > 0


class TestLegalComplianceAndLanguage:
    """Yasal uyum, ölçülü dil ve yasaklı sözcük denetimleri."""

    def test_forbidden_words_validator(self) -> None:
        """Yasaklı ifadeler içerildiğinde hata fırlatılmalıdır."""
        with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
            validate_compliant_text("Bu hisseyi şimdi al ve bekle")

        with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
            validate_compliant_text("Hisse için hedef fiyat 150 TL")

        with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
            validate_compliant_text("Bu sistemde kesinlikle kayıp olmaz")

    def test_result_text_contains_no_prohibited_terms(self) -> None:
        """Üretilen tüm başlık, açıklama ve geçersizleşme metinleri temiz olmalıdır."""
        df = _generate_synthetic_bars(n=70, trend_type="uptrend")
        engine = TrendEngine()
        result = engine.evaluate(df, symbol="SISE")

        full_text = (
            f"{result.headline} {result.explanation} {' '.join(result.invalidation_criteria)}"
        )
        lower_text = full_text.lower()
        forbidden_list = [
            " al ",
            " sat ",
            " tut ",
            "al-sat",
            "hedef fiyat",
            "kesinlikle",
            "garanti",
        ]
        for forbidden in forbidden_list:
            assert forbidden not in lower_text


class TestReproducibilityAndCooldown:
    """Tekrar üretilebilirlik ve sinyal soğuma (cooldown) testleri."""

    def test_reproducibility_hash_consistency(self) -> None:
        """Aynı veri kesitiyle yapılan analiz daima aynı hash çıktısını üretmelidir."""
        df = _generate_synthetic_bars(n=70, trend_type="uptrend")
        engine = TrendEngine()

        res1 = engine.evaluate(df, symbol="BIMAS", as_of_date="2026-03-10")
        res2 = engine.evaluate(df, symbol="BIMAS", as_of_date="2026-03-10")

        assert res1.reproducibility_hash == res2.reproducibility_hash
        assert res1.direction == res2.direction
        assert res1.met_rules == res2.met_rules

    def test_cooldown_detection(self) -> None:
        """Belirli periyot içinde tekrarlayan sinyallerde cooldown aktif olmalıdır."""
        df = _generate_synthetic_bars(n=70, trend_type="uptrend")
        engine = TrendEngine(cooldown_bars=5)

        recent_date = str(df.index[-2])
        res = engine.evaluate(df, symbol="TUPRS", previous_signal_date=recent_date)

        assert res.cooldown_active is True

    def test_scan_universe(self) -> None:
        """Evren taraması birden çok hisseyi sıralı ve hatasız analiz etmelidir."""
        universe = {
            "GARAN": _generate_synthetic_bars(n=60, trend_type="uptrend"),
            "THYAO": _generate_synthetic_bars(n=60, trend_type="downtrend"),
            "SPEC": _generate_synthetic_bars(n=60, daily_volume=100.0),
        }
        engine = TrendEngine()
        results = engine.scan_universe(universe)

        assert len(results) == 3
        symbols = [r.symbol for r in results]
        assert symbols == ["GARAN", "SPEC", "THYAO"]
