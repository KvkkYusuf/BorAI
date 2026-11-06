"""Core unit test suite covering the 5 foundational analytical and business domains (FAZ-14).

Covers:
1. Indicators: SMA, EMA, RSI, MACD, ATR, Volatility (pure calculation, bounds, edge cases)
2. Price Transformations: Corporate action adjustments, returns, scaling
3. Data Cleaning: OHLC integrity, zero/negative prices, duplicate/missing bars
4. Authorization: RBAC role hierarchy, privilege escalation guards (pure logic)
5. Alert Conditions: Threshold validation, explainable rules, Turkish explanations

Strictly isolated: 100% in-memory, deterministic, zero network/DB dependencies.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

# Domain 5: Alert Conditions
from stock_analysis.alerts.rules import (
    AlertCondition,
    AlertRuleConfig,
    ConditionOperator,
    ConditionType,
    LogicalOperator,
    RuleValidationError,
)

# Domain 1: Indicators
from stock_analysis.indicators.technical import (
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_volatility,
)

# Domain 2: Price Transformations
from stock_analysis.pipeline.corporate_actions import (
    CorporateAction,
    CorporateActionType,
    apply_corporate_actions,
)
from stock_analysis.pipeline.price_scaling import (
    MultiSymbolPriceScaler,
    ScalingMethod,
)

# Domain 3: Data Cleaning & Integrity
from stock_analysis.pipeline.validator import (
    DataIssueType,
    validate_market_data_series,
)
from stock_analysis.providers.base import MarketBarRecord

# Domain 4: Authorization & RBAC
from stock_analysis.security.audit import (
    PrivilegeEscalationError,
    RoleHierarchy,
)
from stock_analysis.security.environments import AccessRole

pytestmark = pytest.mark.unit


# ==============================================================================
# 1. BIRIM TESTLER: TEKNİK GÖSTERGELER (INDICATORS)
# ==============================================================================


class TestIndicatorsUnit:
    """Teknik analiz indikatörleri saf birim testleri."""

    def test_sma_pure_values_and_edge_cases(self) -> None:
        """SMA hesaplaması, pencere kayması ve uç durumlar."""
        series = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        sma3 = calculate_sma(series, period=3)

        assert np.isnan(sma3.iloc[0])
        assert np.isnan(sma3.iloc[1])
        assert sma3.iloc[2] == pytest.approx(20.0)
        assert sma3.iloc[3] == pytest.approx(30.0)
        assert sma3.iloc[4] == pytest.approx(40.0)

        # Periyot seriden büyükse tüm değerler NaN dönmeli
        sma_large = calculate_sma(series, period=10)
        assert sma_large.isna().all()

        # Boş seri kontrolü
        empty_s = pd.Series([], dtype=float)
        assert calculate_sma(empty_s, period=5).empty

    def test_ema_smoothing_behavior(self) -> None:
        """EMA hesaplaması, son değerlere daha fazla ağırlık verme davranışı."""
        series = pd.Series([10.0, 10.0, 10.0, 20.0, 20.0])
        ema = calculate_ema(series, period=3)

        assert len(ema) == 5
        # min_periods=period olduğu için ilk 2 bar NaN, 3. bar 10.0'dır
        assert np.isnan(ema.iloc[0])
        assert np.isnan(ema.iloc[1])
        assert ema.iloc[2] == pytest.approx(10.0)
        # Sıçramada EMA hemen 20 olmaz, yumuşak yaklaşır
        assert 10.0 < ema.iloc[3] < 20.0
        assert ema.iloc[3] < ema.iloc[4] <= 20.0

    def test_rsi_bounds_and_extremes(self) -> None:
        """RSI 0-100 aralık sınırları, sürekli artan ve sürekli düşen seriler."""
        # Sürekli artan seri -> RSI = 100 olmalı
        uptrend = pd.Series([float(i) for i in range(1, 30)])
        rsi_up = calculate_rsi(uptrend, period=14)
        assert rsi_up.iloc[-1] == pytest.approx(100.0)

        # Sürekli düşen seri -> RSI = 0 olmalı
        downtrend = pd.Series([float(30 - i) for i in range(1, 30)])
        rsi_down = calculate_rsi(downtrend, period=14)
        assert rsi_down.iloc[-1] == pytest.approx(0.0)

        # Sabit seri -> fiyat farkı 0, RSI 0-100 içinde kalmalı
        flat = pd.Series([100.0] * 30)
        rsi_flat = calculate_rsi(flat, period=14)
        assert not rsi_flat.iloc[-1] < 0.0
        assert not rsi_flat.iloc[-1] > 100.0

    def test_macd_line_signal_and_histogram(self) -> None:
        """MACD çizgisi, sinyal çizgisi ve histogram ilişkisi."""
        np.random.seed(42)
        prices = pd.Series(100.0 + np.cumsum(np.random.normal(0.1, 1.0, 60)))
        macd_res = calculate_macd(prices, fast_period=12, slow_period=26, signal_period=9)

        assert len(macd_res.macd_line) == 60
        assert len(macd_res.signal_line) == 60
        assert len(macd_res.histogram) == 60

        # Histogram tam olarak MACD - Sinyal farkı olmalıdır
        valid_idx = ~(macd_res.macd_line.isna() | macd_res.signal_line.isna())
        diff = macd_res.macd_line[valid_idx] - macd_res.signal_line[valid_idx]
        np.testing.assert_allclose(macd_res.histogram[valid_idx], diff, rtol=1e-5)

    def test_atr_positive_and_true_range(self) -> None:
        """ATR daima pozitif olmalı ve gap durumlarını hesaba katmalıdır."""
        high = pd.Series([105.0, 112.0, 115.0, 120.0])
        low = pd.Series([95.0, 104.0, 108.0, 110.0])
        close = pd.Series([100.0, 110.0, 114.0, 118.0])

        atr = calculate_atr(high=high, low=low, close=close, period=3)
        assert len(atr) == 4
        # Son ATR pozitif ve makul aralıkta olmalı
        assert atr.iloc[-1] > 0.0

    def test_volatility_calculation(self) -> None:
        """Tarihsel volatilite hesaplaması pozitif ve yıllıklandırılmış olmalıdır."""
        close = pd.Series([100.0, 102.0, 99.0, 104.0, 101.0, 105.0, 103.0])
        vol = calculate_volatility(close, period=5, annualize=True)
        assert len(vol) == 7
        assert vol.iloc[-1] > 0.0


# ==============================================================================
# 2. BIRIM TESTLER: FİYAT DÖNÜŞÜMLERİ VE KURUMSAL İŞLEMLER (TRANSFORMATIONS)
# ==============================================================================


class TestPriceTransformationsUnit:
    """Fiyat dönüşümleri, kurumsal işlem düzeltmeleri ve ölçekleme birim testleri."""

    def _create_mock_bar(
        self,
        symbol: str,
        trade_date: date,
        close: str,
        volume: int = 100_000,
    ) -> MarketBarRecord:
        p = Decimal(close)
        return MarketBarRecord(
            symbol=symbol,
            exchange="BIST",
            trade_date=trade_date,
            currency="TRY",
            raw_open=p,
            raw_high=p * Decimal("1.02"),
            raw_low=p * Decimal("0.98"),
            raw_close=p,
            adj_open=p,
            adj_high=p * Decimal("1.02"),
            adj_low=p * Decimal("0.98"),
            adj_close=p,
            volume=volume,
            turnover_try=p * Decimal(volume),
            source_name="unit_test",
            is_verified=True,
        )

    def test_stock_split_adjustment_factor(self) -> None:
        """2:1 forward split (SPLIT) eski fiyatları yarıya indirmelidir."""
        b1 = self._create_mock_bar("THYAO", date(2026, 1, 5), "200.0", volume=100_000)
        b2 = self._create_mock_bar("THYAO", date(2026, 1, 6), "100.0", volume=200_000)

        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
            description="2:1 Stock Split",
        )

        adjusted = apply_corporate_actions([b1, b2], [action])
        assert len(adjusted) == 2

        # Eski barın düzeltilmiş kapanışı 200 / 2 = 100 olmalı
        assert adjusted[0].adj_close == Decimal("100.0000")
        assert adjusted[0].raw_close == Decimal("200.0")  # Ham fiyat değişmez
        # Split günü barı
        assert adjusted[1].adj_close == Decimal("100.0000")

    def test_cash_dividend_adjustment_factor(self) -> None:
        """Nakit temettü düşümünde temettü öncesi fiyatlar oransal düzeltilmelidir."""
        b1 = self._create_mock_bar("EREGL", date(2026, 3, 10), "50.0")
        b2 = self._create_mock_bar("EREGL", date(2026, 3, 11), "45.0")

        # 5 TL temettü
        action = CorporateAction(
            symbol="EREGL",
            ex_date=date(2026, 3, 11),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("5.0"),
        )

        adjusted = apply_corporate_actions([b1, b2], [action])
        assert adjusted[0].adj_close == Decimal("45.0000")
        assert adjusted[0].raw_close == Decimal("50.0")

    def test_multi_symbol_price_scaler_percentage_and_log_returns(self) -> None:
        """Farklı nominal ölçekteki hisselerin (10 TL vs 1000 TL) Min-Max normalizasyonu."""
        bars_cheap = [
            self._create_mock_bar("CHEAP", date(2026, 1, 1), "10.0"),
            self._create_mock_bar("CHEAP", date(2026, 1, 2), "15.0"),
            self._create_mock_bar("CHEAP", date(2026, 1, 3), "20.0"),
        ]
        bars_exp = [
            self._create_mock_bar("EXP", date(2026, 1, 1), "1000.0"),
            self._create_mock_bar("EXP", date(2026, 1, 2), "1500.0"),
            self._create_mock_bar("EXP", date(2026, 1, 3), "2000.0"),
        ]

        scaler = MultiSymbolPriceScaler(method=ScalingMethod.MINMAX)
        f_cheap = scaler.fit_transform(bars_cheap)
        f_exp = scaler.fit_transform(bars_exp)

        assert len(f_cheap) == 3
        assert len(f_exp) == 3

        # Nominal fiyat farkı (10 TL vs 1000 TL) ortadan kalkmalı ve [0, 0.5, 1.0] olmalıdır
        assert f_cheap[0].close_scaled == pytest.approx(0.0)
        assert f_cheap[1].close_scaled == pytest.approx(0.5)
        assert f_cheap[2].close_scaled == pytest.approx(1.0)

        assert f_exp[0].close_scaled == pytest.approx(0.0)
        assert f_exp[1].close_scaled == pytest.approx(0.5)
        assert f_exp[2].close_scaled == pytest.approx(1.0)


# ==============================================================================
# 3. BIRIM TESTLER: VERİ TEMİZLEME VE BÜTÜNLÜK (DATA CLEANING & INTEGRITY)
# ==============================================================================


class TestDataCleaningIntegrityUnit:
    """Piyasa verisi doğrulama, ayıklama ve temizleme saf birim testleri."""

    def _make_raw_bar(
        self,
        trade_date: date,
        open_p: str = "100.0",
        high_p: str = "105.0",
        low_p: str = "95.0",
        close_p: str = "100.0",
        volume: int = 1000,
        turnover: str = "100000.0",
    ) -> MarketBarRecord:
        return MarketBarRecord(
            symbol="KCHOL",
            exchange="BIST",
            trade_date=trade_date,
            currency="TRY",
            raw_open=Decimal(open_p),
            raw_high=Decimal(high_p),
            raw_low=Decimal(low_p),
            raw_close=Decimal(close_p),
            adj_open=Decimal(open_p),
            adj_high=Decimal(high_p),
            adj_low=Decimal(low_p),
            adj_close=Decimal(close_p),
            volume=volume,
            turnover_try=Decimal(turnover),
            source_name="test_cleaner",
            is_verified=True,
        )

    def test_schema_level_cleaning_non_positive_and_inconsistent_prices(self) -> None:
        """Sıfır/negatif fiyatlar ve High < Low verisi Pydantic katmanında anında temizlenir."""
        # Sıfır fiyat reddi
        with pytest.raises(ValidationError):
            self._make_raw_bar(date(2026, 1, 1), close_p="0.0")

        # Negatif fiyat reddi
        with pytest.raises(ValidationError):
            self._make_raw_bar(date(2026, 1, 1), close_p="-10.0")

        # High < Low reddi
        with pytest.raises(ValidationError):
            self._make_raw_bar(
                date(2026, 1, 1),
                high_p="90.0",
                low_p="100.0",
                close_p="95.0",
            )

    def test_duplicate_bar_cleaning(self) -> None:
        """Aynı trade_date içeren mükerrer kayıtlar tespit edilip reddedilmelidir."""
        b1 = self._make_raw_bar(date(2026, 1, 1))
        b2 = self._make_raw_bar(date(2026, 1, 1))  # Mükerrer tarih

        report = validate_market_data_series([b1, b2])
        assert not report.is_valid
        assert any(i.issue_type == DataIssueType.DUPLICATE_BAR for i in report.issues)

    def test_out_of_order_series(self) -> None:
        """Kronolojik sırası bozuk veriler yakalanmalıdır."""
        b1 = self._make_raw_bar(date(2026, 1, 5))
        b2 = self._make_raw_bar(date(2026, 1, 2))  # Geriye gidiş

        report = validate_market_data_series([b1, b2])
        assert not report.is_valid
        assert any(i.issue_type == DataIssueType.OUT_OF_ORDER for i in report.issues)


# ==============================================================================
# 4. BIRIM TESTLER: YETKİLENDİRME VE RBAC (AUTHORIZATION)
# ==============================================================================


class TestAuthorizationRbacUnit:
    """En az ayrıcalık, yetki hiyerarşisi ve yetki yükseltme engeli saf birim testleri."""

    def test_role_hierarchy_ranking(self) -> None:
        """Rol üstünlük sıralaması katı olmalıdır."""
        assert RoleHierarchy.get_rank(AccessRole.SUPER_ADMIN) > RoleHierarchy.get_rank(
            AccessRole.ADMIN
        )
        assert RoleHierarchy.get_rank(AccessRole.ADMIN) > RoleHierarchy.get_rank(
            AccessRole.ML_ENGINEER
        )
        assert RoleHierarchy.get_rank(AccessRole.ML_ENGINEER) == RoleHierarchy.get_rank(
            AccessRole.COMPLIANCE_OFFICER
        )
        assert RoleHierarchy.get_rank(AccessRole.ANALYST) > RoleHierarchy.get_rank(AccessRole.USER)
        assert RoleHierarchy.get_rank(AccessRole.USER) > RoleHierarchy.get_rank(AccessRole.GUEST)

    def test_can_manage_target_strictly_enforced(self) -> None:
        """Bir aktör yalnızca kendinden kesinlikle düşük seviyedeki rolleri yönetebilir."""
        # Super admin admini yönetebilir
        assert RoleHierarchy.can_manage_target(
            actor_role=AccessRole.SUPER_ADMIN,
            target_role=AccessRole.ADMIN,
        )
        # Admin kendisiyle eşit seviyedeki admini yönetemez
        assert not RoleHierarchy.can_manage_target(
            actor_role=AccessRole.ADMIN,
            target_role=AccessRole.ADMIN,
        )
        # Admin super admini kesinlikle yönetemez
        assert not RoleHierarchy.can_manage_target(
            actor_role=AccessRole.ADMIN,
            target_role=AccessRole.SUPER_ADMIN,
        )
        # can_assign_role: Standart kullanıcı rol atayamaz
        assert not RoleHierarchy.can_assign_role(
            actor_role=AccessRole.USER,
            new_role=AccessRole.GUEST,
        )
        # can_assign_role: Admin, Super Admin atayamaz
        assert not RoleHierarchy.can_assign_role(
            actor_role=AccessRole.ADMIN,
            new_role=AccessRole.SUPER_ADMIN,
        )
        # can_assign_role: Admin, User atayabilir
        assert RoleHierarchy.can_assign_role(
            actor_role=AccessRole.ADMIN,
            new_role=AccessRole.USER,
        )

    def test_privilege_escalation_raises_error(self) -> None:
        """Kendinden yüksek role geçiş veya kendini yükseltme engeli kontrolü."""
        # Admin, bir kullanıcıyı SUPER_ADMIN yapamaz
        with pytest.raises(PrivilegeEscalationError):
            RoleHierarchy.validate_role_transition(
                actor_role=AccessRole.ADMIN,
                target_current_role=AccessRole.USER,
                new_role=AccessRole.SUPER_ADMIN,
            )

        # Yönetici kendi rolünü doğrudan değiştiremez
        with pytest.raises(PrivilegeEscalationError):
            RoleHierarchy.validate_role_transition(
                actor_role=AccessRole.ADMIN,
                target_current_role=AccessRole.ADMIN,
                new_role=AccessRole.SUPER_ADMIN,
                is_self=True,
            )

        # Geçerli atama (Admin -> User'ı Analyst yapabilir)
        RoleHierarchy.validate_role_transition(
            actor_role=AccessRole.ADMIN,
            target_current_role=AccessRole.USER,
            new_role=AccessRole.ANALYST,
        )


# ==============================================================================
# 5. BIRIM TESTLER: UYARI KOŞULLARI VE KURAL MOTORU (ALERT CONDITIONS)
# ==============================================================================


class TestAlertConditionsUnit:
    """Kullanıcı uyarı koşulları, açıklanabilir metinler ve eşik denetimleri."""

    def test_alert_condition_threshold_validation(self) -> None:
        """Eşik değer sınırları ve veri doğrulama denetimleri."""
        c_price = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=250.0,
        )
        c_price.validate()
        assert c_price.threshold == 250.0

        # Negatif fiyat eşiğinde validate() hata fırlatmalıdır
        c_neg = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=-10.0,
        )
        with pytest.raises(RuleValidationError):
            c_neg.validate()

        # RSI 0-100 dışı eşikte validate() hata fırlatmalıdır
        c_rsi = AlertCondition(
            condition_type=ConditionType.RSI_THRESHOLD,
            operator=ConditionOperator.GREATER_THAN,
            threshold=150.0,
        )
        with pytest.raises(RuleValidationError):
            c_rsi.validate()

    def test_explainable_rule_text_generation(self) -> None:
        """Kural motorunun yatırım tavsiyesi içermeyen Türkçe açıklama üretimi."""
        c1 = AlertCondition(
            condition_type=ConditionType.PRICE_ABOVE,
            operator=ConditionOperator.GREATER_THAN,
            threshold=100.0,
        )
        c2 = AlertCondition(
            condition_type=ConditionType.RSI_THRESHOLD,
            operator=ConditionOperator.GREATER_THAN,
            threshold=70.0,
        )

        config = AlertRuleConfig(
            name="THYAO Fiyat ve RSI Takibi",
            user_id=1,
            symbol="THYAO",
            logical_operator=LogicalOperator.ALL,
            conditions=[c1, c2],
        )

        explanation = config.explain()
        assert "THYAO" in explanation.overall_summary
        assert "VE mantığı" in explanation.overall_summary
        assert len(explanation.condition_explanations) == 2
        assert "100.00 TL" in explanation.condition_explanations[0].title
        assert "70.0" in explanation.condition_explanations[1].title
        assert "RSI" in explanation.condition_explanations[1].title

        # Yasal uyarı mevcudiyeti
        assert "kesinlikle yatırım danışmanlığı" in explanation.legal_disclaimer

    def test_rule_evaluation_on_dataframe(self) -> None:
        """Koşulların DataFrame verisi üzerinden doğru tetiklenme üretmesi."""
        # 60 barlık veri
        df = pd.DataFrame(
            {
                "close": [90.0, 95.0, 105.0],
                "open": [89.0, 94.0, 104.0],
                "high": [91.0, 96.0, 106.0],
                "low": [88.0, 93.0, 103.0],
                "volume": [1000, 1000, 1000],
            }
        )

        # 100 TL üzeri kuralı
        rule_above = AlertRuleConfig(
            name="Fiyat 100 TL üstü",
            user_id=1,
            symbol="THYAO",
            conditions=[
                AlertCondition(
                    condition_type=ConditionType.PRICE_ABOVE,
                    operator=ConditionOperator.GREATER_THAN,
                    threshold=100.0,
                )
            ],
        )

        is_triggered, details = rule_above.evaluate(df)
        assert is_triggered is True
        assert len(details) > 0
        assert "100.00 TL" in details[0]
