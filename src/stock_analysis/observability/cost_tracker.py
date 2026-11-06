"""LLM Token, cost accounting, cache hit ratio tracking, and budget alert governance.

Implements token usage tracking, cache hit ratio telemetry, privacy-preserving
per-user cost accounting, budget warning/hard-limit alarms, and rule-based
fallback decision enforcement as specified in docs/TOKEN_EFFICIENCY.md and
.agent/rules/token-ve-llm.md.
"""

from __future__ import annotations

import collections
import datetime
import hashlib
import html
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    COST_BUDGET_ALERTS_TOTAL,
    COST_BUDGET_USAGE_RATIO_GAUGE,
    COST_CACHE_HIT_RATIO_GAUGE,
    COST_LLM_CALLS_TOTAL,
    COST_LLM_TOKENS_TOTAL,
    COST_TOTAL_DOLLARS_COUNTER,
)


class BudgetAlertLevel(StrEnum):
    """Severity levels for budget consumption and financial limits."""

    NORMAL = "normal"
    INFO = "info"
    WARNING = "warning"  # Soft limit (e.g. 80%)
    EXHAUSTED = "exhausted"  # Hard limit (100%+, calls blocked)


class BudgetScope(StrEnum):
    """Scope dimension of a budget boundary."""

    GLOBAL_DAILY = "global_daily"
    GLOBAL_MONTHLY = "global_monthly"
    USER_DAILY = "user_daily"
    FEATURE_DAILY = "feature_daily"


@dataclass(frozen=True)
class ModelPricing:
    """Pricing configuration in USD per 1,000,000 tokens."""

    prompt_cost_per_million: float
    completion_cost_per_million: float

    def calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate total USD cost for given prompt and completion tokens."""
        prompt_cost = (prompt_tokens / 1_000_000.0) * self.prompt_cost_per_million
        completion_cost = (completion_tokens / 1_000_000.0) * self.completion_cost_per_million
        return round(prompt_cost + completion_cost, 6)


# Default model catalog pricing
DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-1.5-flash": ModelPricing(
        prompt_cost_per_million=0.075, completion_cost_per_million=0.30
    ),
    "gemini-1.5-pro": ModelPricing(prompt_cost_per_million=3.50, completion_cost_per_million=10.50),
    "gpt-4o-mini": ModelPricing(prompt_cost_per_million=0.15, completion_cost_per_million=0.60),
    "gpt-4o": ModelPricing(prompt_cost_per_million=2.50, completion_cost_per_million=10.00),
    "claude-3-5-sonnet": ModelPricing(
        prompt_cost_per_million=3.00, completion_cost_per_million=15.00
    ),
    "mock-model": ModelPricing(prompt_cost_per_million=0.10, completion_cost_per_million=0.20),
}
FALLBACK_MODEL_PRICING = ModelPricing(
    prompt_cost_per_million=0.15, completion_cost_per_million=0.60
)


@dataclass(frozen=True)
class BudgetAlert:
    """Event emitted when a budget boundary threshold is exceeded."""

    alert_id: str
    timestamp: datetime.datetime
    level: BudgetAlertLevel
    scope: BudgetScope
    target: str
    current_spent_usd: float
    budget_limit_usd: float
    usage_ratio: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Convert budget alert to dictionary representation."""
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "scope": self.scope.value,
            "target": self.target,
            "current_spent_usd": round(self.current_spent_usd, 4),
            "budget_limit_usd": round(self.budget_limit_usd, 4),
            "usage_ratio": round(self.usage_ratio, 4),
            "message": self.message,
        }


@dataclass
class BudgetConfig:
    """Budget limits and alarm thresholds configuration."""

    daily_global_budget_usd: float = 25.0
    monthly_global_budget_usd: float = 500.0
    daily_user_budget_usd: float = 1.0
    warning_threshold_ratio: float = 0.80  # 80% soft limit
    hard_limit_threshold_ratio: float = 1.00  # 100% hard ceiling
    cache_hit_target_ratio: float = 0.80  # Target cache hit ratio >= 80%


