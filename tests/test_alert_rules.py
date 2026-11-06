"""Tests for Explainable Alert Rules and Threshold Configuration (FAZ-12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_analysis.alerts.rules import (
    LEGAL_DISCLAIMER_RULES,
    AlertCondition,
    AlertRuleConfig,
    ConditionOperator,
    ConditionType,
    ExplainableRuleBuilder,
    LogicalOperator,
    RuleValidationError,
)
from stock_analysis.indicators.trend import PROHIBITED_TERMS


def _create_sample_df(n_bars: int = 60, trend: str = "bullish") -> pd.DataFrame:
    """Belirli trend eğilimine sahip test DataFrame'i üretir."""
    end = datetime.now(UTC)
    dates = [end - timedelta(days=n_bars - 1 - i) for i in range(n_bars)]

    close = [100.0]
    for _ in range(1, n_bars):
        pct = 0.015 if trend == "bullish" else (-0.015 if trend == "bearish" else 0.0)
        close.append(close[-1] * (1 + pct))

    close_arr = np.array(close)
    high_arr = close_arr * 1.01
    low_arr = close_arr * 0.99
    open_arr = (high_arr + low_arr) / 2.0
    vol_arr = np.full(n_bars, 100_000.0)

    return pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": vol_arr,
        },
        index=dates,
    )


class TestAlertConditionValidation:
    """Bireysel koşul ve eşik sınır doğrulama testleri."""

    def test_price_above_validation(self) -> None:
        # Geçerli
        cond = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=150.0,
        )
        cond.validate()

        # Negatif veya 0 fiyat hatası
        with pytest.raises(RuleValidationError, match="pozitif"):
            AlertCondition(
                condition_type=ConditionType.PRICE_ABOVE,
                operator=ConditionOperator.GREATER_THAN,
                threshold=0.0,
            ).validate()

        # Uyumsuz operatör hatası (< ile PRICE_ABOVE)
        with pytest.raises(RuleValidationError, match="operatör"):
            AlertCondition(
                condition_type=ConditionType.PRICE_ABOVE,
                operator=ConditionOperator.LESS_THAN,
                threshold=100.0,
            ).validate()

    def test_price_below_validation(self) -> None:
        # Geçerli
        cond = AlertCondition(
            condition_type=ConditionType.PRICE_BELOW,
            operator=ConditionOperator.LESS_THAN,
            threshold=75.5,
        )
        cond.validate()

        # Negatif eşik hatası
        with pytest.raises(RuleValidationError, match="pozitif"):
            AlertCondition(
                condition_type=ConditionType.PRICE_BELOW,
                operator=ConditionOperator.LESS_THAN,
                threshold=-10.0,
            ).validate()

        # Uyumsuz operatör hatası (> ile PRICE_BELOW)
        with pytest.raises(RuleValidationError, match="operatör"):
            AlertCondition(
                condition_type=ConditionType.PRICE_BELOW,
                operator=ConditionOperator.GREATER_THAN,
                threshold=50.0,
            ).validate()

    def test_rsi_bounds_validation(self) -> None:
        # Geçerli
        AlertCondition(
            condition_type=ConditionType.RSI_THRESHOLD,
            operator=ConditionOperator.GREATER_THAN,
            threshold=70.0,
            period=14,
        ).validate()

        # 100 üzeri RSI hatası
        with pytest.raises(RuleValidationError, match="0 ile 100 arasında"):
            AlertCondition(
                condition_type=ConditionType.RSI_THRESHOLD,
                operator=ConditionOperator.GREATER_THAN,
                threshold=105.0,
            ).validate()

        # Negatif RSI hatası
        with pytest.raises(RuleValidationError, match="0 ile 100 arasında"):
            AlertCondition(
                condition_type=ConditionType.RSI_THRESHOLD,
                operator=ConditionOperator.LESS_THAN,
                threshold=-5.0,
            ).validate()

        # Geçersiz periyot
        with pytest.raises(RuleValidationError, match="periyodu 2 ile 250"):
            AlertCondition(
                condition_type=ConditionType.RSI_THRESHOLD,
                operator=ConditionOperator.GREATER_THAN,
                threshold=60.0,
                period=1,
            ).validate()

    def test_ma_crossover_validation(self) -> None:
        # Geçerli (SMA 20/50)
        AlertCondition(
            condition_type=ConditionType.MA_CROSSOVER,
            operator=ConditionOperator.CROSSES_ABOVE,
            threshold=0.0,
            period=20,
            secondary_period=50,
            ma_type="SMA",
        ).validate()

        # Hızlı >= Yavaş hatası (SMA 50/20)
        with pytest.raises(RuleValidationError, match="küçük olmalıdır"):
            AlertCondition(
                condition_type=ConditionType.MA_CROSSOVER,
                operator=ConditionOperator.CROSSES_ABOVE,
                threshold=0.0,
                period=50,
                secondary_period=20,
            ).validate()

        # Geçersiz MA türü
        with pytest.raises(RuleValidationError, match="Desteklenmeyen ortalama türü"):
            AlertCondition(
                condition_type=ConditionType.MA_CROSSOVER,
                operator=ConditionOperator.CROSSES_ABOVE,
                threshold=0.0,
                period=10,
                secondary_period=30,
                ma_type="UNKNOWN_MA",
            ).validate()

    def test_volume_spike_validation(self) -> None:
        # Geçerli
        AlertCondition(
            condition_type=ConditionType.VOLUME_SPIKE,
            operator=ConditionOperator.GREATER_THAN_OR_EQUAL,
            threshold=2.5,
            period=20,
        ).validate()

        # Negatif çarpan hatası
        with pytest.raises(RuleValidationError, match="çarpanı pozitif"):
            AlertCondition(
                condition_type=ConditionType.VOLUME_SPIKE,
                operator=ConditionOperator.GREATER_THAN_OR_EQUAL,
                threshold=-1.0,
            ).validate()

    def test_price_change_pct_validation(self) -> None:
        # Geçerli
        AlertCondition(
            condition_type=ConditionType.PRICE_CHANGE_PCT,
            operator=ConditionOperator.GREATER_THAN,
            threshold=5.0,
            period=1,
        ).validate()

        # Aşırı sınır hatası
        with pytest.raises(RuleValidationError, match="-%100 ile"):
            AlertCondition(
                condition_type=ConditionType.PRICE_CHANGE_PCT,
                operator=ConditionOperator.LESS_THAN,
                threshold=-150.0,
            ).validate()


