"""Corporate action modeling and historical price adjustment engine.

Handles stock splits, reverse splits, bonus issues (bedelsiz sermaye artırımı),
rights issues (bedelli sermaye artırımı), and cash dividends (nakit temettü)
according to BIST / standard quantitative finance adjustment methodologies.
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_analysis.providers.base import MarketBarRecord


class CorporateActionType(StrEnum):
    """Types of corporate actions affecting share count or historical prices."""

    SPLIT = "split"  # Forward stock split (e.g., 2:1 -> 2 shares for 1)
    REVERSE_SPLIT = "reverse_split"  # Consolidation (e.g., 1:10 -> 1 share for 10)
    BONUS_ISSUE = (
        "bonus_issue"  # Bedelsiz sermaye artırımı (e.g., 100% bonus -> 1 new share per share)
    )
    RIGHTS_ISSUE = (
        "rights_issue"  # Bedelli sermaye artırımı (capital increase with rights subscription)
    )
    CASH_DIVIDEND = "cash_dividend"  # Nakit temettü (cash payout per share)


class CorporateAction(BaseModel):
    """Specification of an individual corporate action event."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1, max_length=10, description="BIST ticker symbol (e.g. THYAO)")
    ex_date: date = Field(description="Ex-action date on which the price adjusts in the market")
    action_type: CorporateActionType = Field(description="Type of corporate action")
    ratio: Decimal = Field(
        default=Decimal("1.0"),
        gt=Decimal("0"),
        description="Ratio for split/bonus/rights (e.g. 2.0 for 2:1 split or 1.0 for 100% bonus)",
    )
    amount_try: Decimal = Field(
        default=Decimal("0.0"),
        ge=Decimal("0"),
        description="Cash dividend per share in TRY or nominal amount",
    )
    subscription_price_try: Decimal | None = Field(
        default=None,
        description="Subscription/exercise price per share in TRY for rights issues",
    )
    description: str = Field(default="", description="Human-readable event notes")

    @model_validator(mode="after")
    def validate_action_fields(self) -> "CorporateAction":
        """Validate corporate action parameters based on type."""
        if self.action_type == CorporateActionType.CASH_DIVIDEND and self.amount_try <= Decimal(
            "0"
        ):
            raise ValueError("Cash dividend action must specify amount_try > 0")

        if self.action_type == CorporateActionType.RIGHTS_ISSUE:
            if self.subscription_price_try is None or self.subscription_price_try <= Decimal("0"):
                raise ValueError("Rights issue requires a positive subscription_price_try")

        return self

    def calculate_adjustment_factor(self, cum_close_price: Decimal) -> Decimal:
        """Calculate the backward adjustment multiplier for bars prior to ex-date.

        Args:
            cum_close_price: The closing price of the session immediately preceding ex_date.

        Returns:
            Decimal adjustment multiplier (< 1.0 reduces historical prices).
        """
        if cum_close_price <= Decimal("0"):
            raise ValueError(f"cum_close_price must be positive, got {cum_close_price}")

        match self.action_type:
            case CorporateActionType.SPLIT | CorporateActionType.REVERSE_SPLIT:
                # 2:1 split (ratio=2) -> past prices are halved (factor = 0.5)
                # 1:10 reverse split (ratio=0.1) -> past prices multiplied by 10 (factor = 10.0)
                return Decimal("1.0") / self.ratio

            case CorporateActionType.BONUS_ISSUE:
                # e.g., 100% bedelsiz (ratio=1.0) -> past prices multiplied by 1 / (1 + 1.0) = 0.5
                return Decimal("1.0") / (Decimal("1.0") + self.ratio)

            case CorporateActionType.RIGHTS_ISSUE:
                # BIST theoretical ex-rights formula:
                # P_ex = (P_cum + ratio * subscription_price) / (1 + ratio)
                # factor = P_ex / P_cum
                sub_price = self.subscription_price_try or Decimal("1.0")
                theoretical_ex = (cum_close_price + (self.ratio * sub_price)) / (
                    Decimal("1.0") + self.ratio
                )
                return theoretical_ex / cum_close_price

            case CorporateActionType.CASH_DIVIDEND:
                # Proportional dividend adjustment:
                # factor = (P_cum - dividend) / P_cum
                if self.amount_try >= cum_close_price:
                    msg = (
                        f"Dividend {self.amount_try} TRY cannot be >= "
                        f"cum close price {cum_close_price} TRY"
                    )
                    raise ValueError(msg)
                return (cum_close_price - self.amount_try) / cum_close_price