@dataclass(frozen=True)
class CostRecord:
    """Telemetry record for an individual LLM or analytical operation."""

    record_id: str
    timestamp: datetime.datetime
    feature: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    cached: bool
    user_hash: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert cost record to dictionary representation."""
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp.isoformat(),
            "feature": self.feature,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cached": self.cached,
            "user_hash": self.user_hash,
            "metadata": self.metadata,
        }


@dataclass
class FeatureMetrics:
    """Aggregated usage and cost metrics for a specific product feature."""

    feature: str
    total_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    @property
    def cache_hit_ratio(self) -> float:
        """Calculate cache hit ratio (0.0 to 1.0)."""
        if self.total_calls == 0:
            return 0.0
        return round(self.cache_hits / self.total_calls, 4)

    @property
    def avg_tokens_per_call(self) -> float:
        """Calculate average tokens consumed per non-cached call."""
        if self.cache_misses == 0:
            return 0.0
        return round(self.total_tokens / self.cache_misses, 1)

    @property
    def avg_cost_per_call(self) -> float:
        """Calculate average cost in USD per non-cached call."""
        if self.cache_misses == 0:
            return 0.0
        return round(self.total_cost_usd / self.cache_misses, 6)

    def to_dict(self) -> dict[str, Any]:
        """Convert feature metrics to dictionary representation."""
        return {
            "feature": self.feature,
            "total_calls": self.total_calls,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_ratio": self.cache_hit_ratio,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "avg_tokens_per_call": self.avg_tokens_per_call,
            "avg_cost_per_call": self.avg_cost_per_call,
        }


@dataclass
class UserCostSummary:
    """Privacy-preserving cost aggregation for an anonymized user identifier."""

    user_hash: str
    call_count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    daily_cost_usd: float = 0.0
    last_active_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert user cost summary to dictionary representation."""
        return {
            "user_hash": self.user_hash,
            "call_count": self.call_count,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "daily_cost_usd": round(self.daily_cost_usd, 4),
            "last_active_at": self.last_active_at.isoformat(),
        }


def hash_user_identifier(user_id: str | None) -> str | None:
    """Hash user identifier with SHA-256 for zero-PII privacy compliance."""
    if not user_id:
        return None
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


