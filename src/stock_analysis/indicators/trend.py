"""Trend koşulları analizi, çoklu gösterge teyit motoru ve kalite/likidite filtreleri.

Bu modül, SPK ve yasal uyum kırmızı çizgilerine uygun olarak çalışır:
- Tek bir göstergeyi ASLA kesin veya yeterli sinyal saymaz; bağımsız gösterge teyidi zorunludur.
- Kesin hüküm ("yükseliş başladı", "al", "sat") içermez; ölçülü eğilim dili kullanır.
- Yetersiz veri kalitesi, hacim veya likiditeye sahip sembolleri otomatik olarak eler.
- Sinyal geçersizleşme koşullarını (invalidation) açıkça belirtir.
- Cooldown ve tekilleştirme (deduplication) mekanizması ile tekrarları önler.
- Deterministik ve tekrar üretilebilirdir.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from stock_analysis.indicators.technical import calculate_all_indicators


class TrendDirection(StrEnum):
    """Trend eğilim yönü (asla al/sat veya kesin yön bildirmez)."""

    BULLISH_TENDENCY = "BULLISH_TENDENCY"  # Tanımlı koşullara göre yükseliş eğilimi olasılığı arttı
    BEARISH_TENDENCY = "BEARISH_TENDENCY"  # Tanımlı koşullara göre düşüş eğilimi olasılığı arttı
    NEUTRAL = "NEUTRAL"  # Belirgin bir eğilim saptanmadı veya teyit yetersiz


class IndicatorCategory(StrEnum):
    """Gösterge bağımsızlık kategorileri."""

    TREND = "TREND"  # Hareketli ortalamalar (SMA, EMA vb.)
    MOMENTUM = "MOMENTUM"  # RSI, MACD
    VOLATILITY = "VOLATILITY"  # ATR, Tarihsel Oynaklık
    VOLUME = "VOLUME"  # İşlem hacmi ortalamaları


# Kesinlikle yasaklı sözcükler (finans-hukuk.md ve DISCLAIMER_TR.md uyumu)
PROHIBITED_TERMS: tuple[str, ...] = (
    " al ",
    " sat ",
    " tut ",
    "al-sat",
    "al/sat",
    "hedef fiyat",
    "kesinlikle",
    "garanti",
    "mutlaka",
    "şimdi gir",
    "riski yok",
    "risksiz",
)


def validate_compliant_text(text: str) -> None:
    """Metnin SPK uyumluluk kırmızı çizgilerini ihlal etmediğini doğrular."""
    normalized = f" {text.lower()} "
    for term in PROHIBITED_TERMS:
        if term in normalized:
            raise ValueError(f"Yasaklı finansal tavsiye/kesinlik ifadesi tespit edildi: '{term}'")


@dataclass(frozen=True)
class QualityLiquidityFilterConfig:
    """Likidite, oynaklık ve veri kalitesi filtreleme eşikleri."""

    min_bars: int = 50
    min_average_volume_20: float = 10_000.0
    min_average_turnover_try: float = 500_000.0  # Ortalama işlem hacmi (TL)
    max_volatility: float = 1.20  # Yıllıklandırılmış aşırı oynaklık tavanı (%120)
    min_volatility: float = 0.001  # Donuk tahta tabanı
    require_valid_ohlc: bool = True


@dataclass(frozen=True)
class FilterResult:
    """Veri kalitesi ve likidite filtreleme sonucu."""

    passed: bool
    failures: tuple[str, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ConditionRule:
    """Belirli bir gösterge koşulu kuralı."""

    rule_id: str
    name: str
    category: IndicatorCategory
    direction: TrendDirection
    description: str
    invalidation_criteria: str


@dataclass(frozen=True)
class TrendSignalResult:
    """Trend tarama ve analiz çıktısı."""

    symbol: str
    evaluated_at: str
    direction: TrendDirection
    headline: str
    explanation: str
    invalidation_criteria: tuple[str, ...]
    met_rules: tuple[str, ...]
    met_categories: tuple[str, ...]
    filter_result: FilterResult
    cooldown_active: bool
    reproducibility_hash: str
    disclaimer: str

    def __post_init__(self) -> None:
        """Tüm metin çıktılarının yasal uyumluluğunu zorunlu kontrol eder."""
        validate_compliant_text(self.headline)
        validate_compliant_text(self.explanation)
        for crit in self.invalidation_criteria:
            validate_compliant_text(crit)


class TrendEngine:
    """Açık, yapılandırılabilir ve açıklanabilir trend analiz motoru."""

    DEFAULT_DISCLAIMER = (
        "Bu sayfadaki bilgiler genel bilgilendirme amaçlıdır; yatırım danışmanlığı veya al-sat "
        "tavsiyesi değildir. Tanımlı gösterge koşullarına göre eğilim olasılığı analizi "
        "yapılmıştır. Geçmiş performans gelecekteki sonuçların göstergesi değildir."
    )

    def __init__(
        self,
        filter_config: QualityLiquidityFilterConfig | None = None,
        min_rules_required: int = 3,
        min_categories_required: int = 2,
        cooldown_bars: int = 5,
    ) -> None:
        """Motor yapılandırması.

        Args:
            filter_config: Kalite ve likidite filtre ayarları.
            min_rules_required: Sinyal üretimi için asgari kural sayısı (tek gösterge engeli).
            min_categories_required: Gereken asgari bağımsız gösterge kategorisi sayısı.
            cooldown_bars: Tekrar eden sinyaller için soğuma çubuğu sayısı.
        """
        if min_rules_required < 2:
            raise ValueError("Tek göstergeyi sinyal saymamak için en az 2 kural zorunludur.")
        if min_categories_required < 2:
            raise ValueError("Bağımsız teyit için en az 2 gösterge kategorisi zorunludur.")

        self.filter_config = filter_config or QualityLiquidityFilterConfig()
        self.min_rules_required = min_rules_required
        self.min_categories_required = min_categories_required
        self.cooldown_bars = cooldown_bars

    def apply_filters(self, df: pd.DataFrame, symbol: str) -> FilterResult:
        """Likidite, işlem hacmi, oynaklık ve veri bütünlüğü filtrelerini denetler."""
        failures: list[str] = []
        metrics: dict[str, float] = {}

        if len(df) < self.filter_config.min_bars:
            failures.append(
                f"Yetersiz bar sayısı: {len(df)} bar mevcut, "
                f"en az {self.filter_config.min_bars} gereklidir."
            )
            return FilterResult(passed=False, failures=tuple(failures), metrics=metrics)

        # Temel OHLCV sütun varlığı ve eksik veri kontrolü
        required_cols = ["open", "high", "low", "close", "volume"]
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            failures.append(f"Eksik veri sütunları: {', '.join(missing_cols)}")
            return FilterResult(passed=False, failures=tuple(failures), metrics=metrics)

        last_bars = df.tail(self.filter_config.min_bars)

        # OHLC ilişkisi ve sıfır/negatif kontrolü
        if self.filter_config.require_valid_ohlc:
            invalid_prices = (
                (last_bars["close"] <= 0)
                | (last_bars["high"] <= 0)
                | (last_bars["low"] <= 0)
                | (last_bars["open"] <= 0)
                | (last_bars["high"] < last_bars["low"])
                | (last_bars["high"] < last_bars["close"])
                | (last_bars["low"] > last_bars["close"])
            )
            if invalid_prices.any():
                failures.append(
                    "Veri kalitesi hatası: Fiyat serisinde tutarsız veya negatif/sıfır OHLC."
                )

        # Hacim kontrolleri
        avg_volume_20 = float(last_bars["volume"].tail(20).mean())
        metrics["avg_volume_20"] = avg_volume_20
        if avg_volume_20 < self.filter_config.min_average_volume_20:
            failures.append(
                f"Yetersiz işlem hacmi: 20 günlük ortalama {avg_volume_20:.0f} adet, "
                f"eşik {self.filter_config.min_average_volume_20:.0f}."
            )

        # TL Hacim / Turnover kontrolü (ortalama fiyat * hacim)
        avg_turnover = float((last_bars["close"] * last_bars["volume"]).tail(20).mean())
        metrics["avg_turnover_try"] = avg_turnover
        if avg_turnover < self.filter_config.min_average_turnover_try:
            failures.append(
                f"Yetersiz TL işlem büyüklüğü: Ortalama {avg_turnover:,.0f} TL, "
                f"eşik {self.filter_config.min_average_turnover_try:,.0f} TL."
            )

        # Oynaklık kontrolleri
        close_series = pd.Series(last_bars["close"], dtype=float)
        log_ret = pd.Series(np.log(close_series / close_series.shift(1))).dropna()
        annualized_vol = float(log_ret.tail(20).std() * np.sqrt(252)) if len(log_ret) >= 20 else 0.0
        metrics["annualized_volatility"] = annualized_vol

        if annualized_vol > self.filter_config.max_volatility:
            failures.append(
                f"Aşırı oynaklık: Yıllık oynaklık %{annualized_vol * 100:.1f}, "
                f"azami kabul edilebilir eşik %{self.filter_config.max_volatility * 100:.1f}."
            )
        elif annualized_vol < self.filter_config.min_volatility:
            failures.append(
                f"Donuk piyasa (yetersiz oynaklık): Oynaklık %{annualized_vol * 100:.2f}, "
                f"asgari eşik %{self.filter_config.min_volatility * 100:.2f}."
            )

        return FilterResult(
            passed=len(failures) == 0,
            failures=tuple(failures),
            metrics=metrics,
        )

    def evaluate(
        self,
        df: pd.DataFrame,
        symbol: str,
        as_of_date: str | None = None,
        previous_signal_date: str | None = None,
    ) -> TrendSignalResult:
        """Belirtilen hisse verisi için trend koşullarını değerlendirir.

        Args:
            df: OHLCV zaman serisi verisi.
            symbol: Hisse senedi kodu.
            as_of_date: Analiz tarihi (None ise son bar tarihi).
            previous_signal_date: En son üretilmiş sinyal tarihi (cooldown tespiti için).
        """
        filter_result = self.apply_filters(df, symbol)
        eval_time = as_of_date or str(df.index[-1])

        # Filtreleri geçemezse doğrudan NEUTRAL ve filtre gerekçesi döndürülür
        if not filter_result.passed:
            fail_reasons = "; ".join(filter_result.failures)
            explanation = f"Likidite ve veri kalitesi kriterleri sağlanamadı: {fail_reasons}"
            return TrendSignalResult(
                symbol=symbol,
                evaluated_at=eval_time,
                direction=TrendDirection.NEUTRAL,
                headline="Filtre kriterleri nedeniyle trend analizi kapsamı dışı.",
                explanation=explanation,
                invalidation_criteria=(
                    "Likidite ve veri bütünlüğü koşulları sağlandığında yeniden değerlendirilir.",
                ),
                met_rules=(),
                met_categories=(),
                filter_result=filter_result,
                cooldown_active=False,
                reproducibility_hash=self._calculate_hash(
                    symbol, eval_time, TrendDirection.NEUTRAL, ()
                ),
                disclaimer=self.DEFAULT_DISCLAIMER,
            )

        # İndikatörleri hesapla
        enriched = calculate_all_indicators(df, symbol=symbol)
        curr = enriched.iloc[-1]

        # Kural havuzunu değerlendir
        bullish_rules: list[ConditionRule] = []
        bearish_rules: list[ConditionRule] = []

        # 1. Kural: EMA 20 ve Fiyat İlişkisi (TREND)
        close_p = float(curr["close"])
        ema20 = float(curr["ema_20"]) if curr.get("ema_20") is not None else np.nan
        sma50 = float(curr["sma_50"]) if curr.get("sma_50") is not None else np.nan

        if not np.isnan(ema20) and not np.isnan(close_p):
            if close_p > ema20:
                bullish_rules.append(
                    ConditionRule(
                        rule_id="PRICE_ABOVE_EMA20",
                        name="Fiyat EMA 20 Üzerinde",
                        category=IndicatorCategory.TREND,
                        direction=TrendDirection.BULLISH_TENDENCY,
                        description=(
                            f"Son kapanış ({close_p:.2f}), "
                            f"20 günlük üstel ortalamanın ({ema20:.2f}) üzerindedir."
                        ),
                        invalidation_criteria=(
                            "Fiyatın 20 günlük EMA seviyesi altına inmesi halinde geçersizleşir."
                        ),
                    )
                )
            elif close_p < ema20:
                bearish_rules.append(
                    ConditionRule(
                        rule_id="PRICE_BELOW_EMA20",
                        name="Fiyat EMA 20 Altında",
                        category=IndicatorCategory.TREND,
                        direction=TrendDirection.BEARISH_TENDENCY,
                        description=(
                            f"Son kapanış ({close_p:.2f}), "
                            f"20 günlük üstel ortalamanın ({ema20:.2f}) altındadır."
                        ),
                        invalidation_criteria=(
                            "Fiyatın 20 günlük EMA seviyesi üzerine çıkması halinde geçersizleşir."
                        ),
                    )
                )

        # 2. Kural: EMA 20 / SMA 50 Hizalanması (TREND)
        if not np.isnan(ema20) and not np.isnan(sma50):
            if ema20 > sma50:
                bullish_rules.append(
                    ConditionRule(
                        rule_id="EMA20_ABOVE_SMA50",
                        name="Kısa Vadeli Ortalama Uzun Vadelinin Üzerinde",
                        category=IndicatorCategory.TREND,
                        direction=TrendDirection.BULLISH_TENDENCY,
                        description=(
                            f"20 günlük EMA ({ema20:.2f}), "
                            f"50 günlük SMA ({sma50:.2f}) üzerinde yer almaktadır."
                        ),
                        invalidation_criteria=(
                            "Kısa vadeli EMA'nın 50 günlük SMA altına sarkması durumunda "
                            "geçersizleşir."
                        ),
                    )
                )
            elif ema20 < sma50:
                bearish_rules.append(
                    ConditionRule(
                        rule_id="EMA20_BELOW_SMA50",
                        name="Kısa Vadeli Ortalama Uzun Vadelinin Altında",
                        category=IndicatorCategory.TREND,
                        direction=TrendDirection.BEARISH_TENDENCY,
                        description=(
                            f"20 günlük EMA ({ema20:.2f}), "
                            f"50 günlük SMA ({sma50:.2f}) altında yer almaktadır."
                        ),
                        invalidation_criteria=(
                            "Kısa vadeli EMA'nın 50 günlük SMA üzerine çıkması durumunda "
                            "geçersizleşir."
                        ),
                    )
                )

        # 3. Kural: RSI Momentum Aralığı (MOMENTUM)
        rsi = float(curr["rsi_14"]) if curr.get("rsi_14") is not None else np.nan
        if not np.isnan(rsi):
            if 50.0 <= rsi <= 80.0:
                bullish_rules.append(
                    ConditionRule(
                        rule_id="RSI_HEALTHY_BULLISH_MOMENTUM",
                        name="RSI Pozitif Momentum Bölgesinde",
                        category=IndicatorCategory.MOMENTUM,
                        direction=TrendDirection.BULLISH_TENDENCY,
                        description=(
                            f"14 günlük RSI ({rsi:.1f}) pozitif momentum bölgesi "
                            "olan 50-80 bandındadır."
                        ),
                        invalidation_criteria=(
                            "RSI göstergesinin 50 referans çizgisinin altına inmesi "
                            "halinde geçersizleşir."
                        ),
                    )
                )
            elif rsi < 50.0:
                bearish_rules.append(
                    ConditionRule(
                        rule_id="RSI_BEARISH_MOMENTUM",
                        name="RSI Zayıf Momentum Bölgesinde",
                        category=IndicatorCategory.MOMENTUM,
                        direction=TrendDirection.BEARISH_TENDENCY,
                        description=(
                            f"14 günlük RSI ({rsi:.1f}) negatif momentum bölgesi olan "
                            "50 referans seviyesinin altındadır."
                        ),
                        invalidation_criteria=(
                            "RSI göstergesinin 50 referans çizgisinin üzerine çıkması "
                            "halinde geçersizleşir."
                        ),
                    )
                )

        # 4. Kural: MACD Histogram ve Çizgi Teyidi (MOMENTUM)
        macd_line = float(curr["macd_line"]) if curr.get("macd_line") is not None else np.nan
        macd_sig = float(curr["macd_signal"]) if curr.get("macd_signal") is not None else np.nan
        macd_hist = float(curr["macd_hist"]) if curr.get("macd_hist") is not None else np.nan

        if not np.isnan(macd_hist) and not np.isnan(macd_line) and not np.isnan(macd_sig):
            if macd_hist > 0 and macd_line > macd_sig:
                bullish_rules.append(
                    ConditionRule(
                        rule_id="MACD_POSITIVE_EXPANSION",
                        name="MACD Pozitif Kesişim ve Histogram",
                        category=IndicatorCategory.MOMENTUM,
                        direction=TrendDirection.BULLISH_TENDENCY,
                        description=(
                            f"MACD çizgisi sinyal hattının üzerinde ve "
                            f"histogram ({macd_hist:.3f}) pozitiftir."
                        ),
                        invalidation_criteria=(
                            "MACD histogramının sıfır altına inmesi halinde geçersizleşir."
                        ),
                    )
                )
            elif macd_hist < 0 and macd_line < macd_sig:
                bearish_rules.append(
                    ConditionRule(
                        rule_id="MACD_NEGATIVE_EXPANSION",
                        name="MACD Negatif Kesişim ve Histogram",
                        category=IndicatorCategory.MOMENTUM,
                        direction=TrendDirection.BEARISH_TENDENCY,
                        description=(
                            f"MACD çizgisi sinyal hattının altında ve "
                            f"histogram ({macd_hist:.3f}) negatiftir."
                        ),
                        invalidation_criteria=(
                            "MACD histogramının sıfır üzerine çıkması halinde geçersizleşir."
                        ),
                    )
                )

        # 5. Kural: Hacim Genişlemesi ve Ortalama Teyidi (VOLUME)
        vol = float(curr["volume"]) if curr.get("volume") is not None else np.nan
        vol_sma20 = (
            float(curr["volume_sma_20"]) if curr.get("volume_sma_20") is not None else np.nan
        )
        if not np.isnan(vol) and not np.isnan(vol_sma20) and vol_sma20 > 0:
            if vol >= vol_sma20:
                bullish_rules.append(
                    ConditionRule(
                        rule_id="VOLUME_ABOVE_AVERAGE",
                        name="İşlem Hacmi 20 Günlük Ortalamanın Üzerinde",
                        category=IndicatorCategory.VOLUME,
                        direction=TrendDirection.BULLISH_TENDENCY,
                        description=(
                            f"Son bar işlem hacmi ({vol:,.0f}), "
                            f"20 günlük ortalamasını ({vol_sma20:,.0f}) desteklemektedir."
                        ),
                        invalidation_criteria=(
                            "İşlem hacminin belirgin şekilde ortalamanın altına "
                            "gerilemesi halinde geçersizleşir."
                        ),
                    )
                )

        # Karar ve Tek Gösterge Savunması Değerlendirmesi
        bullish_cats = {r.category for r in bullish_rules}
        bearish_cats = {r.category for r in bearish_rules}

        final_direction = TrendDirection.NEUTRAL
        active_rules: list[ConditionRule] = []

        if (
            len(bullish_rules) >= self.min_rules_required
            and len(bullish_cats) >= self.min_categories_required
        ):
            final_direction = TrendDirection.BULLISH_TENDENCY
            active_rules = bullish_rules
        elif (
            len(bearish_rules) >= self.min_rules_required
            and len(bearish_cats) >= self.min_categories_required
        ):
            final_direction = TrendDirection.BEARISH_TENDENCY
            active_rules = bearish_rules

        # Sinyal tekrarı ve soğuma (cooldown) denetimi
        cooldown_active = False
        if previous_signal_date is not None and final_direction != TrendDirection.NEUTRAL:
            try:
                curr_dt = pd.to_datetime(eval_time)
                prev_dt = pd.to_datetime(previous_signal_date)
                bars_since = len(df.loc[prev_dt:curr_dt])
                if bars_since <= self.cooldown_bars:
                    cooldown_active = True
            except Exception:
                cooldown_active = False

        # Başlık ve açıklama (SPK ölçülü dil ve hukuki kurallar)
        if final_direction == TrendDirection.BULLISH_TENDENCY:
            headline = "Tanımlı kurallara göre yükseliş eğilimi olasılığı arttı."
            explanation = (
                f"Analiz edilen {len(active_rules)} kural ve {len(bullish_cats)} kategori "
                "birlikte pozitif eğilim koşullarını teyit etmektedir."
            )
        elif final_direction == TrendDirection.BEARISH_TENDENCY:
            headline = "Tanımlı kurallara göre düşüş eğilimi olasılığı arttı."
            explanation = (
                f"Analiz edilen {len(active_rules)} kural ve {len(bearish_cats)} kategori "
                "birlikte negatif eğilim koşullarını teyit etmektedir."
            )
        else:
            tot_bull = len(bullish_rules)
            tot_bear = len(bearish_rules)
            if tot_bull > 0 or tot_bear > 0:
                headline = "Yetersiz teyit: Tek veya sınırlı gösterge sinyal sayılmaz."
                explanation = (
                    f"Yetersiz gösterge ({tot_bull} pozitif, {tot_bear} negatif) tespit edildi. "
                    f"En az {self.min_rules_required} kural ve {self.min_categories_required} "
                    "kategori teyidi olmadan eğilim sinyali üretilmez."
                )
            else:
                headline = "Belirgin bir trend eğilimi saptanmadı."
                explanation = (
                    "Göstergeler nötr bölgede seyretmekte olup tanımlı bir eşik aşımı oluşmamıştır."
                )

        # Geçersizleşme kriterleri derlemesi
        invalidation_list: list[str] = [r.invalidation_criteria for r in active_rules]
        if not invalidation_list:
            invalidation_list = [
                "Tanımlı çoklu gösterge eşikleri sağlandığında yeni değerlendirme yapılır."
            ]

        hash_val = self._calculate_hash(
            symbol,
            eval_time,
            final_direction,
            tuple(r.rule_id for r in active_rules),
        )

        return TrendSignalResult(
            symbol=symbol,
            evaluated_at=eval_time,
            direction=final_direction,
            headline=headline,
            explanation=explanation,
            invalidation_criteria=tuple(invalidation_list),
            met_rules=tuple(r.rule_id for r in active_rules),
            met_categories=tuple(c.value for c in ({r.category for r in active_rules})),
            filter_result=filter_result,
            cooldown_active=cooldown_active,
            reproducibility_hash=hash_val,
            disclaimer=self.DEFAULT_DISCLAIMER,
        )

    def scan_universe(
        self,
        universe_data: dict[str, pd.DataFrame],
    ) -> list[TrendSignalResult]:
        """Birden çok hisse senedi için tarama yapar ve sonuçları sıralar."""
        results: list[TrendSignalResult] = []
        for symbol, df in sorted(universe_data.items()):
            res = self.evaluate(df, symbol=symbol)
            results.append(res)
        return results

    @staticmethod
    def _calculate_hash(
        symbol: str,
        timestamp: str,
        direction: TrendDirection,
        rules: tuple[str, ...],
    ) -> str:
        """Deterministik ve tekrarlanabilir hash üretir."""
        raw = f"{symbol}|{timestamp}|{direction.value}|{','.join(sorted(rules))}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