class TestExplainabilityAndCompliance:
    """Türkçe anlaşılır açıklama ve SPK mevzuat uyumu testleri."""

    def test_explainable_description_structure(self) -> None:
        cond = AlertCondition(
            condition_type=ConditionType.RSI_THRESHOLD,
            operator=ConditionOperator.CROSSES_ABOVE,
            threshold=70.0,
            period=14,
        )
        exp = cond.explain()

        assert exp.condition_type == ConditionType.RSI_THRESHOLD
        assert "70.0" in exp.title
        assert "RSI" in exp.indicator_name
        assert "momentum" in exp.summary.lower()
        assert "tetiklenir" in exp.when_triggers
        assert "tetiklenmez" in exp.invalidation_conditions
        assert exp.disclaimer == LEGAL_DISCLAIMER_RULES

        # Açıklama metninde kesinlikle yasaklı kelimeler bulunmamalı
        full_text = (
            f" {exp.title} {exp.summary} {exp.when_triggers} {exp.invalidation_conditions} "
        ).lower()
        for prohibited in PROHIBITED_TERMS:
            assert prohibited not in full_text, f"Yasaklı terim bulundu: {prohibited}"

    def test_whole_rule_explanation(self) -> None:
        rule = ExplainableRuleBuilder.create_rsi_alert(
            name="THYAO Aşırı Momentum Takibi",
            user_id=1,
            symbol="THYAO",
            threshold=75.0,
            period=14,
            is_upper_bound=True,
            cooldown_minutes=90,
        )
        explanation = rule.explain()

        assert explanation.rule_name == "THYAO Aşırı Momentum Takibi"
        assert explanation.symbol == "THYAO"
        assert "THYAO" in explanation.overall_summary
        assert "90 dakika" in explanation.cooldown_info
        assert len(explanation.condition_explanations) == 1
        assert explanation.legal_disclaimer == LEGAL_DISCLAIMER_RULES

    def test_prohibited_terms_rejected_in_rule_config(self) -> None:
        cond = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=100.0,
        )

        # Kural adında "kesin al" geçtiğinde engellenmeli
        with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
            AlertRuleConfig(
                name="THYAO Kesin Al Kuralı",
                user_id=1,
                symbol="THYAO",
                conditions=[cond],
            ).validate()

        # Kullanıcı notunda "hedef fiyat" veya "garanti" geçtiğinde engellenmeli
        with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
            AlertRuleConfig(
                name="THYAO Eşik Takibi",
                user_id=1,
                symbol="THYAO",
                conditions=[cond],
                user_note="Bu seviye hedef fiyattır.",
            ).validate()


