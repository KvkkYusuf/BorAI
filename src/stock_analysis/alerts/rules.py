"""Kullanıcı tarafından yapılandırılabilir, açıklanabilir uyarı ve eşik kural motoru.

Bu modül FAZ-12 gereksinimlerine ve SPK uyumluluk kırmızı çizgilerine tam uyumludur:
- Kullanıcının teknik gösterge ve fiyat eşiklerini şeffaf biçimde yapılandırmasını sağlar.
- Her eşik ve koşul için sade, anlaşılır Türkçe açıklama metni ve sınır doğrulama üretir.
- Göstergelerin neyi ölçtüğünü, ne zaman tetikleneceğini ve ne zaman geçersiz olacağını
  kullanıcıya teknik karmaşa yaratmadan açıklar.
- Kural adları, açıklamalar ve kullanıcı notlarında kesinlikle yatırım tavsiyesi, hedef fiyat
  veya yönlendirici ifade (al/sat vb.) barındırılmasına izin vermez.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from stock_analysis.indicators.technical import (
    calculate_all_indicators,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)
from stock_analysis.indicators.trend import validate_compliant_text

LEGAL_DISCLAIMER_RULES: str = (
    "Bu kural yapılandırması yalnızca teknik ve matematiksel gösterge eşiklerini takip eder; "
    "kesinlikle yatırım danışmanlığı, yönlendirme, hedef fiyat veya al/sat tavsiyesi değildir."
)


class ConditionType(StrEnum):
    """Desteklenen uyarı koşulu türleri."""

    PRICE_ABOVE = "PRICE_ABOVE"
    PRICE_BELOW = "PRICE_BELOW"
    PRICE_CHANGE_PCT = "PRICE_CHANGE_PCT"
    RSI_THRESHOLD = "RSI_THRESHOLD"
    MA_CROSSOVER = "MA_CROSSOVER"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"


class ConditionOperator(StrEnum):
    """Koşul karşılaştırma operatörleri."""

    GREATER_THAN = "GREATER_THAN"  # >
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"  # >=
    LESS_THAN = "LESS_THAN"  # <
    LESS_THAN_OR_EQUAL = "LESS_THAN_OR_EQUAL"  # <=
    CROSSES_ABOVE = "CROSSES_ABOVE"  # Yukarı Kestiğinde
    CROSSES_BELOW = "CROSSES_BELOW"  # Aşağı Kestiğinde


class LogicalOperator(StrEnum):
    """Birden fazla koşulun birleştirilme mantığı."""

    ALL = "ALL"  # VE: Tüm koşullar aynı anda sağlanmalı
    ANY = "ANY"  # VEYA: Koşullardan en az biri sağlanmalı


class RuleValidationError(ValueError):
    """Kural veya eşik parametrelerinin geçerlilik hatası."""


@dataclass(frozen=True)
class ExplainableDescription:
    """Kullanıcıya sunulacak açıklanabilir koşul detayı."""

    condition_type: ConditionType
    title: str
    summary: str
    indicator_name: str
    parameters: dict[str, Any]
    when_triggers: str
    invalidation_conditions: str
    disclaimer: str = LEGAL_DISCLAIMER_RULES


@dataclass(frozen=True)
class RuleExplanation:
    """Bütüncül uyarı kuralının açıklanabilirlik özeti."""

    rule_name: str
    symbol: str
    logical_operator: LogicalOperator
    overall_summary: str
    condition_explanations: list[ExplainableDescription]
    cooldown_info: str
    legal_disclaimer: str = LEGAL_DISCLAIMER_RULES


@dataclass
class AlertCondition:
    """Bireysel eşik ve tetiklenme koşulu yapılandırması."""

    condition_type: ConditionType
    operator: ConditionOperator
    threshold: float
    period: int | None = None
    secondary_period: int | None = None
    ma_type: str = "SMA"  # SMA veya EMA
    unit: str = "TL"

    def validate(self) -> None:
        """Eşik değerlerinin ve parametre sınırlarının matematiksel doğruluğunu denetler."""
        if not isinstance(self.threshold, (int, float)) or pd.isna(self.threshold):
            raise RuleValidationError("Eşik değeri (threshold) geçerli bir sayı olmalıdır.")

        if self.condition_type in (ConditionType.PRICE_ABOVE, ConditionType.PRICE_BELOW):
            if self.threshold <= 0:
                raise RuleValidationError(
                    f"Fiyat eşiği pozitif bir değer olmalıdır, verilen: {self.threshold}"
                )
            if self.condition_type == ConditionType.PRICE_ABOVE and self.operator not in (
                ConditionOperator.GREATER_THAN,
                ConditionOperator.GREATER_THAN_OR_EQUAL,
                ConditionOperator.CROSSES_ABOVE,
            ):
                raise RuleValidationError(
                    "Fiyat Üzeri koşulunda > veya Yukarı Keser operatörü kullanılmalıdır."
                )
            if self.condition_type == ConditionType.PRICE_BELOW and self.operator not in (
                ConditionOperator.LESS_THAN,
                ConditionOperator.LESS_THAN_OR_EQUAL,
                ConditionOperator.CROSSES_BELOW,
            ):
                raise RuleValidationError(
                    "Fiyat Altı koşulunda < veya Aşağı Keser operatörü kullanılmalıdır."
                )

        elif self.condition_type == ConditionType.RSI_THRESHOLD:
            if not (0.0 <= self.threshold <= 100.0):
                raise RuleValidationError(
                    f"RSI eşiği 0 ile 100 arasında olmalıdır, verilen: {self.threshold}"
                )
            p = self.period if self.period is not None else 14
            if p < 2 or p > 250:
                raise RuleValidationError(
                    f"RSI periyodu 2 ile 250 bar arasında olmalıdır, verilen: {p}"
                )

        elif self.condition_type == ConditionType.PRICE_CHANGE_PCT:
            if not (-100.0 <= self.threshold <= 1000.0):
                raise RuleValidationError(
                    f"Fiyat yüzdesel değişim eşiği -%100 ile +%1000 arasında olmalıdır: "
                    f"{self.threshold}"
                )
            p = self.period if self.period is not None else 1
            if p < 1 or p > 100:
                raise RuleValidationError(
                    f"Değişim periyodu 1 ile 100 bar arasında olmalıdır, verilen: {p}"
                )

        elif self.condition_type == ConditionType.MA_CROSSOVER:
            fast = self.period if self.period is not None else 20
            slow = self.secondary_period if self.secondary_period is not None else 50
            if fast < 2 or slow < 2:
                raise RuleValidationError("Hareketli ortalama periyotları en az 2 olmalıdır.")
            if fast >= slow:
                raise RuleValidationError(
                    f"Hızlı ortalama periyodu ({fast}) yavaş ortalama ({slow}) küçük olmalıdır."
                )
            if self.ma_type.upper() not in ("SMA", "EMA"):
                raise RuleValidationError(
                    f"Desteklenmeyen ortalama türü: {self.ma_type}. 'SMA' veya 'EMA' seçiniz."
                )

        elif self.condition_type == ConditionType.VOLUME_SPIKE:
            if self.threshold <= 0:
                raise RuleValidationError(
                    f"Hacim çarpanı pozitif bir değer olmalıdır, verilen: {self.threshold}"
                )
            p = self.period if self.period is not None else 20
            if p < 3 or p > 200:
                raise RuleValidationError(
                    f"Hacim ortalaması periyodu 3 ile 200 bar arasında olmalıdır, verilen: {p}"
                )

        elif self.condition_type == ConditionType.VOLATILITY_EXPANSION:
            if self.threshold <= 0:
                raise RuleValidationError(
                    f"Oynaklık eşiği pozitif olmalıdır, verilen: {self.threshold}"
                )
            p = self.period if self.period is not None else 14
            if p < 2 or p > 100:
                raise RuleValidationError(
                    f"ATR periyodu 2 ile 100 bar arasında olmalıdır, verilen: {p}"
                )

    def explain(self) -> ExplainableDescription:
        """Koşulu kullanıcı için sade, anlaşılır ve şeffaf Türkçe açıklamaya dönüştürür."""
        self.validate()

        if self.condition_type == ConditionType.PRICE_ABOVE:
            return ExplainableDescription(
                condition_type=self.condition_type,
                title=f"Fiyat Eşik Üzeri ({self.threshold:,.2f} {self.unit})",
                summary=(
                    f"Son kapanış fiyatı {self.threshold:,.2f} {self.unit} "
                    "seviyesinin üzerine çıktığında bildirim üretilir."
                ),
                indicator_name="Son Kapanış Fiyatı (Close)",
                parameters={
                    "threshold": self.threshold,
                    "operator": self.operator.value,
                    "unit": self.unit,
                },
                when_triggers=(
                    f"Fiyat verisi {self.threshold:,.2f} {self.unit} "
                    "eşik değerini aştığı anda tetiklenir."
                ),
                invalidation_conditions=(
                    f"Fiyat {self.threshold:,.2f} {self.unit} altında kaldığı sürece tetiklenmez."
                ),
            )

        if self.condition_type == ConditionType.PRICE_BELOW:
            return ExplainableDescription(
                condition_type=self.condition_type,
                title=f"Fiyat Eşik Altı ({self.threshold:,.2f} {self.unit})",
                summary=(
                    f"Son kapanış fiyatı {self.threshold:,.2f} {self.unit} "
                    "seviyesinin altına indiğinde bildirim üretilir."
                ),
                indicator_name="Son Kapanış Fiyatı (Close)",
                parameters={
                    "threshold": self.threshold,
                    "operator": self.operator.value,
                    "unit": self.unit,
                },
                when_triggers=(
                    f"Fiyat verisi {self.threshold:,.2f} {self.unit} "
                    "eşik değerinin altına sarktığı anda tetiklenir."
                ),
                invalidation_conditions=(
                    f"Fiyat {self.threshold:,.2f} {self.unit} üzerinde kaldığı sürece tetiklenmez."
                ),
            )

        if self.condition_type == ConditionType.RSI_THRESHOLD:
            period = self.period or 14
            op_text = (
                "üzerine çıktığında"
                if self.operator
                in (
                    ConditionOperator.GREATER_THAN,
                    ConditionOperator.GREATER_THAN_OR_EQUAL,
                    ConditionOperator.CROSSES_ABOVE,
                )
                else "altına indiğinde"
            )
            zone = (
                "aşırı alım bölgesine yakınlaşma"
                if self.threshold >= 70
                else (
                    "aşırı satım bölgesine yakınlaşma" if self.threshold <= 30 else "momentum eşiği"
                )
            )
            return ExplainableDescription(
                condition_type=self.condition_type,
                title=f"RSI({period}) Göstergesi {self.threshold:.1f} Eşiği",
                summary=(
                    f"{period} periyotluk RSI momentum göstergesi {self.threshold:.1f} "
                    f"seviyesinin {op_text} tetiklenir ({zone})."
                ),
                indicator_name=f"Göreceli Güç Endeksi (RSI, {period} bar)",
                parameters={
                    "threshold": self.threshold,
                    "period": period,
                    "operator": self.operator.value,
                },
                when_triggers=(
                    f"RSI değeri {self.threshold:.1f} değerini {op_text} "
                    "kural tetiklenir ve sistem bildirim kaydı oluşturulur. "
                    "Bu durum piyasadaki momentum eğilimini gösterir."
                ),
                invalidation_conditions=(
                    f"RSI göstergesi {self.threshold:.1f} sınırını sağlamadığında tetiklenmez."
                ),
            )

        if self.condition_type == ConditionType.PRICE_CHANGE_PCT:
            period = self.period or 1
            direction = "yükseliş" if self.threshold >= 0 else "düşüş"
            sign = "+" if self.threshold > 0 else ""
            return ExplainableDescription(
                condition_type=self.condition_type,
                title=f"{period} Barlık Fiyat Değişimi ({sign}%{self.threshold:.2f})",
                summary=(
                    f"Son {period} bar içerisindeki fiyat değişimi {sign}%{self.threshold:.2f} "
                    f"eşiğini geçtiğinde tetiklenir ({direction} yönlü değişim)."
                ),
                indicator_name=f"Yüzdesel Getiri / Fiyat Değişimi ({period} bar)",
                parameters={
                    "threshold_pct": self.threshold,
                    "period": period,
                    "operator": self.operator.value,
                },
                when_triggers=(
                    f"Fiyatın son {period} bar önceki kapanışa göre yüzdesel farkı "
                    f"{sign}%{self.threshold:.2f} eşiğini aştığında tetiklenir."
                ),
                invalidation_conditions=(
                    "Fiyat değişimi belirtilen yüzdesel sınırın gerisinde kaldığında tetiklenmez."
                ),
            )

        if self.condition_type == ConditionType.MA_CROSSOVER:
            fast = self.period or 20
            slow = self.secondary_period or 50
            ma_name = self.ma_type.upper()
            op_text = (
                "yukarı kestiğinde (pozitif eğilim)"
                if self.operator
                in (
                    ConditionOperator.CROSSES_ABOVE,
                    ConditionOperator.GREATER_THAN,
                    ConditionOperator.GREATER_THAN_OR_EQUAL,
                )
                else "aşağı kestiğinde (negatif eğilim)"
            )
            return ExplainableDescription(
                condition_type=self.condition_type,
                title=f"{ma_name}({fast}) / {ma_name}({slow}) Kesişimi",
                summary=(
                    f"Kısa vadeli {ma_name}({fast}) hareketli ortalaması, "
                    f"uzun vadeli {ma_name}({slow}) ortalamasını {op_text} tetiklenir."
                ),
                indicator_name=f"{ma_name} Hareketli Ortalama Kesişimi ({fast} ve {slow} bar)",
                parameters={
                    "fast_period": fast,
                    "slow_period": slow,
                    "ma_type": ma_name,
                    "operator": self.operator.value,
                },
                when_triggers=(
                    f"Hızlı eğilim çizgisi ({fast}), yavaş eğilim çizgisini ({slow}) "
                    "belirtilen yönde kestiğinde tetiklenir."
                ),
                invalidation_conditions=(
                    "Ortalamalar kesişmediğinde veya ters yönde seyrettiğinde koşul gerçekleşmez."
                ),
            )

        if self.condition_type == ConditionType.VOLUME_SPIKE:
            period = self.period or 20
            return ExplainableDescription(
                condition_type=self.condition_type,
                title=f"Hacim Sıçraması ({period} Günlük Ortalamanın {self.threshold:.1f}x Katı)",
                summary=(
                    f"İşlem hacmi, son {period} günlük ortalama işlem hacminin "
                    f"{self.threshold:.1f} katına ulaştığında tetiklenir."
                ),
                indicator_name=f"Hacim Sıçraması (Volume Spike - {period} bar SMA)",
                parameters={
                    "volume_multiplier": self.threshold,
                    "period": period,
                    "operator": self.operator.value,
                },
                when_triggers=(
                    f"Günlük hacim {period} günlük ortalama hacmin en az "
                    f"{self.threshold:.1f} katı seviyesine çıktığında tetiklenir."
                ),
                invalidation_conditions=(
                    "Hacim ortalamanın altında veya eşik çarpanından düşük kaldığında tetiklenmez."
                ),
            )

        # VOLATILITY_EXPANSION
        period = self.period or 14
        return ExplainableDescription(
            condition_type=self.condition_type,
            title=f"Oynaklık Artışı (ATR({period}) > {self.threshold:.2f})",
            summary=(
                f"Ortalama Gerçek Aralık (ATR) oynaklık göstergesi "
                f"{self.threshold:.2f} değerini aştığında tetiklenir."
            ),
            indicator_name=f"Ortalama Gerçek Aralık (ATR - {period} bar)",
            parameters={
                "threshold": self.threshold,
                "period": period,
                "operator": self.operator.value,
            },
            when_triggers=(
                f"Fiyat dalgalanma aralığı (oynaklık) {self.threshold:.2f} eşiğini "
                "aştığında tetiklenir."
            ),
            invalidation_conditions=(
                "Piyasa oynaklığı belirtilen eşik değerinin altında seyrettiğinde tetiklenmez."
            ),
        )

    def evaluate(self, df: pd.DataFrame) -> bool:
        """Verilen fiyat/gösterge verisi üzerinde koşulun sağlanıp sağlanmadığını değerlendirir."""
        self.validate()
        if df.empty or len(df) < 2:
            return False

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        close_col = "close" if "close" in df.columns else "Close"
        vol_col = "volume" if "volume" in df.columns else "Volume"

        if self.condition_type == ConditionType.PRICE_ABOVE:
            price = float(last_row[close_col])
            if self.operator == ConditionOperator.CROSSES_ABOVE:
                prev_price = float(prev_row[close_col])
                return prev_price <= self.threshold and price > self.threshold
            return price >= self.threshold

        if self.condition_type == ConditionType.PRICE_BELOW:
            price = float(last_row[close_col])
            if self.operator == ConditionOperator.CROSSES_BELOW:
                prev_price = float(prev_row[close_col])
                return prev_price >= self.threshold and price < self.threshold
            return price <= self.threshold

        if self.condition_type == ConditionType.RSI_THRESHOLD:
            period = self.period or 14
            rsi_col = f"rsi_{period}"
            if rsi_col in df.columns:
                rsi_series = df[rsi_col]
            else:
                rsi_series = calculate_rsi(df[close_col], period=period)

            curr_rsi = float(rsi_series.iloc[-1])
            if pd.isna(curr_rsi):
                return False

            if self.operator == ConditionOperator.CROSSES_ABOVE:
                prev_rsi = float(rsi_series.iloc[-2])
                return prev_rsi <= self.threshold and curr_rsi > self.threshold
            if self.operator == ConditionOperator.CROSSES_BELOW:
                prev_rsi = float(rsi_series.iloc[-2])
                return prev_rsi >= self.threshold and curr_rsi < self.threshold
            if self.operator in (
                ConditionOperator.GREATER_THAN,
                ConditionOperator.GREATER_THAN_OR_EQUAL,
            ):
                return curr_rsi >= self.threshold
            return curr_rsi <= self.threshold

        if self.condition_type == ConditionType.PRICE_CHANGE_PCT:
            period = self.period or 1
            if len(df) <= period:
                return False
            past_price = float(df[close_col].iloc[-(period + 1)])
            curr_price = float(df[close_col].iloc[-1])
            if past_price <= 0:
                return False
            pct_change = ((curr_price - past_price) / past_price) * 100.0

            if self.operator in (
                ConditionOperator.GREATER_THAN,
                ConditionOperator.GREATER_THAN_OR_EQUAL,
            ):
                return pct_change >= self.threshold
            return pct_change <= self.threshold

        if self.condition_type == ConditionType.MA_CROSSOVER:
            fast = self.period or 20
            slow = self.secondary_period or 50
            if len(df) < slow:
                return False

            fn = calculate_sma if self.ma_type.upper() == "SMA" else calculate_ema
            fast_series = fn(df[close_col], fast)
            slow_series = fn(df[close_col], slow)

            curr_fast = float(fast_series.iloc[-1])
            curr_slow = float(slow_series.iloc[-1])
            prev_fast = float(fast_series.iloc[-2])
            prev_slow = float(slow_series.iloc[-2])

            if any(pd.isna([curr_fast, curr_slow, prev_fast, prev_slow])):
                return False

            if self.operator == ConditionOperator.CROSSES_ABOVE:
                return prev_fast <= prev_slow and curr_fast > curr_slow
            if self.operator == ConditionOperator.CROSSES_BELOW:
                return prev_fast >= prev_slow and curr_fast < curr_slow
            if self.operator in (
                ConditionOperator.GREATER_THAN,
                ConditionOperator.GREATER_THAN_OR_EQUAL,
            ):
                return curr_fast >= curr_slow
            return curr_fast <= curr_slow

        if self.condition_type == ConditionType.VOLUME_SPIKE:
            period = self.period or 20
            if len(df) < period or vol_col not in df.columns:
                return False
            avg_vol = float(df[vol_col].iloc[-(period + 1) : -1].mean())
            curr_vol = float(df[vol_col].iloc[-1])
            if avg_vol <= 0:
                return False
            return (curr_vol / avg_vol) >= self.threshold

        if self.condition_type == ConditionType.VOLATILITY_EXPANSION:
            period = self.period or 14
            if "atr_14" in df.columns and period == 14:
                atr_val = float(df["atr_14"].iloc[-1])
            else:
                tech_df = calculate_all_indicators(df)
                atr_col = f"atr_{period}" if f"atr_{period}" in tech_df.columns else "atr_14"
                atr_val = float(tech_df[atr_col].iloc[-1]) if atr_col in tech_df.columns else 0.0

            if pd.isna(atr_val):
                return False
            return atr_val >= self.threshold

        return False

    def to_dict(self) -> dict[str, Any]:
        """Serileştirme fonksiyonu."""
        return {
            "condition_type": self.condition_type.value,
            "operator": self.operator.value,
            "threshold": self.threshold,
            "period": self.period,
            "secondary_period": self.secondary_period,
            "ma_type": self.ma_type,
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlertCondition:
        """Sözlükten AlertCondition nesnesi oluşturur."""
        return cls(
            condition_type=ConditionType(data["condition_type"]),
            operator=ConditionOperator(data["operator"]),
            threshold=float(data["threshold"]),
            period=data.get("period"),
            secondary_period=data.get("secondary_period"),
            ma_type=data.get("ma_type", "SMA"),
            unit=data.get("unit", "TL"),
        )


@dataclass
class AlertRuleConfig:
    """Kullanıcı tarafından yapılandırılmış tam bir uyarı kuralı."""

    name: str
    user_id: int
    symbol: str
    conditions: list[AlertCondition]
    logical_operator: LogicalOperator = LogicalOperator.ALL
    cooldown_minutes: int = 60
    is_active: bool = True
    user_note: str | None = None

    def validate(self) -> None:
        """Kural adı, mevzuat uyumu, koşullar ve bekleme süresini doğrular."""
        if not self.name or not self.name.strip():
            raise RuleValidationError("Kural adı boş olamaz.")
        validate_compliant_text(self.name)

        if not self.symbol or not self.symbol.strip():
            raise RuleValidationError("Sembol boş olamaz.")

        if self.user_id <= 0:
            raise RuleValidationError("Geçersiz kullanıcı kimliği (user_id).")

        if not self.conditions:
            raise RuleValidationError("En az bir uyarı koşulu tanımlanmalıdır.")

        if self.user_note:
            validate_compliant_text(self.user_note)

        if not (1 <= self.cooldown_minutes <= 10080):
            raise RuleValidationError(
                f"Tekrar bildirim süresi (cooldown) 1 ile 10080 dakika arasında olmalıdır: "
                f"{self.cooldown_minutes}"
            )

        for cond in self.conditions:
            cond.validate()

    def explain(self) -> RuleExplanation:
        """Kuralın tamamını açıklanabilir rapor formatına dönüştürür."""
        self.validate()
        cond_explanations = [cond.explain() for cond in self.conditions]

        op_desc = (
            "tanımlı tüm koşulların aynı anda gerçekleşmesi (VE mantığı)"
            if self.logical_operator == LogicalOperator.ALL
            else "tanımlı koşullardan en az birinin gerçekleşmesi (VEYA mantığı)"
        )

        overall_summary = (
            f"'{self.symbol}' sembolü için {self.name} kuralı kapsamında {op_desc} "
            f"durumunda bildirim oluşturulacaktır. Tetiklenme sonrası en az "
            f"{self.cooldown_minutes} dakika boyunca aynı koşul için tekrar bildirim gönderilmez."
        )

        cooldown_info = (
            f"Sessiz süre (cooldown): {self.cooldown_minutes} dakika. "
            "Kural tetiklendikten sonra bildirim kirliliğini önlemek adına beklenir."
        )

        return RuleExplanation(
            rule_name=self.name,
            symbol=self.symbol,
            logical_operator=self.logical_operator,
            overall_summary=overall_summary,
            condition_explanations=cond_explanations,
            cooldown_info=cooldown_info,
        )

    def evaluate(self, df: pd.DataFrame) -> tuple[bool, list[str]]:
        """Kuraldaki koşulları değerlendirir ve tetiklenen koşul özetlerini döner."""
        self.validate()
        if not self.is_active:
            return False, []

        triggered_reasons: list[str] = []
        condition_results: list[bool] = []

        for i, cond in enumerate(self.conditions):
            res = cond.evaluate(df)
            condition_results.append(res)
            if res:
                exp = cond.explain()
                triggered_reasons.append(f"Koşul #{i + 1} ({exp.title}): {exp.summary}")

        if self.logical_operator == LogicalOperator.ALL:
            is_triggered = all(condition_results)
            return (is_triggered, triggered_reasons if is_triggered else [])

        is_triggered = any(condition_results)
        return (is_triggered, triggered_reasons if is_triggered else [])

    def to_dict(self) -> dict[str, Any]:
        """Serileştirme metodu."""
        return {
            "name": self.name,
            "user_id": self.user_id,
            "symbol": self.symbol,
            "conditions": [c.to_dict() for c in self.conditions],
            "logical_operator": self.logical_operator.value,
            "cooldown_minutes": self.cooldown_minutes,
            "is_active": self.is_active,
            "user_note": self.user_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlertRuleConfig:
        """JSON/sözlük verisinden AlertRuleConfig inşa eder."""
        return cls(
            name=data["name"],
            user_id=int(data["user_id"]),
            symbol=data["symbol"],
            conditions=[AlertCondition.from_dict(c) for c in data["conditions"]],
            logical_operator=LogicalOperator(data.get("logical_operator", "ALL")),
            cooldown_minutes=int(data.get("cooldown_minutes", 60)),
            is_active=bool(data.get("is_active", True)),
            user_note=data.get("user_note"),
        )


class ExplainableRuleBuilder:
    """Kullanıcı dostu hazır şablonlar ve açıklanabilir kural kurucu."""

    @staticmethod
    def create_rsi_alert(
        name: str,
        user_id: int,
        symbol: str,
        threshold: float = 70.0,
        period: int = 14,
        is_upper_bound: bool = True,
        cooldown_minutes: int = 60,
    ) -> AlertRuleConfig:
        """RSI aşırı alım/satım eşik kuralı oluşturur."""
        op = ConditionOperator.CROSSES_ABOVE if is_upper_bound else ConditionOperator.CROSSES_BELOW
        cond = AlertCondition(
            condition_type=ConditionType.RSI_THRESHOLD,
            operator=op,
            threshold=threshold,
            period=period,
            unit="Puan",
        )
        rule = AlertRuleConfig(
            name=name,
            user_id=user_id,
            symbol=symbol,
            conditions=[cond],
            cooldown_minutes=cooldown_minutes,
        )
        rule.validate()
        return rule

    @staticmethod
    def create_price_threshold_alert(
        name: str,
        user_id: int,
        symbol: str,
        price_threshold: float,
        is_above: bool = True,
        cooldown_minutes: int = 60,
    ) -> AlertRuleConfig:
        """Kapanış fiyatı eşik kuralı oluşturur."""
        ctype = ConditionType.PRICE_ABOVE if is_above else ConditionType.PRICE_BELOW
        op = ConditionOperator.CROSSES_ABOVE if is_above else ConditionOperator.CROSSES_BELOW
        cond = AlertCondition(
            condition_type=ctype,
            operator=op,
            threshold=price_threshold,
            unit="TL",
        )
        rule = AlertRuleConfig(
            name=name,
            user_id=user_id,
            symbol=symbol,
            conditions=[cond],
            cooldown_minutes=cooldown_minutes,
        )
        rule.validate()
        return rule

    @staticmethod
    def create_ma_crossover_alert(
        name: str,
        user_id: int,
        symbol: str,
        fast_period: int = 20,
        slow_period: int = 50,
        ma_type: str = "SMA",
        bullish_cross: bool = True,
        cooldown_minutes: int = 120,
    ) -> AlertRuleConfig:
        """Hareketli ortalama kesişim kuralı oluşturur."""
        op = ConditionOperator.CROSSES_ABOVE if bullish_cross else ConditionOperator.CROSSES_BELOW
        cond = AlertCondition(
            condition_type=ConditionType.MA_CROSSOVER,
            operator=op,
            threshold=0.0,
            period=fast_period,
            secondary_period=slow_period,
            ma_type=ma_type,
        )
        rule = AlertRuleConfig(
            name=name,
            user_id=user_id,
            symbol=symbol,
            conditions=[cond],
            cooldown_minutes=cooldown_minutes,
        )
        rule.validate()
        return rule

    @staticmethod
    def create_volume_spike_alert(
        name: str,
        user_id: int,
        symbol: str,
        multiplier: float = 2.0,
        period: int = 20,
        cooldown_minutes: int = 180,
    ) -> AlertRuleConfig:
        """İşlem hacmi sıçraması kuralı oluşturur."""
        cond = AlertCondition(
            condition_type=ConditionType.VOLUME_SPIKE,
            operator=ConditionOperator.GREATER_THAN_OR_EQUAL,
            threshold=multiplier,
            period=period,
            unit="Kat",
        )
        rule = AlertRuleConfig(
            name=name,
            user_id=user_id,
            symbol=symbol,
            conditions=[cond],
            cooldown_minutes=cooldown_minutes,
        )
        rule.validate()
        return rule

    @staticmethod
    def get_preset_templates() -> list[dict[str, Any]]:
        """Arayüzde kullanıcılara sunulacak önceden hazırlanmış açıklanabilir şablonlar."""
        return [
            {
                "template_id": "rsi_overbought",
                "title": "RSI Aşırı Değerlenme Eşiği",
                "condition_type": ConditionType.RSI_THRESHOLD.value,
                "default_threshold": 70.0,
                "min_threshold": 50.0,
                "max_threshold": 95.0,
                "default_period": 14,
                "explanation": (
                    "Momentum göstergesi 70 üzerine çıktığında fiyatın hızlı yükseldiği "
                    "ve aşırı alım bölgesine yaklaştığı uyarısını üretir."
                ),
            },
            {
                "template_id": "rsi_oversold",
                "title": "RSI Aşırı Düşüş Eşiği",
                "condition_type": ConditionType.RSI_THRESHOLD.value,
                "default_threshold": 30.0,
                "min_threshold": 5.0,
                "max_threshold": 50.0,
                "default_period": 14,
                "explanation": (
                    "Momentum göstergesi 30 altına indiğinde fiyatın hızlı gerilediği "
                    "ve aşırı satım bölgesine yaklaştığı uyarısını üretir."
                ),
            },
            {
                "template_id": "golden_cross_tendency",
                "title": "Hareketli Ortalama Pozitif Kesişimi (SMA 20/50)",
                "condition_type": ConditionType.MA_CROSSOVER.value,
                "default_fast_period": 20,
                "default_slow_period": 50,
                "ma_type": "SMA",
                "explanation": (
                    "Kısa vadeli 20 günlük ortalama, 50 günlük ortalamayı yukarı kestiğinde "
                    "orta vadeli pozitif eğilim koşulu uyarısı üretir."
                ),
            },
            {
                "template_id": "volume_surge",
                "title": "Hacim Patlaması (20 Günlük Ortalamanın 2 Katı)",
                "condition_type": ConditionType.VOLUME_SPIKE.value,
                "default_threshold": 2.0,
                "min_threshold": 1.2,
                "max_threshold": 10.0,
                "default_period": 20,
                "explanation": (
                    "Günlük işlem hacmi son 20 günün ortalamasının 2 katına ulaştığında "
                    "piyasa hareketliliği uyarısı üretir."
                ),
            },
            {
                "template_id": "daily_surge_pct",
                "title": "Günlük Hızlı Yükseliş Eşiği (+%4)",
                "condition_type": ConditionType.PRICE_CHANGE_PCT.value,
                "default_threshold": 4.0,
                "min_threshold": 0.5,
                "max_threshold": 20.0,
                "default_period": 1,
                "explanation": (
                    "Son gün içindeki fiyat artışı %4'ü aştığında "
                    "volatilite ve trend bildirimi üretir."
                ),
            },
        ]
