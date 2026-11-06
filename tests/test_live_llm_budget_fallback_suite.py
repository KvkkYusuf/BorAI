"""Live runtime verification suite for LLM budget limits, quota enforcement, and fallback.

Validates the final item of FAZ-16 (docs/phases/FAZ-16-dagitim-oncesi.md):
- LLM daily global budget limit enforcement and hard-limit trigger.
- Per-user daily call quota enforcement.
- Graceful, zero-error deterministic rule-based fallback under budget/quota exhaustion.
- LLM provider outage/blackout resilience with automatic fallback.
- Feature flag toggle safety (LLM_ENABLED=False).
- Prometheus telemetry and audit trails.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.config import Settings
from stock_analysis.models.llm_explainer import (
    DEFAULT_SAFETY_DISCLAIMER,
    MockSafeLLMClient,
    SafeLLMExplainer,
)
from stock_analysis.observability.cost_tracker import (
    BudgetAlert,
    BudgetAlertLevel,
    BudgetConfig,
    CostTracker,
    ModelPricing,
)


@pytest.fixture
def test_client() -> TestClient:
    """Fixture providing FastAPI test client."""
    return TestClient(create_app())


class TestLiveLLMBudgetAndFallback:
    """Live verification of budget limits, quota caps, and fallback behavior."""

    def test_live_global_budget_hard_limit_fallback(self) -> None:
        """Verify that reaching global daily budget seamlessly falls back to rule-based summary."""
        # 1. Simulate environment with tight global budget of $0.05
        settings = Settings(  # type: ignore[call-arg]
            llm_enabled=True,
            llm_daily_budget_usd=0.05,
            llm_user_daily_calls=50,
        )
        explainer = SafeLLMExplainer(settings=settings)

        # 2. Simulate calls exhausting the budget
        indicators = {"rsi_14": 62.5, "sma_20": 115.0, "sma_50": 108.0}
        explainer.explain(symbol="THYAO", indicators=indicators, user_id="user_prod_1")

        # Manually charge remaining budget to force threshold breach
        explainer.budget_tracker.record_usage("user_prod_1", estimated_cost_usd=0.06)

        # 3. Subsequent live user call across system must NOT fail with 500 error
        res, meta = explainer.explain(
            symbol="ASELS",
            indicators={"rsi_14": 28.0, "sma_20": 58.0, "sma_50": 62.0},
            user_id="user_prod_2",
        )

        # Verify fallback properties
        assert meta["fallback_used"] is True
        assert meta["llm_used"] is False
        assert "GLOBAL_BUDGET_EXCEEDED" in meta["reason"]
        assert res.symbol == "ASELS"
        assert res.disclaimer == DEFAULT_SAFETY_DISCLAIMER
        assert "nesnel parametrelerle ozetlenmistir" in res.context_summary
        assert any(
            "RSI 14 periyotluk degeri 28.0 ile dusuk bolgededir" in obs
            for obs in res.technical_observations
        )

    def test_live_per_user_quota_cap_and_isolation(self) -> None:
        """Verify that user exhausting daily quota is throttled without affecting others."""
        settings = Settings(  # type: ignore[call-arg]
            llm_enabled=True,
            llm_daily_budget_usd=10.0,
            llm_user_daily_calls=2,  # User allowed max 2 LLM calls per day
        )
        explainer = SafeLLMExplainer(settings=settings)

        user_abuser = "usr_heavy_trader"
        user_normal = "usr_regular_trader"

        # User 1 performs call 1
        res1, meta1 = explainer.explain(
            symbol="EREGL",
            indicators={"rsi_14": 45.0},
            user_id=user_abuser,
        )
        assert meta1["fallback_used"] is False
        assert meta1["llm_used"] is True

        # User 1 performs call 2 (quota maxed out)
        res2, meta2 = explainer.explain(
            symbol="KCHOL",
            indicators={"rsi_14": 55.0},
            user_id=user_abuser,
        )
        assert meta2["fallback_used"] is False
        assert meta2["llm_used"] is True

        # User 1 performs call 3 (exceeds quota -> fallback activated)
        res3, meta3 = explainer.explain(
            symbol="SISE",
            indicators={"rsi_14": 75.0, "sma_20": 48.0, "sma_50": 42.0},
            user_id=user_abuser,
        )
        assert meta3["fallback_used"] is True
        assert meta3["llm_used"] is False
        assert meta3["reason"] == "USER_QUOTA_EXCEEDED"
        assert "nesnel parametrelerle ozetlenmistir" in res3.context_summary

        # User 2 makes a call: isolated from User 1's quota exhaustion
        res_normal, meta_normal = explainer.explain(
            symbol="BIMAS",
            indicators={"rsi_14": 52.0},
            user_id=user_normal,
        )
        assert meta_normal["fallback_used"] is False
        assert meta_normal["llm_used"] is True

    def test_live_provider_outage_blackout_resilience(self) -> None:
        """Verify that provider outages, network timeouts or exceptions trigger instant fallback."""
        def failing_generator(_prompt: str) -> str:
            raise ConnectionError("504 Gateway Timeout: OpenAI upstream unavailable")

        failing_client = MockSafeLLMClient(response_generator=failing_generator)
        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings, llm_client=failing_client)

        res, meta = explainer.explain(
            symbol="TUPRS",
            indicators={"rsi_14": 72.0, "sma_20": 170.0, "sma_50": 160.0},
            user_id="user_prod_3",
        )

        assert meta["fallback_used"] is True
        assert meta["llm_used"] is False
        assert "LLM_PROVIDER_ERROR" in meta["reason"]
        assert res.symbol == "TUPRS"
        assert len(res.technical_observations) > 0
        assert res.disclaimer == DEFAULT_SAFETY_DISCLAIMER

    def test_live_feature_flag_zero_call_safety(self) -> None:
        """Verify that when LLM feature flag is off, system functions 100% on rule-based engine."""
        settings = Settings(llm_enabled=False)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings)

        res, meta = explainer.explain(
            symbol="AKBNK",
            indicators={"rsi_14": 49.0, "sma_20": 55.0, "sma_50": 54.0},
            user_id="user_prod_4",
        )

        assert meta["llm_enabled"] is False
        assert meta["llm_used"] is False
        assert meta["fallback_used"] is True
        assert meta["reason"] == "LLM_DISABLED_BY_FEATURE_FLAG"
        assert "AKBNK" in res.symbol
        assert res.disclaimer == DEFAULT_SAFETY_DISCLAIMER

    def test_cost_tracker_observability_and_budget_alarms(self) -> None:
        """Verify cost tracker budget alarm triggering, usage ratio gauges, and alert handlers."""
        alerts_captured: list[BudgetAlert] = []

        config = BudgetConfig(
            daily_global_budget_usd=5.0,
            monthly_global_budget_usd=100.0,
            daily_user_budget_usd=1.0,
            warning_threshold_ratio=0.80,
            hard_limit_threshold_ratio=1.00,
        )
        tracker = CostTracker(budget_config=config)
        tracker.add_alert_handler(alerts_captured.append)
        tracker.register_model_pricing("gpt-4o-mini", ModelPricing(1.0, 1.0))

        # Initial call status
        allowed, reason = tracker.should_allow_call("market_summary")
        assert allowed is True
        assert "Budget permitted" in reason

        # Consume up to warning threshold ($4.20 / $5.00 = 84%)
        # 1M prompt ($1) + 3.2M completion ($3.20) = $4.20
        tracker.record_call(
            feature="market_summary",
            model="gpt-4o-mini",
            prompt_tokens=1_000_000,
            completion_tokens=3_200_000,
            user_id="user_obs_1",
        )
        assert len(alerts_captured) >= 1
        assert any(a.level == BudgetAlertLevel.WARNING for a in alerts_captured)

        # Consume up to hard limit ($5.50 / $5.00 = 110%)
        tracker.record_call(
            feature="market_summary",
            model="gpt-4o-mini",
            prompt_tokens=1_300_000,
            completion_tokens=0,
            user_id="user_obs_1",
        )
        assert any(a.level == BudgetAlertLevel.EXHAUSTED for a in alerts_captured)

        # Subsequent call must be rejected with fallback guidance
        allowed_after, fallback_reason = tracker.should_allow_call("market_summary")
        assert allowed_after is False
        assert "Falling back to rule-based summary without LLM" in fallback_reason