class TestConditionEvaluation:
    """Fiyat serisi üzerinde koşul tetiklenme mantığı testleri."""

    def test_price_above_evaluation(self) -> None:
        df = _create_sample_df(n_bars=30, trend="bullish")
        last_price = float(df["close"].iloc[-1])

        # Eşik son fiyattan küçük -> True dönmeli
        cond_true = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=last_price - 5.0,
        )
        assert cond_true.evaluate(df) is True

        # Eşik son fiyattan büyük -> False dönmeli
        cond_false = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=last_price + 5.0,
        )
        assert cond_false.evaluate(df) is False

    def test_volume_spike_evaluation(self) -> None:
        df = _create_sample_df(n_bars=40, trend="neutral")
        # Son gün hacmini 3 katına çıkar
        df.loc[df.index[-1], "volume"] = 350_000.0

        cond_spike = AlertCondition(
            condition_type=ConditionType.VOLUME_SPIKE,
            operator=ConditionOperator.GREATER_THAN_OR_EQUAL,
            threshold=2.0,
            period=20,
        )
        assert cond_spike.evaluate(df) is True

        # 5 katı eşiği konursa False dönmeli
        cond_high_spike = AlertCondition(
            condition_type=ConditionType.VOLUME_SPIKE,
            operator=ConditionOperator.GREATER_THAN_OR_EQUAL,
            threshold=5.0,
            period=20,
        )
        assert cond_high_spike.evaluate(df) is False

    def test_logical_operators_all_and_any(self) -> None:
        df = _create_sample_df(n_bars=40, trend="bullish")
        last_price = float(df["close"].iloc[-1])

        c_true = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=last_price - 2.0,
        )
        c_false = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=last_price + 10.0,
        )

        # ALL mantığı: Biri False ise kural tetiklenmemeli
        rule_all = AlertRuleConfig(
            name="Çoklu Koşul VE",
            user_id=1,
            symbol="EREGL",
            conditions=[c_true, c_false],
            logical_operator=LogicalOperator.ALL,
        )
        triggered, reasons = rule_all.evaluate(df)
        assert triggered is False
        assert len(reasons) == 0

        # ANY mantığı: Biri True ise kural tetiklenmeli
        rule_any = AlertRuleConfig(
            name="Çoklu Koşul VEYA",
            user_id=1,
            symbol="EREGL",
            conditions=[c_true, c_false],
            logical_operator=LogicalOperator.ANY,
        )
        triggered_any, reasons_any = rule_any.evaluate(df)
        assert triggered_any is True
        assert len(reasons_any) == 1


class TestSerializationAndBuilder:
    """Serileştirme ve fabrika şablonları testleri."""

    def test_to_and_from_dict(self) -> None:
        rule = ExplainableRuleBuilder.create_ma_crossover_alert(
            name="SMA 20/50 Kesişimi",
            user_id=42,
            symbol="ASELS",
            fast_period=20,
            slow_period=50,
            ma_type="SMA",
            bullish_cross=True,
            cooldown_minutes=120,
        )

        d = rule.to_dict()
        assert d["name"] == "SMA 20/50 Kesişimi"
        assert d["user_id"] == 42
        assert d["symbol"] == "ASELS"
        assert len(d["conditions"]) == 1

        restored = AlertRuleConfig.from_dict(d)
        restored.validate()
        assert restored.name == rule.name
        assert restored.conditions[0].period == 20
        assert restored.conditions[0].secondary_period == 50

    def test_preset_templates_availability(self) -> None:
        templates = ExplainableRuleBuilder.get_preset_templates()
        assert len(templates) >= 4

        for tmpl in templates:
            assert "template_id" in tmpl
            assert "title" in tmpl
            assert "explanation" in tmpl
            # Açıklamada SPK yasağı olmamalı
            norm = f" {tmpl['explanation'].lower()} "
            for p in PROHIBITED_TERMS:
                assert p not in norm
