"""Tests for query pattern index coverage and schema index structure."""

from sqlalchemy import create_engine, inspect

# Import models to ensure registration in Base.metadata
import stock_analysis.db.models  # noqa: F401
from stock_analysis.db.base import Base


def test_models_composite_indexes_registered() -> None:
    """Verify that expected query pattern composite indexes exist in ORM metadata."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)

    # 1. Market bars composite indexes
    mb_indexes = {idx["name"]: idx for idx in inspector.get_indexes("market_bars")}
    assert "ix_market_bars_inst_ts" in mb_indexes
    assert mb_indexes["ix_market_bars_inst_ts"]["column_names"] == ["instrument_id", "timestamp"]

    assert "ix_market_bars_inst_adj_ts" in mb_indexes
    assert mb_indexes["ix_market_bars_inst_adj_ts"]["column_names"] == [
        "instrument_id",
        "is_adjusted",
        "timestamp",
    ]

    # 2. Alert rules composite indexes
    alert_indexes = {idx["name"]: idx for idx in inspector.get_indexes("alert_rules")}
    assert "ix_alert_rules_user_inst" in alert_indexes
    assert "ix_alert_rules_inst_active" in alert_indexes
    assert alert_indexes["ix_alert_rules_inst_active"]["column_names"] == [
        "instrument_id",
        "is_active",
    ]

    # 3. Forecasts composite indexes
    fc_indexes = {idx["name"]: idx for idx in inspector.get_indexes("forecasts")}
    assert "ix_forecasts_inst_target" in fc_indexes
    assert fc_indexes["ix_forecasts_inst_target"]["column_names"] == [
        "instrument_id",
        "target_date",
    ]
