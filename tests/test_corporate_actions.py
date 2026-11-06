"""Tests for corporate actions modeling and historical price adjustment engine."""

from datetime import date
from decimal import Decimal

import pytest

from stock_analysis.pipeline import (
    CorporateAction,
    CorporateActionType,
    apply_corporate_actions,
)
from stock_analysis.providers import MarketBarRecord

pytestmark = pytest.mark.unit


def _make_bar(
    symbol: str = "THYAO",
    trade_date: date = date(2026, 1, 5),
    raw_open: str = "100.0",
    raw_high: str = "105.0",
    raw_low: str = "98.0",
    raw_close: str = "100.0",
) -> MarketBarRecord:
    """Helper to construct a MarketBarRecord for corporate actions testing."""
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        currency="TRY",
        raw_open=Decimal(raw_open),
        raw_high=Decimal(raw_high),
        raw_low=Decimal(raw_low),
        raw_close=Decimal(raw_close),
        adj_open=Decimal(raw_open),
        adj_high=Decimal(raw_high),
        adj_low=Decimal(raw_low),
        adj_close=Decimal(raw_close),
        volume=100_000,
        turnover_try=Decimal("10000000.00"),
        source_name="test",
        is_verified=True,
    )


def test_corporate_action_validation() -> None:
    """Validation checks on CorporateAction model."""
    # Cash dividend with 0 or negative amount must fail
    with pytest.raises(ValueError, match="Cash dividend action must specify amount_try > 0"):
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 5, 10),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("0.0"),
        )

    # Rights issue without subscription price must fail
    with pytest.raises(ValueError, match="Rights issue requires a positive subscription_price_try"):
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 5, 10),
            action_type=CorporateActionType.RIGHTS_ISSUE,
            ratio=Decimal("0.5"),
        )


def test_split_backward_adjustment() -> None:
    """2:1 forward split halves historical prices prior to ex-date."""
    bars = [
        _make_bar(
            trade_date=date(2026, 1, 5),
            raw_open="200.0",
            raw_high="210.0",
            raw_low="195.0",
            raw_close="200.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 6),
            raw_open="100.0",
            raw_high="105.0",
            raw_low="95.0",
            raw_close="100.0",
        ),
    ]
    actions = [
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
            description="2:1 Stock Split",
        )
    ]

    adjusted = apply_corporate_actions(bars, actions)

    # Bar 0 (prior to split): raw prices halved in adj fields
    assert adjusted[0].raw_close == Decimal("200.0")
    assert adjusted[0].adj_close == Decimal("100.0000")
    assert adjusted[0].adj_open == Decimal("100.0000")
    assert adjusted[0].adj_high == Decimal("105.0000")
    assert adjusted[0].adj_low == Decimal("97.5000")

    # Bar 1 (on/after ex-date): raw matches adj
    assert adjusted[1].raw_close == Decimal("100.0")
    assert adjusted[1].adj_close == Decimal("100.0000")
    assert adjusted[1].split_factor == Decimal("2.0")


def test_reverse_split_adjustment() -> None:
    """1:10 reverse split (ratio=0.1) multiplies historical prices by 10."""
    bars = [
        _make_bar(
            trade_date=date(2026, 1, 5),
            raw_open="2.0",
            raw_high="2.5",
            raw_low="1.8",
            raw_close="2.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 6),
            raw_open="20.0",
            raw_high="22.0",
            raw_low="19.0",
            raw_close="20.0",
        ),
    ]
    actions = [
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.REVERSE_SPLIT,
            ratio=Decimal("0.1"),
            description="1:10 Reverse Split",
        )
    ]

    adjusted = apply_corporate_actions(bars, actions)

    # Historical bar should be adjusted x 10
    assert adjusted[0].adj_close == Decimal("20.0000")
    assert adjusted[0].adj_open == Decimal("20.0000")
    assert adjusted[1].adj_close == Decimal("20.0000")


def test_bonus_issue_adjustment() -> None:
    """100% bonus issue (bedelsiz ratio=1.0) multiplies historical prices by 1 / (1 + 1.0) = 0.5."""
    bars = [
        _make_bar(
            trade_date=date(2026, 1, 5),
            raw_open="100.0",
            raw_high="105.0",
            raw_low="95.0",
            raw_close="100.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 6),
            raw_open="50.0",
            raw_high="52.0",
            raw_low="48.0",
            raw_close="50.0",
        ),
    ]
    actions = [
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.BONUS_ISSUE,
            ratio=Decimal("1.0"),
            description="100% Bedelsiz Sermaye Artırımı",
        )
    ]

    adjusted = apply_corporate_actions(bars, actions)

    assert adjusted[0].adj_close == Decimal("50.0000")
    assert adjusted[0].adj_open == Decimal("50.0000")
    assert adjusted[1].adj_close == Decimal("50.0000")


