"""Unit and integration test suite for LLM cost tracker, token accounting, and budget alarms."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability.cost_tracker import (
    COST_TRACKER,
    BudgetAlert,
    BudgetAlertLevel,
    BudgetConfig,
    BudgetScope,
    CostTracker,
    ModelPricing,
    hash_user_identifier,
)
from stock_analysis.observability.metrics import (
    COST_CACHE_HIT_RATIO_GAUGE,
    COST_LLM_CALLS_TOTAL,
    COST_LLM_TOKENS_TOTAL,
)


@pytest.fixture
def clean_tracker() -> CostTracker:
    """Fixture providing an isolated CostTracker instance."""
    config = BudgetConfig(
        daily_global_budget_usd=10.0,
        monthly_global_budget_usd=100.0,
        daily_user_budget_usd=1.0,
        warning_threshold_ratio=0.80,
        hard_limit_threshold_ratio=1.00,
        cache_hit_target_ratio=0.80,
    )
    return CostTracker(budget_config=config)


@pytest.fixture
def client() -> TestClient:
    """Fixture providing FastAPI test client."""
    return TestClient(create_app())


class TestModelPricing:
    """Test model pricing calculation and catalog."""

    def test_pricing_calculation(self) -> None:
        pricing = ModelPricing(prompt_cost_per_million=1.0, completion_cost_per_million=2.0)
        # 500,000 prompt tokens = $0.50, 250,000 completion = $0.50 -> $1.00
        cost = pricing.calculate_cost(prompt_tokens=500_000, completion_tokens=250_000)
        assert cost == 1.0

    def test_custom_pricing_registration(self, clean_tracker: CostTracker) -> None:
        clean_tracker.register_model_pricing("custom-llm", ModelPricing(0.50, 1.50))
        rec = clean_tracker.record_call(
            feature="test_feat",
            model="custom-llm",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        assert rec.cost_usd == 2.0


class TestCostAndTokenTracking:
    """Test call recording, token consumption, and cache hits."""

    def test_record_non_cached_call(self, clean_tracker: CostTracker) -> None:
        rec = clean_tracker.record_call(
            feature="market_summary",
            model="gemini-1.5-flash",
            prompt_tokens=1000,
            completion_tokens=200,
            cached=False,
            user_id="user_123",
        )
        assert rec.feature == "market_summary"
        assert rec.total_tokens == 1200
        assert rec.cached is False
        assert rec.cost_usd > 0.0
        assert rec.user_hash == hash_user_identifier("user_123")

        feat = clean_tracker.get_feature_metrics("market_summary")
        assert feat is not None
        assert feat.total_calls == 1
        assert feat.cache_hits == 0
        assert feat.cache_misses == 1
        assert feat.prompt_tokens == 1000
        assert feat.completion_tokens == 200
        assert feat.total_tokens == 1200
        assert feat.cache_hit_ratio == 0.0

    def test_record_cached_call(self, clean_tracker: CostTracker) -> None:
        rec = clean_tracker.record_call(
            feature="market_summary",
            model="gemini-1.5-flash",
            prompt_tokens=1000,
            completion_tokens=200,
            cached=True,
            user_id="user_123",
        )
        assert rec.cached is True
        assert rec.cost_usd == 0.0
        assert rec.total_tokens == 0  # Cached calls avoid token consumption

        feat = clean_tracker.get_feature_metrics("market_summary")
        assert feat is not None
        assert feat.total_calls == 1
        assert feat.cache_hits == 1
        assert feat.cache_misses == 0
        assert feat.cache_hit_ratio == 1.0


class TestCacheHitRatioAndSla:
    """Test cache hit ratio calculations and SLA target compliance."""

    def test_cache_hit_ratio_target(self, clean_tracker: CostTracker) -> None:
        # 8 cache hits, 2 cache misses -> 80% hit ratio
        for _ in range(8):
            clean_tracker.record_call("anomaly_narration", "gemini-1.5-flash", cached=True)
        for _ in range(2):
            clean_tracker.record_call(
                "anomaly_narration", "gemini-1.5-flash", prompt_tokens=500, cached=False
            )

        ratio = clean_tracker.get_cache_hit_ratio("anomaly_narration")
        assert ratio == 0.80
        assert clean_tracker.is_cache_target_met("anomaly_narration") is True

        # Overall ratio
        assert clean_tracker.get_cache_hit_ratio(None) == 0.80
        assert clean_tracker.is_cache_target_met(None) is True

    def test_cache_target_not_met(self, clean_tracker: CostTracker) -> None:
        clean_tracker.record_call("sentiment", "gemini-1.5-flash", prompt_tokens=500, cached=False)
        assert clean_tracker.is_cache_target_met("sentiment") is False


class TestUserCostAccountingAndPrivacy:
    """Test zero-PII user cost aggregation."""

    def test_user_pseudonymization(self) -> None:
        user_id = "trader_ahmet@example.com"
        h = hash_user_identifier(user_id)
        assert h is not None
        assert len(h) == 16
        assert "ahmet" not in h
        assert "@" not in h

    def test_user_tracking_aggregation(self, clean_tracker: CostTracker) -> None:
        clean_tracker.record_call(
            "forecast_explanation",
            "gemini-1.5-flash",
            prompt_tokens=1000,
            completion_tokens=500,
            user_id="user_abc",
        )
        clean_tracker.record_call(
            "forecast_explanation",
            "gemini-1.5-flash",
            prompt_tokens=2000,
            completion_tokens=500,
            user_id="user_abc",
        )

        user_summary = clean_tracker.get_user_cost("user_abc")
        assert user_summary is not None
        assert user_summary.call_count == 2
        assert user_summary.total_tokens == 4000
        assert user_summary.daily_cost_usd > 0.0

        top_users = clean_tracker.get_top_users_by_cost(limit=5)
        assert len(top_users) == 1
        assert top_users[0].user_hash == hash_user_identifier("user_abc")


class TestBudgetMonitoringAndAlarms:
    """Test warning soft limits, hard limits, and fallback decisions."""

    def test_budget_warning_alarm(self, clean_tracker: CostTracker) -> None:
        alerts: list[BudgetAlert] = []
        clean_tracker.add_alert_handler(alerts.append)

        # Daily limit is $10.00. Warning threshold is 80% ($8.00).
        # Register an expensive model: $10.00 per 1M tokens
        clean_tracker.register_model_pricing("expensive-model", ModelPricing(10.0, 0.0))

        # Call with 850,000 tokens -> $8.50 spent (85% of $10.00)
        clean_tracker.record_call(
            "report_generation",
            "expensive-model",
            prompt_tokens=850_000,
            cached=False,
        )

        assert len(alerts) >= 1
        warning_alert = next((a for a in alerts if a.level == BudgetAlertLevel.WARNING), None)
        assert warning_alert is not None
        assert warning_alert.scope == BudgetScope.GLOBAL_DAILY
        assert warning_alert.usage_ratio >= 0.80

    def test_budget_hard_limit_and_fallback(self, clean_tracker: CostTracker) -> None:
        alerts: list[BudgetAlert] = []
        clean_tracker.add_alert_handler(alerts.append)

        clean_tracker.register_model_pricing("expensive-model", ModelPricing(10.0, 0.0))

        # Spend $10.50 (105% of $10.00 daily budget)
        clean_tracker.record_call(
            "report_generation",
            "expensive-model",
            prompt_tokens=1_050_000,
            cached=False,
        )

        exhausted_alert = next((a for a in alerts if a.level == BudgetAlertLevel.EXHAUSTED), None)
        assert exhausted_alert is not None

        # Verify fallback rule enforcement
        allowed, reason = clean_tracker.should_allow_call("report_generation")
        assert allowed is False
        assert "Falling back to rule-based summary without LLM" in reason

    def test_user_daily_budget_exhaustion(self, clean_tracker: CostTracker) -> None:
        # User limit is $1.00
        clean_tracker.register_model_pricing("expensive-model", ModelPricing(10.0, 0.0))

        # Spend $1.20 for user_1
        clean_tracker.record_call(
            "qa_chat",
            "expensive-model",
            prompt_tokens=120_000,
            user_id="user_1",
            cached=False,
        )

        allowed, reason = clean_tracker.should_allow_call("qa_chat", user_id="user_1")
        assert allowed is False
        assert "User daily budget exhausted" in reason

        # Another user is still allowed
        allowed_other, _ = clean_tracker.should_allow_call("qa_chat", user_id="user_2")
        assert allowed_other is True

    def test_reset_daily_counters(self, clean_tracker: CostTracker) -> None:
        clean_tracker.register_model_pricing("expensive-model", ModelPricing(10.0, 0.0))
        clean_tracker.record_call(
            "qa_chat",
            "expensive-model",
            prompt_tokens=1_000_000,
            cached=False,
        )
        assert clean_tracker.should_allow_call("qa_chat")[0] is False

        clean_tracker.reset_daily_counters()
        assert clean_tracker.should_allow_call("qa_chat")[0] is True


class TestPrometheusMetricsAndDashboard:
    """Test Prometheus metric updates and HTML rendering."""

    def test_prometheus_metrics_updated(self, clean_tracker: CostTracker) -> None:
        clean_tracker.record_call(
            "market_summary",
            "gemini-1.5-flash",
            prompt_tokens=500,
            completion_tokens=100,
            cached=False,
        )

        calls = COST_LLM_CALLS_TOTAL.get(
            {"feature": "market_summary", "model": "gemini-1.5-flash", "cached": "miss"}
        )
        assert calls >= 1.0

        p_tokens = COST_LLM_TOKENS_TOTAL.get(
            {"feature": "market_summary", "model": "gemini-1.5-flash", "type": "prompt"}
        )
        assert p_tokens >= 500.0

        ratio = COST_CACHE_HIT_RATIO_GAUGE.get({"feature": "market_summary"})
        assert ratio == 0.0

    def test_html_dashboard_generation(self, clean_tracker: CostTracker) -> None:
        clean_tracker.record_call(
            "market_summary",
            "gemini-1.5-flash",
            prompt_tokens=800,
            completion_tokens=200,
            cached=False,
            user_id="user_test",
        )
        html_page = clean_tracker.generate_html_dashboard()
        assert "<!DOCTYPE html>" in html_page
        assert "BoraI — LLM Maliyet & Token Panosu" in html_page
        assert "market_summary" in html_page
        assert "user_test" not in html_page  # raw PII not present
        assert "Günlük Bütçe Tüketimi" in html_page


class TestFastApiCostEndpoints:
    """Test FastAPI /cost endpoints."""

    def test_get_cost_summary_endpoint(self, client: TestClient) -> None:
        # Record a call into global singleton
        COST_TRACKER.record_call(
            feature="api_test_feature",
            model="gemini-1.5-flash",
            prompt_tokens=400,
            completion_tokens=100,
            cached=False,
        )

        res = client.get("/cost")
        assert res.status_code == 200
        data = res.json()
        assert "summary" in data
        assert "budget" in data
        assert "features" in data
        assert "api_test_feature" in data["features"]

    def test_get_cost_features_endpoint(self, client: TestClient) -> None:
        res = client.get("/cost/features")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, dict)
        assert "api_test_feature" in data

    def test_get_cost_alerts_endpoint(self, client: TestClient) -> None:
        res = client.get("/cost/alerts")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)

    def test_get_cost_html_endpoint(self, client: TestClient) -> None:
        res = client.get("/cost/html")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert (
            "LLM Maliyet &amp; Token Panosu" in res.text or "LLM Maliyet & Token Panosu" in res.text
        )