def apply_corporate_actions(
    bars: Sequence[MarketBarRecord],
    actions: Sequence[CorporateAction],
    *,
    precision: int = 4,
) -> list[MarketBarRecord]:
    """Apply historical corporate actions to an EOD bar series using backward adjustment.

    Backward adjustment keeps current/recent prices unchanged (matching raw prices)
    and scales past OHLC prices downward/upward so historical returns and technical
    indicators remain continuous and free of artificial price cliffs.

    Args:
        bars: Daily market bars (will be sorted chronologically).
        actions: Corporate actions for the symbol (will be sorted chronologically).
        precision: Number of decimal places to round adjusted prices (default: 4).

    Returns:
        List of MarketBarRecord instances with updated adj_open, adj_high, adj_low, adj_close,
        split_factor, and dividend_try fields.
    """
    if not bars:
        return []

    # Verify all bars belong to a single symbol
    symbol = bars[0].symbol
    if any(b.symbol != symbol for b in bars):
        raise ValueError("All bars in series must belong to the same symbol")

    # Filter and verify actions for this symbol
    symbol_actions = [a for a in actions if a.symbol == symbol]
    if not symbol_actions:
        return list(bars)

    # Sort bars and actions chronologically
    sorted_bars = sorted(bars, key=lambda b: b.trade_date)
    sorted_actions = sorted(symbol_actions, key=lambda a: a.ex_date)

    # Map each trade_date to its cumulative adjustment factor (starts at 1.0)
    # A bar prior to an action will accumulate that action's factor.
    factors: dict[date, Decimal] = {b.trade_date: Decimal("1.0") for b in sorted_bars}
    bar_dates = [b.trade_date for b in sorted_bars]
    date_to_bar = {b.trade_date: b for b in sorted_bars}

    # Action event markers on ex_date bar if present
    action_events_by_date: dict[date, dict[str, Decimal]] = {}

    for action in sorted_actions:
        # Find the cum-bar (the last trading session prior to action.ex_date)
        cum_dates = [d for d in bar_dates if d < action.ex_date]
        if not cum_dates:
            # Action happens before the earliest bar in our history;
            # entire history is already post-action, no adjustment needed.
            continue

        cum_date = max(cum_dates)
        cum_bar = date_to_bar[cum_date]

        # Calculate factor using cum_bar's raw_close
        action_factor = action.calculate_adjustment_factor(cum_bar.raw_close)

        # Multiply cumulative factor for all bars strictly before ex_date
        for d in cum_dates:
            factors[d] *= action_factor

        # Record action metadata for the ex_date bar if it exists in data
        if action.ex_date in date_to_bar:
            if action.ex_date not in action_events_by_date:
                action_events_by_date[action.ex_date] = {
                    "split_factor": Decimal("1.0"),
                    "dividend_try": Decimal("0.0"),
                }
            if action.action_type in (
                CorporateActionType.SPLIT,
                CorporateActionType.REVERSE_SPLIT,
                CorporateActionType.BONUS_ISSUE,
            ):
                action_events_by_date[action.ex_date]["split_factor"] *= action.ratio
            elif action.action_type == CorporateActionType.CASH_DIVIDEND:
                action_events_by_date[action.ex_date]["dividend_try"] += action.amount_try

    # Build updated MarketBarRecord instances
    rounding_format = Decimal("10") ** -precision
    adjusted_bars: list[MarketBarRecord] = []

    for bar in sorted_bars:
        factor = factors[bar.trade_date]
        adj_open = (bar.raw_open * factor).quantize(rounding_format, rounding=ROUND_HALF_UP)
        adj_high = (bar.raw_high * factor).quantize(rounding_format, rounding=ROUND_HALF_UP)
        adj_low = (bar.raw_low * factor).quantize(rounding_format, rounding=ROUND_HALF_UP)
        adj_close = (bar.raw_close * factor).quantize(rounding_format, rounding=ROUND_HALF_UP)

        # Guard against precision edge cases where high < max(open, close) due to rounding
        adj_high = max(adj_high, adj_open, adj_close)
        adj_low = min(adj_low, adj_open, adj_close)

        events = action_events_by_date.get(
            bar.trade_date,
            {"split_factor": bar.split_factor, "dividend_try": bar.dividend_try},
        )

        updated_bar = bar.model_copy(
            update={
                "adj_open": adj_open,
                "adj_high": adj_high,
                "adj_low": adj_low,
                "adj_close": adj_close,
                "split_factor": events["split_factor"],
                "dividend_try": events["dividend_try"],
            }
        )
        adjusted_bars.append(updated_bar)

    return adjusted_bars


class CorporateActionAdjuster:
    """Historical corporate action adjustment engine supporting dual-stream data architectures."""

    DUAL_STREAM: str = "dual_stream"

    def __init__(self, precision: int = 4) -> None:
        """Initialize CorporateActionAdjuster."""
        self.precision = precision

    def adjust(
        self,
        bars: Sequence[MarketBarRecord],
        actions: Sequence[CorporateAction],
    ) -> list[MarketBarRecord]:
        """Apply corporate action adjustments to bar series."""
        return apply_corporate_actions(bars, actions, precision=self.precision)