class CostTracker:
    """Thread-safe cost management, token telemetry, cache ratio, and budget monitor."""

    def __init__(
        self,
        budget_config: BudgetConfig | None = None,
        model_pricing: dict[str, ModelPricing] | None = None,
        max_record_history: int = 1000,
        max_alert_history: int = 200,
    ) -> None:
        self._lock = threading.RLock()
        self.budget_config = budget_config or BudgetConfig()
        self.model_pricing = dict(DEFAULT_MODEL_PRICING)
        if model_pricing:
            self.model_pricing.update(model_pricing)

        self._max_record_history = max_record_history
        self._max_alert_history = max_alert_history

        # Aggregated telemetry
        self._features: dict[str, FeatureMetrics] = {}
        self._users: dict[str, UserCostSummary] = {}
        self._records: collections.deque[CostRecord] = collections.deque(maxlen=max_record_history)
        self._alerts: collections.deque[BudgetAlert] = collections.deque(maxlen=max_alert_history)

        # Global aggregations
        self._daily_spent_usd: float = 0.0
        self._monthly_spent_usd: float = 0.0
        self._current_date: datetime.date = datetime.datetime.now(datetime.UTC).date()
        self._current_month: int = self._current_date.month

        # Triggered alert level caches to prevent duplicate spam
        self._active_global_daily_level: BudgetAlertLevel = BudgetAlertLevel.NORMAL
        self._user_alert_levels: dict[str, BudgetAlertLevel] = {}

        # Alert callback handlers
        self._alert_handlers: list[Callable[[BudgetAlert], None]] = []

    def register_model_pricing(self, model: str, pricing: ModelPricing) -> None:
        """Register or update model pricing in USD per 1M tokens."""
        with self._lock:
            self.model_pricing[model] = pricing

    def add_alert_handler(self, handler: Callable[[BudgetAlert], None]) -> None:
        """Register an alert dispatch callback handler."""
        with self._lock:
            self._alert_handlers.append(handler)

    def _get_pricing_for_model(self, model: str) -> ModelPricing:
        """Retrieve model pricing or fallback default."""
        return self.model_pricing.get(model, FALLBACK_MODEL_PRICING)

    def _check_day_rollover(self, now: datetime.datetime) -> None:
        """Reset daily counters if date has advanced."""
        today = now.date()
        if today != self._current_date:
            self._current_date = today
            self._daily_spent_usd = 0.0
            self._active_global_daily_level = BudgetAlertLevel.NORMAL
            # Reset user daily counters
            for user in self._users.values():
                user.daily_cost_usd = 0.0
            self._user_alert_levels.clear()

        if today.month != self._current_month:
            self._current_month = today.month
            self._monthly_spent_usd = 0.0

    def record_call(
        self,
        feature: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached: bool = False,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime.datetime | None = None,
    ) -> CostRecord:
        """Record an LLM call or analytical execution with token counts and cache state."""
        now = timestamp or datetime.datetime.now(datetime.UTC)
        user_hash = hash_user_identifier(user_id)

        with self._lock:
            self._check_day_rollover(now)

            pricing = self._get_pricing_for_model(model)
            if cached:
                # Cache hits do not consume new API tokens or dollar expenditure
                call_cost = 0.0
                effective_prompt = 0
                effective_completion = 0
                effective_total = 0
            else:
                call_cost = pricing.calculate_cost(prompt_tokens, completion_tokens)
                effective_prompt = prompt_tokens
                effective_completion = completion_tokens
                effective_total = prompt_tokens + completion_tokens

            record_id = f"cr_{int(now.timestamp() * 1000)}_{len(self._records) + 1}"
            record = CostRecord(
                record_id=record_id,
                timestamp=now,
                feature=feature,
                model=model,
                prompt_tokens=effective_prompt,
                completion_tokens=effective_completion,
                total_tokens=effective_total,
                cost_usd=call_cost,
                cached=cached,
                user_hash=user_hash,
                metadata=metadata or {},
            )
            self._records.append(record)

            # Update feature metrics
            if feature not in self._features:
                self._features[feature] = FeatureMetrics(feature=feature)
            feat = self._features[feature]
            feat.total_calls += 1
            if cached:
                feat.cache_hits += 1
            else:
                feat.cache_misses += 1
                feat.prompt_tokens += effective_prompt
                feat.completion_tokens += effective_completion
                feat.total_tokens += effective_total
                feat.total_cost_usd += call_cost

            # Update user metrics
            if user_hash:
                if user_hash not in self._users:
                    self._users[user_hash] = UserCostSummary(user_hash=user_hash)
                u = self._users[user_hash]
                u.call_count += 1
                u.total_tokens += effective_total
                u.total_cost_usd += call_cost
                u.daily_cost_usd += call_cost
                u.last_active_at = now

            # Update global financials
            self._daily_spent_usd += call_cost
            self._monthly_spent_usd += call_cost

            # Update Prometheus metrics
            self._publish_prometheus_metrics(feature, model, cached, record)

            # Evaluate budget alarms
            self._evaluate_budget_alarms(user_hash=user_hash, now=now)

            return record

    def _publish_prometheus_metrics(
        self,
        feature: str,
        model: str,
        cached: bool,
        record: CostRecord,
    ) -> None:
        """Update Prometheus telemetry counters and gauges."""
        cache_label = "hit" if cached else "miss"
        COST_LLM_CALLS_TOTAL.inc(1.0, {"feature": feature, "model": model, "cached": cache_label})

        if not cached:
            if record.prompt_tokens > 0:
                COST_LLM_TOKENS_TOTAL.inc(
                    float(record.prompt_tokens),
                    {"feature": feature, "model": model, "type": "prompt"},
                )
            if record.completion_tokens > 0:
                COST_LLM_TOKENS_TOTAL.inc(
                    float(record.completion_tokens),
                    {"feature": feature, "model": model, "type": "completion"},
                )
            if record.cost_usd > 0.0:
                COST_TOTAL_DOLLARS_COUNTER.inc(
                    record.cost_usd,
                    {"feature": feature, "model": model},
                )

        # Update cache hit ratio gauge
        feat_ratio = self.get_cache_hit_ratio(feature)
        COST_CACHE_HIT_RATIO_GAUGE.set(feat_ratio, {"feature": feature})

        overall_ratio = self.get_cache_hit_ratio(None)
        COST_CACHE_HIT_RATIO_GAUGE.set(overall_ratio, {"feature": "aggregate"})

        # Update budget usage ratio gauges
        daily_ratio = (
            self._daily_spent_usd / self.budget_config.daily_global_budget_usd
            if self.budget_config.daily_global_budget_usd > 0
            else 0.0
        )
        monthly_ratio = (
            self._monthly_spent_usd / self.budget_config.monthly_global_budget_usd
            if self.budget_config.monthly_global_budget_usd > 0
            else 0.0
        )
        COST_BUDGET_USAGE_RATIO_GAUGE.set(round(daily_ratio, 4), {"scope": "daily_global"})
        COST_BUDGET_USAGE_RATIO_GAUGE.set(round(monthly_ratio, 4), {"scope": "monthly_global"})

    def _evaluate_budget_alarms(self, user_hash: str | None, now: datetime.datetime) -> None:
        """Evaluate global daily and user budget thresholds and emit alarms."""
        # Global daily evaluation
        daily_limit = self.budget_config.daily_global_budget_usd
        if daily_limit > 0:
            usage_ratio = self._daily_spent_usd / daily_limit
            new_level = BudgetAlertLevel.NORMAL
            if usage_ratio >= self.budget_config.hard_limit_threshold_ratio:
                new_level = BudgetAlertLevel.EXHAUSTED
            elif usage_ratio >= self.budget_config.warning_threshold_ratio:
                new_level = BudgetAlertLevel.WARNING
            elif usage_ratio >= 0.50:
                new_level = BudgetAlertLevel.INFO

            if new_level not in (
                self._active_global_daily_level,
                BudgetAlertLevel.NORMAL,
            ):
                self._active_global_daily_level = new_level
                alert = BudgetAlert(
                    alert_id=f"alt_g_{int(now.timestamp())}_{new_level.value}",
                    timestamp=now,
                    level=new_level,
                    scope=BudgetScope.GLOBAL_DAILY,
                    target="global_daily",
                    current_spent_usd=self._daily_spent_usd,
                    budget_limit_usd=daily_limit,
                    usage_ratio=usage_ratio,
                    message=(
                        f"Global daily budget {new_level.value.upper()}: "
                        f"${self._daily_spent_usd:.2f} spent of ${daily_limit:.2f} "
                        f"({usage_ratio * 100:.1f}%)"
                    ),
                )
                self._emit_alert(alert)

        # User daily evaluation
        if user_hash and user_hash in self._users:
            u = self._users[user_hash]
            user_limit = self.budget_config.daily_user_budget_usd
            if user_limit > 0:
                u_ratio = u.daily_cost_usd / user_limit
                u_level = BudgetAlertLevel.NORMAL
                if u_ratio >= self.budget_config.hard_limit_threshold_ratio:
                    u_level = BudgetAlertLevel.EXHAUSTED
                elif u_ratio >= self.budget_config.warning_threshold_ratio:
                    u_level = BudgetAlertLevel.WARNING

                prev_u_level = self._user_alert_levels.get(user_hash, BudgetAlertLevel.NORMAL)
                if u_level != prev_u_level and u_level != BudgetAlertLevel.NORMAL:
                    self._user_alert_levels[user_hash] = u_level
                    alert = BudgetAlert(
                        alert_id=f"alt_u_{user_hash}_{int(now.timestamp())}_{u_level.value}",
                        timestamp=now,
                        level=u_level,
                        scope=BudgetScope.USER_DAILY,
                        target=user_hash,
                        current_spent_usd=u.daily_cost_usd,
                        budget_limit_usd=user_limit,
                        usage_ratio=u_ratio,
                        message=(
                            f"User daily budget {u_level.value.upper()} for {user_hash}: "
                            f"${u.daily_cost_usd:.3f} spent of ${user_limit:.2f} "
                            f"({u_ratio * 100:.1f}%)"
                        ),
                    )
                    self._emit_alert(alert)

    def _emit_alert(self, alert: BudgetAlert) -> None:
        """Record budget alert in deque, publish metric, and dispatch callbacks."""
        self._alerts.append(alert)
        COST_BUDGET_ALERTS_TOTAL.inc(1.0, {"level": alert.level.value, "scope": alert.scope.value})
        for handler in self._alert_handlers:
            try:
                handler(alert)
            except Exception:
                pass

    def should_allow_call(
        self,
        feature: str,
        user_id: str | None = None,
        estimated_cost_usd: float = 0.0,
    ) -> tuple[bool, str]:
        """Check whether an LLM call is permissible under current budget limits.

        Enforces fallback rule from .agent/rules/token-ve-llm.md:
        When budget is exhausted, falls back to deterministic rule-based summary without LLM.
        """
        now = datetime.datetime.now(datetime.UTC)
        user_hash = hash_user_identifier(user_id)

        with self._lock:
            self._check_day_rollover(now)

            # Check Global Daily Budget
            daily_limit = self.budget_config.daily_global_budget_usd
            if daily_limit > 0:
                if (self._daily_spent_usd + estimated_cost_usd) >= daily_limit:
                    return (
                        False,
                        f"Global daily budget exhausted (${self._daily_spent_usd:.2f} >= "
                        f"${daily_limit:.2f}). Falling back to rule-based summary without "
                        f"LLM for feature '{feature}'.",
                    )

            # Check Monthly Global Budget
            monthly_limit = self.budget_config.monthly_global_budget_usd
            if monthly_limit > 0:
                if (self._monthly_spent_usd + estimated_cost_usd) >= monthly_limit:
                    return (
                        False,
                        f"Global monthly budget exhausted (${self._monthly_spent_usd:.2f} >= "
                        f"${monthly_limit:.2f}). Falling back to rule-based summary without "
                        f"LLM for feature '{feature}'.",
                    )

            # Check User Daily Budget
            if user_hash and user_hash in self._users:
                u = self._users[user_hash]
                user_limit = self.budget_config.daily_user_budget_usd
                if user_limit > 0:
                    if (u.daily_cost_usd + estimated_cost_usd) >= user_limit:
                        return (
                            False,
                            f"User daily budget exhausted (${u.daily_cost_usd:.3f} >= "
                            f"${user_limit:.2f}) for user {user_hash}. "
                            f"Falling back to rule-based summary.",
                        )

            return (True, "Budget permitted")

    def get_feature_metrics(self, feature: str) -> FeatureMetrics | None:
        """Return metrics for specific feature."""
        with self._lock:
            return self._features.get(feature)

    def get_all_feature_metrics(self) -> dict[str, FeatureMetrics]:
        """Return copy of all feature metrics."""
        with self._lock:
            return dict(self._features)

    def get_cache_hit_ratio(self, feature: str | None = None) -> float:
        """Calculate cache hit ratio for a feature or whole platform."""
        with self._lock:
            if feature is not None:
                feat = self._features.get(feature)
                return feat.cache_hit_ratio if feat else 0.0

            total_calls = sum(f.total_calls for f in self._features.values())
            total_hits = sum(f.cache_hits for f in self._features.values())
            if total_calls == 0:
                return 0.0
            return round(total_hits / total_calls, 4)

    def is_cache_target_met(self, feature: str | None = None) -> bool:
        """Check whether cache hit ratio satisfies target (default >= 80%)."""
        ratio = self.get_cache_hit_ratio(feature)
        return ratio >= self.budget_config.cache_hit_target_ratio

    def get_user_cost(self, user_id: str) -> UserCostSummary | None:
        """Retrieve cost summary for user (by raw ID, internally hashed)."""
        user_hash = hash_user_identifier(user_id)
        if not user_hash:
            return None
        with self._lock:
            return self._users.get(user_hash)

    def get_top_users_by_cost(self, limit: int = 10) -> list[UserCostSummary]:
        """Return top users ordered by total cost (anonymized hashes)."""
        with self._lock:
            sorted_users = sorted(
                self._users.values(), key=lambda u: u.total_cost_usd, reverse=True
            )
            return sorted_users[:limit]

    def get_recent_alerts(self, limit: int = 50) -> list[BudgetAlert]:
        """Return recent budget alerts in reverse chronological order."""
        with self._lock:
            alerts = list(self._alerts)
            alerts.reverse()
            return alerts[:limit]

    def get_cost_summary(self) -> dict[str, Any]:
        """Generate comprehensive cost, token, and cache telemetry report."""
        now = datetime.datetime.now(datetime.UTC)
        with self._lock:
            self._check_day_rollover(now)

            total_calls = sum(f.total_calls for f in self._features.values())
            cache_hits = sum(f.cache_hits for f in self._features.values())
            cache_misses = sum(f.cache_misses for f in self._features.values())
            overall_cache_ratio = self.get_cache_hit_ratio(None)
            total_prompt_tokens = sum(f.prompt_tokens for f in self._features.values())
            total_completion_tokens = sum(f.completion_tokens for f in self._features.values())
            total_tokens = sum(f.total_tokens for f in self._features.values())
            total_cost_usd = sum(f.total_cost_usd for f in self._features.values())

            daily_ratio = (
                self._daily_spent_usd / self.budget_config.daily_global_budget_usd
                if self.budget_config.daily_global_budget_usd > 0
                else 0.0
            )
            monthly_ratio = (
                self._monthly_spent_usd / self.budget_config.monthly_global_budget_usd
                if self.budget_config.monthly_global_budget_usd > 0
                else 0.0
            )

            target = self.budget_config.cache_hit_target_ratio
            return {
                "generated_at": now.isoformat(),
                "summary": {
                    "total_calls": total_calls,
                    "cache_hits": cache_hits,
                    "cache_misses": cache_misses,
                    "cache_hit_ratio": overall_cache_ratio,
                    "cache_target_ratio": target,
                    "cache_target_met": overall_cache_ratio >= target,
                    "total_prompt_tokens": total_prompt_tokens,
                    "total_completion_tokens": total_completion_tokens,
                    "total_tokens": total_tokens,
                    "total_cost_usd": round(total_cost_usd, 4),
                    "daily_spent_usd": round(self._daily_spent_usd, 4),
                    "monthly_spent_usd": round(self._monthly_spent_usd, 4),
                },
                "budget": {
                    "daily_global_limit_usd": self.budget_config.daily_global_budget_usd,
                    "daily_global_spent_usd": round(self._daily_spent_usd, 4),
                    "daily_usage_ratio": round(daily_ratio, 4),
                    "monthly_global_limit_usd": self.budget_config.monthly_global_budget_usd,
                    "monthly_global_spent_usd": round(self._monthly_spent_usd, 4),
                    "monthly_usage_ratio": round(monthly_ratio, 4),
                    "daily_user_limit_usd": self.budget_config.daily_user_budget_usd,
                    "status": self._active_global_daily_level.value,
                    "warning_threshold_ratio": self.budget_config.warning_threshold_ratio,
                    "hard_limit_threshold_ratio": self.budget_config.hard_limit_threshold_ratio,
                },
                "features": {k: v.to_dict() for k, v in self._features.items()},
                "top_users": [u.to_dict() for u in self.get_top_users_by_cost(limit=10)],
                "recent_alerts": [a.to_dict() for a in self.get_recent_alerts(limit=10)],
            }

    def reset_daily_counters(self) -> None:
        """Force manual reset of daily expenditure counters."""
        with self._lock:
            self._daily_spent_usd = 0.0
            self._active_global_daily_level = BudgetAlertLevel.NORMAL
            for u in self._users.values():
                u.daily_cost_usd = 0.0
            self._user_alert_levels.clear()

    def generate_html_dashboard(self) -> str:
        """Render standalone, responsive HTML cost and token dashboard."""
        summary = self.get_cost_summary()
        sum_data = summary["summary"]
        bgt_data = summary["budget"]
        features = summary["features"]
        top_users = summary["top_users"]
        alerts = summary["recent_alerts"]

        cache_target_badge = (
            '<span class="badge badge-success">✓ Hedef Karşılandı</span>'
            if sum_data["cache_target_met"]
            else '<span class="badge badge-warning">⚠ Hedef Altında</span>'
        )

        bgt_status_badge = (
            '<span class="badge badge-danger">Aşım / Limit Dolu</span>'
            if bgt_data["status"] == "exhausted"
            else (
                '<span class="badge badge-warning">Uyarı Seviyesi</span>'
                if bgt_data["status"] == "warning"
                else '<span class="badge badge-success">Normal</span>'
            )
        )

        # Feature table rows
        feature_rows = []
        for feat_name, feat_info in features.items():
            f_ratio = feat_info["cache_hit_ratio"] * 100
            f_badge = (
                f'<span class="badge badge-success">%{f_ratio:.1f}</span>'
                if feat_info["cache_hit_ratio"] >= 0.80
                else f'<span class="badge badge-warning">%{f_ratio:.1f}</span>'
            )
            feature_rows.append(
                f"""<tr>
                    <td><strong>{html.escape(feat_name)}</strong></td>
                    <td>{feat_info["total_calls"]:,}</td>
                    <td>{feat_info["cache_hits"]:,}</td>
                    <td>{feat_info["cache_misses"]:,}</td>
                    <td>{f_badge}</td>
                    <td>{feat_info["total_tokens"]:,}</td>
                    <td>${feat_info["total_cost_usd"]:.4f}</td>
                    <td>${feat_info["avg_cost_per_call"]:.6f}</td>
                </tr>"""
            )
        if not feature_rows:
            feature_rows.append(
                "<tr><td colspan='8' class='text-muted'>Henüz kayıtlı özellik verisi yok.</td></tr>"
            )

        # User table rows
        user_rows = []
        for u in top_users:
            user_rows.append(
                f"""<tr>
                    <td><code>{html.escape(u["user_hash"])}</code></td>
                    <td>{u["call_count"]:,}</td>
                    <td>{u["total_tokens"]:,}</td>
                    <td>${u["daily_cost_usd"]:.4f}</td>
                    <td><strong>${u["total_cost_usd"]:.4f}</strong></td>
                </tr>"""
            )
        if not user_rows:
            user_rows.append(
                "<tr><td colspan='5' class='text-muted'>"
                "Henüz kayıtlı kullanıcı verisi yok.</td></tr>"
            )

        # Alert items
        alert_items = []
        for a in alerts:
            lvl_class = "alert-danger" if a["level"] == "exhausted" else "alert-warning"
            badge_text = html.escape(a["level"].upper())
            alert_items.append(
                f"""<div class="alert-item {lvl_class}">
                    <div class="alert-meta">
                        <span class="badge badge-{lvl_class}">{badge_text}</span>
                        <span>{html.escape(a["timestamp"])}</span>
                        <span>Scope: {html.escape(a["scope"])}</span>
                    </div>
                    <div class="alert-msg">{html.escape(a["message"])}</div>
                </div>"""
            )
        if not alert_items:
            alert_items.append(
                "<div class='text-muted'>Aktif bütçe alarmı veya aşım bulunmuyor.</div>"
            )

        daily_spent = bgt_data["daily_global_spent_usd"]
        daily_limit = bgt_data["daily_global_limit_usd"]
        daily_ratio = bgt_data["daily_usage_ratio"]
        bar_class = "danger" if daily_ratio >= 1.0 else ("warning" if daily_ratio >= 0.8 else "")
        bar_width = min(daily_ratio * 100, 100.0)

        cache_ratio_pct = sum_data["cache_hit_ratio"] * 100
        cache_bar_width = min(cache_ratio_pct, 100.0)

        monthly_spent = bgt_data["monthly_global_spent_usd"]
        monthly_limit = bgt_data["monthly_global_limit_usd"]

        return f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BoraI — LLM Maliyet & Token Panosu</title>
    <style>
        :root {{
            --bg-color: #0d1117;
            --surface-color: #161b22;
            --surface-border: #30363d;
            --text-primary: #f0f6fc;
            --text-secondary: #8b949e;
            --accent-color: #58a6ff;
            --success-color: #238636;
            --warning-color: #d29922;
            --danger-color: #da3633;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 24px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--surface-border);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .header h1 {{ font-size: 24px; font-weight: 600; }}
        .header .meta {{ color: var(--text-secondary); font-size: 14px; }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: 8px;
            padding: 16px;
        }}
        .card-title {{
            color: var(--text-secondary);
            font-size: 13px;
            text-transform: uppercase;
            margin-bottom: 8px;
        }}
        .card-value {{ font-size: 26px; font-weight: 700; margin-bottom: 4px; }}
        .card-sub {{ font-size: 13px; color: var(--text-secondary); }}
        .progress-bar {{
            background: #21262d;
            border-radius: 4px;
            height: 8px;
            overflow: hidden;
            margin: 8px 0;
        }}
        .progress-fill {{
            height: 100%;
            background: var(--accent-color);
            border-radius: 4px;
            transition: width 0.3s ease;
        }}
        .progress-fill.warning {{ background: var(--warning-color); }}
        .progress-fill.danger {{ background: var(--danger-color); }}
        .section {{
            background: var(--surface-color);
            border: 1px solid var(--surface-border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 24px;
        }}
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
        }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid var(--surface-border);
        }}
        th {{ color: var(--text-secondary); font-weight: 600; background: #0d1117; }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-success {{
            background: rgba(35, 134, 54, 0.2);
            color: #3fb950;
            border: 1px solid #238636;
        }}
        .badge-warning {{
            background: rgba(210, 153, 34, 0.2);
            color: #d29922;
            border: 1px solid #9e6a03;
        }}
        .badge-danger, .badge-alert-danger {{
            background: rgba(218, 54, 51, 0.2);
            color: #f85149;
            border: 1px solid #da3633;
        }}
        .alert-item {{
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 8px;
            border-left: 4px solid var(--accent-color);
            background: #0d1117;
        }}
        .alert-item.alert-warning {{ border-left-color: var(--warning-color); }}
        .alert-item.alert-danger {{ border-left-color: var(--danger-color); }}
        .alert-meta {{
            font-size: 12px;
            color: var(--text-secondary);
            display: flex;
            gap: 12px;
            margin-bottom: 4px;
        }}
        .alert-msg {{ font-size: 14px; }}
        .text-muted {{ color: var(--text-secondary); }}
        code {{
            background: #21262d;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <div>
                <h1>BoraI — LLM Maliyet & Token Panosu</h1>
                <div class="meta">Özellik Bazlı Token · Önbellek Verimliliği · Bütçe Alarmları</div>
            </div>
            <div class="meta">Son Güncelleme: {html.escape(summary["generated_at"])}</div>
        </header>

        <div class="grid">
            <div class="card">
                <div class="card-title">Günlük Bütçe Tüketimi</div>
                <div class="card-value">${daily_spent:.2f} / ${daily_limit:.2f}</div>
                <div class="progress-bar">
                    <div class="progress-fill {bar_class}"
                         style="width: {bar_width:.1f}%"></div>
                </div>
                <div class="card-sub">Kullanım: %{daily_ratio * 100:.1f} · {bgt_status_badge}</div>
            </div>

            <div class="card">
                <div class="card-title">Önbellek İsabet Oranı</div>
                <div class="card-value">%{cache_ratio_pct:.1f}</div>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: {cache_bar_width:.1f}%"></div>
                </div>
                <div class="card-sub">
                    {sum_data["cache_hits"]:,} isabet / {sum_data["total_calls"]:,} çağrı ·
                    {cache_target_badge}
                </div>
            </div>

            <div class="card">
                <div class="card-title">Toplam Tüketilen Token</div>
                <div class="card-value">{sum_data["total_tokens"]:,}</div>
                <div class="card-sub">
                    Girdi: {sum_data["total_prompt_tokens"]:,} ·
                    Çıktı: {sum_data["total_completion_tokens"]:,}
                </div>
            </div>

            <div class="card">
                <div class="card-title">Toplam Maliyet (USD)</div>
                <div class="card-value">${sum_data["total_cost_usd"]:.4f}</div>
                <div class="card-sub">Aylık: ${monthly_spent:.2f} / ${monthly_limit:.2f}</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">
                <span>Özellik (Feature) Bazlı Token ve Maliyet Dağılımı</span>
                <span style="font-size: 13px; color: var(--text-secondary);">
                    Hedef: ≥ %80 Önbellek İsabeti
                </span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Özellik Adı</th>
                        <th>Toplam Çağrı</th>
                        <th>Önbellek İsabeti</th>
                        <th>Doğrudan Çağrı</th>
                        <th>İsabet Oranı</th>
                        <th>Toplam Token</th>
                        <th>Toplam Maliyet</th>
                        <th>Ort. Çağrı Başına Maliyet</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(feature_rows)}
                </tbody>
            </table>
        </div>

        <div class="section">
            <div class="section-title">
                <span>En Çok Tüketen Kullanıcılar (Gizlilik Korumalı / Anonim Hash)</span>
                <span style="font-size: 13px; color: var(--text-secondary);">
                    Günlük Limit: ${bgt_data["daily_user_limit_usd"]:.2f} / kullanıcı
                </span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Anonim Kullanıcı Hash</th>
                        <th>Çağrı Sayısı</th>
                        <th>Toplam Token</th>
                        <th>Günlük Harcama</th>
                        <th>Toplam Harcama</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(user_rows)}
                </tbody>
            </table>
        </div>

        <div class="section">
            <div class="section-title">
                <span>Son Bütçe Alarmları ve Olay Kayıtları</span>
            </div>
            <div>
                {"".join(alert_items)}
            </div>
        </div>
    </div>
</body>
</html>"""


# Global singleton instance
COST_TRACKER = CostTracker()
