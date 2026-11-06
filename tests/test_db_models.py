"""Tests for database models and schema design."""

from datetime import UTC, date, datetime

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session as DbSession

from stock_analysis.db import (
    AlertEvent,
    AlertRule,
    AuditEvent,
    Base,
    CorporateAction,
    Forecast,
    Instrument,
    MarketBar,
    ModelVersion,
    Session,
    User,
    Watchlist,
    WatchlistItem,
)


def test_metadata_contains_all_twelve_required_tables() -> None:
    expected_tables = {
        "users",
        "sessions",
        "instruments",
        "market_bars",
        "corporate_actions",
        "watchlists",
        "watchlist_items",
        "alert_rules",
        "alert_events",
        "model_versions",
        "forecasts",
        "audit_events",
    }
    assert expected_tables.issubset(Base.metadata.tables.keys())


def test_sqlite_in_memory_schema_creation_and_indexes() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert "market_bars" in table_names
    assert "users" in table_names

    # Check indexes on market_bars
    indexes = inspector.get_indexes("market_bars")
    index_columns = [idx["column_names"] for idx in indexes]
    assert ["instrument_id", "timestamp"] in index_columns


def test_models_crud_and_relationships_in_memory() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with DbSession(engine) as session:
        # Create user
        user = User(
            email="investor@example.com",
            hashed_password="argon2id$mockedhash123",
            role="user",
        )
        session.add(user)
        session.flush()

        # Create session
        user_session = Session(
            user_id=user.id,
            session_token_hash="sha256$tokensamplehash",
            provider="local",
            expires_at=datetime.now(UTC),
        )
        session.add(user_session)

        # Create instrument
        instrument = Instrument(
            symbol="THYAO",
            name="Türk Hava Yolları",
            market="BIST",
            currency="TRY",
        )
        session.add(instrument)
        session.flush()

        # Create market bar
        bar = MarketBar(
            instrument_id=instrument.id,
            timestamp=datetime(2026, 10, 22, 10, 0, tzinfo=UTC),
            open_price=300.0,
            high_price=305.5,
            low_price=299.0,
            close_price=304.2,
            volume=1500000.0,
            is_adjusted=False,
            data_version="v1",
        )
        session.add(bar)

        # Create corporate action
        corp_action = CorporateAction(
            instrument_id=instrument.id,
            ex_date=date(2026, 11, 1),
            action_type="dividend",
            cash_amount=5.25,
        )
        session.add(corp_action)

        # Create watchlist & item
        watchlist = Watchlist(user_id=user.id, name="Favorilerim")
        session.add(watchlist)
        session.flush()

        item = WatchlistItem(watchlist_id=watchlist.id, instrument_id=instrument.id)
        session.add(item)

        # Create alert rule & event
        alert_rule = AlertRule(
            user_id=user.id,
            instrument_id=instrument.id,
            condition_type="price_above",
            threshold=300.0,
        )
        session.add(alert_rule)
        session.flush()

        alert_event = AlertEvent(
            alert_rule_id=alert_rule.id,
            message="THYAO 300 TL eşiğini aştı",
        )
        session.add(alert_event)

        # Create model version & forecast
        model_ver = ModelVersion(
            name="bist_lstm_v1",
            version_tag="v1.0.0",
            metrics='{"rmse": 1.25}',
            is_active=True,
        )
        session.add(model_ver)
        session.flush()

        forecast = Forecast(
            model_version_id=model_ver.id,
            instrument_id=instrument.id,
            target_date=date(2026, 10, 23),
            predicted_value=308.0,
            confidence_lower=302.0,
            confidence_upper=314.0,
        )
        session.add(forecast)

        # Create audit event
        audit = AuditEvent(
            user_id=user.id,
            action="login",
            resource="/api/v1/auth/login",
            ip_hash="e3b0c44298fc1c149afbf4c8996fb924",
        )
        session.add(audit)

        session.commit()

        # Query back and verify relationships
        queried_user = session.query(User).filter_by(email="investor@example.com").first()
        assert queried_user is not None
        assert len(queried_user.watchlists) == 1
        assert queried_user.watchlists[0].items[0].instrument.symbol == "THYAO"
        assert len(queried_user.alert_rules) == 1
        assert len(queried_user.alert_rules[0].events) == 1
        assert queried_user.alert_rules[0].events[0].message == "THYAO 300 TL eşiğini aştı"

        queried_inst = session.query(Instrument).filter_by(symbol="THYAO").first()
        assert queried_inst is not None
        assert len(queried_inst.market_bars) == 1
        assert queried_inst.market_bars[0].close_price == 304.2
        assert len(queried_inst.corporate_actions) == 1
        assert queried_inst.corporate_actions[0].cash_amount == 5.25
        assert len(queried_inst.forecasts) == 1
        assert queried_inst.forecasts[0].predicted_value == 308.0