def test_cash_dividend_adjustment() -> None:
    """Cash dividend reduces prior prices proportionally: (P_cum - div) / P_cum."""
    bars = [
        _make_bar(
            trade_date=date(2026, 1, 5),
            raw_open="100.0",
            raw_high="105.0",
            raw_low="95.0",
            raw_close="100.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 6),
            raw_open="90.0",
            raw_high="92.0",
            raw_low="88.0",
            raw_close="90.0",
        ),
    ]
    actions = [
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("10.0"),
            description="10 TRY Nakit Temettü",
        )
    ]

    adjusted = apply_corporate_actions(bars, actions)

    # Factor = (100 - 10) / 100 = 0.90
    assert adjusted[0].adj_close == Decimal("90.0000")
    assert adjusted[0].adj_open == Decimal("90.0000")
    assert adjusted[1].adj_close == Decimal("90.0000")
    assert adjusted[1].dividend_try == Decimal("10.0")


def test_rights_issue_adjustment() -> None:
    """Rights issue (bedelli) adjustment calculates BIST theoretical ex-rights price."""
    # Cum close = 100 TRY, Rights ratio = 100% (1.0), Subscription price = 20 TRY
    # P_ex = (100 + 1.0 * 20) / (1 + 1.0) = 120 / 2 = 60 TRY
    # Factor = 60 / 100 = 0.60
    bars = [
        _make_bar(
            trade_date=date(2026, 1, 5),
            raw_open="100.0",
            raw_high="105.0",
            raw_low="95.0",
            raw_close="100.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 6),
            raw_open="60.0",
            raw_high="65.0",
            raw_low="58.0",
            raw_close="60.0",
        ),
    ]
    actions = [
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.RIGHTS_ISSUE,
            ratio=Decimal("1.0"),
            subscription_price_try=Decimal("20.0"),
            description="100% Bedelli Sermaye Artırımı",
        )
    ]

    adjusted = apply_corporate_actions(bars, actions)

    assert adjusted[0].adj_close == Decimal("60.0000")
    assert adjusted[0].adj_open == Decimal("60.0000")
    assert adjusted[1].adj_close == Decimal("60.0000")


def test_sequential_corporate_actions() -> None:
    """Multiple sequential actions accumulate correctly across historical dates."""
    bars = [
        _make_bar(
            trade_date=date(2026, 1, 2),
            raw_open="200.0",
            raw_high="205.0",
            raw_low="195.0",
            raw_close="200.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 5),
            raw_open="190.0",
            raw_high="195.0",
            raw_low="185.0",
            raw_close="190.0",
        ),
        _make_bar(
            trade_date=date(2026, 1, 6),
            raw_open="95.0",
            raw_high="98.0",
            raw_low="92.0",
            raw_close="95.0",
        ),
    ]
    actions = [
        # Action 1: Cash div 10 TRY on Jan 5 (cum close on Jan 2 is 200, factor = 190/200 = 0.95)
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 5),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("10.0"),
        ),
        # Action 2: 2:1 Split on Jan 6 (cum close on Jan 5 is 190, factor = 0.5)
        CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
        ),
    ]

    adjusted = apply_corporate_actions(bars, actions)

    # Bar 0 (Jan 2): subjected to dividend (0.95) AND split (0.5) -> cum factor = 0.475
    # adj_close = 200 * 0.475 = 95.0000
    assert adjusted[0].adj_close == Decimal("95.0000")

    # Bar 1 (Jan 5): subjected ONLY to split (0.5)
    # adj_close = 190 * 0.5 = 95.0000
    assert adjusted[1].adj_close == Decimal("95.0000")
    assert adjusted[1].dividend_try == Decimal("10.0")

    # Bar 2 (Jan 6): after all actions -> adj matches raw
    assert adjusted[2].adj_close == Decimal("95.0000")
    assert adjusted[2].split_factor == Decimal("2.0")


def test_edge_cases() -> None:
    """Edge cases: empty bars, symbol mismatch, action outside bar date range."""
    # Empty bars
    assert apply_corporate_actions([], []) == []

    # Symbol mismatch
    bars = [
        _make_bar(symbol="THYAO", trade_date=date(2026, 1, 5)),
        _make_bar(symbol="BIMAS", trade_date=date(2026, 1, 6)),
    ]
    with pytest.raises(ValueError, match="All bars in series must belong to the same symbol"):
        apply_corporate_actions(bars, [])

    # Action date before first bar
    single_bar = [_make_bar(trade_date=date(2026, 1, 10), raw_close="100.0")]
    action = CorporateAction(
        symbol="THYAO",
        ex_date=date(2026, 1, 5),
        action_type=CorporateActionType.SPLIT,
        ratio=Decimal("2.0"),
    )
    res = apply_corporate_actions(single_bar, [action])
    assert res[0].adj_close == Decimal("100.0")
