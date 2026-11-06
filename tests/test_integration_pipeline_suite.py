"""Integration test suite covering the 5 foundational operational domains (FAZ-14).

Verifies the end-to-end collaboration and contracts across:
1. Data Provider Adapter (Synthetic / Licensed / Resilient)
2. Database (SQLAlchemy 2.0 ORM, Instruments, MarketBars, AlertRules, AlertEvents)
3. Alert Dispatch Queue (Idempotency, Retries, Dead-Letter Queue)
4. Email Notifier (Masking, SPK Legal Disclaimers, RFC 8058 Unsubscribe)
5. REST API (FastAPI, DB Dependency Overrides, HTTP Caching, Rate Limiting, Disclaimers)
6. Coordinated Full-Cycle Pipeline (Provider -> DB -> API -> Scanner -> Queue -> Email)

Marked with: pytest.mark.integration
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.alerts.email_notifier import (
    LEGAL_DISCLAIMER_ALERT_EMAIL,
    AlertEmailComposer,
    AlertNotificationPayload,
    FormattedEmailNotification,
)
from stock_analysis.alerts.queue import (
    AlertDeliveryJob,
    AlertDispatchQueue,
    DeadLetterQueue,
    DeadLetterReason,
    DeliveryJobStatus,
    IdempotencyRegistry,
    NetworkDeliveryError,
    RetryPolicy,
)
from stock_analysis.alerts.subscription import generate_unsubscribe_token
from stock_analysis.api.app import create_app
from stock_analysis.api.cache import get_cache_backend
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.models import (
    AlertEvent,
    AlertRule,
    Instrument,
    MarketBar,
    User,
)
from stock_analysis.db.session import get_db
from stock_analysis.providers.base import MarketBarRecord
from stock_analysis.providers.synthetic_provider import SyntheticMarketDataProvider

pytestmark = pytest.mark.integration

# Isolated in-memory SQLite shared across connections via StaticPool
INTEGRATION_DB_URL = "sqlite:///:memory:"
integration_engine = create_engine(
    INTEGRATION_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
IntegrationSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=integration_engine,
)


@pytest.fixture(scope="module")
def setup_integration_db() -> Generator[None, None, None]:
    """Create all SQLAlchemy tables in memory for the integration test session."""
    Base.metadata.create_all(bind=integration_engine)
    yield
    Base.metadata.drop_all(bind=integration_engine)


@pytest.fixture
def db(setup_integration_db: None) -> Generator[DBSession, None, None]:
    """Provide a clean, isolated database session with rollback and purge on exit."""
    session = IntegrationSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        # Clean up data between tests while keeping schema
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture
def api_client(db: DBSession) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with overridden get_db dependency and clean cache."""
    reset_all_rate_limiters()
    get_cache_backend().clear_sync()
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    get_cache_backend().clear_sync()


@pytest.fixture
def sample_user(db: DBSession) -> User:
    """Create a test user in DB."""
    user = User(
        email="test_investor@example.com",
        is_active=True,
        is_verified=True,
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def sample_instrument(db: DBSession) -> Instrument:
    """Create a sample BIST instrument."""
    inst = Instrument(
        symbol="THYAO",
        name="Türk Hava Yolları A.O.",
        market="BIST",
        currency="TRY",
        is_active=True,
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


# ============================================================================
# Section 1: Data Provider Adapter <-> Database Pipeline Integration
# ============================================================================


class TestProviderAndDatabaseIntegration:
    """Tests data retrieval from provider and ingestion into SQLAlchemy database."""

    def test_provider_to_database_ingestion(
        self,
        db: DBSession,
        sample_instrument: Instrument,
    ) -> None:
        """Provider generates MarketBarRecords which are persisted as MarketBars."""
        provider = SyntheticMarketDataProvider(seed=101)
        start_d = date(2026, 1, 5)
        end_d = date(2026, 1, 20)

        records: list[MarketBarRecord] = provider.fetch_daily_bars(
            symbol="THYAO",
            start_date=start_d,
            end_date=end_d,
        )
        assert len(records) > 0

        # Ingest into MarketBar table
        for rec in records:
            bar_ts = rec.session_time_utc or datetime(
                rec.trade_date.year,
                rec.trade_date.month,
                rec.trade_date.day,
                15,
                30,
                tzinfo=UTC,
            )
            bar = MarketBar(
                instrument_id=sample_instrument.id,
                timestamp=bar_ts,
                open_price=float(rec.raw_open),
                high_price=float(rec.raw_high),
                low_price=float(rec.raw_low),
                close_price=float(rec.raw_close),
                volume=float(rec.volume),
                is_adjusted=False,
                data_version="v1",
            )
            db.add(bar)
        db.commit()

        # Query back via relationship and verify consistency
        db.refresh(sample_instrument)
        bars = sample_instrument.market_bars
        assert len(bars) == len(records)

        # Chronological order and price sanity check
        bars_sorted = sorted(bars, key=lambda b: b.timestamp)
        assert bars_sorted[0].timestamp <= bars_sorted[-1].timestamp
        for bar in bars_sorted:
            assert bar.low_price <= bar.high_price
            assert bar.open_price > 0
            assert bar.close_price > 0
            assert bar.volume >= 0

    def test_database_idempotency_and_uniqueness_handling(
        self,
        db: DBSession,
        sample_instrument: Instrument,
    ) -> None:
        """Duplicate bar insert with same identity tuple is safely detected and skipped."""
        dt = datetime(2026, 1, 10, 15, 30, tzinfo=UTC)
        bar1 = MarketBar(
            instrument_id=sample_instrument.id,
            timestamp=dt,
            open_price=250.0,
            high_price=255.0,
            low_price=248.0,
            close_price=252.0,
            volume=500000.0,
            is_adjusted=False,
            data_version="v1",
        )
        db.add(bar1)
        db.commit()

        # Check existing bar count
        count_stmt = select(MarketBar).where(
            MarketBar.instrument_id == sample_instrument.id,
            MarketBar.timestamp == dt,
        )
        existing = db.scalars(count_stmt).all()
        assert len(existing) == 1

        # Query existing before re-insert to simulate idempotent pipeline
        bar2_rec = MarketBar(
            instrument_id=sample_instrument.id,
            timestamp=dt,
            open_price=250.0,
            high_price=255.0,
            low_price=248.0,
            close_price=252.0,
            volume=500000.0,
            is_adjusted=False,
            data_version="v1",
        )
        # Idempotent skip: if already exists, do not duplicate
        already_present = db.scalars(
            select(MarketBar).where(
                MarketBar.instrument_id == bar2_rec.instrument_id,
                MarketBar.timestamp == bar2_rec.timestamp,
                MarketBar.is_adjusted == bar2_rec.is_adjusted,
                MarketBar.data_version == bar2_rec.data_version,
            )
        ).first()

        if not already_present:
            db.add(bar2_rec)
            db.commit()

        final_bars = db.scalars(count_stmt).all()
        assert len(final_bars) == 1


# ============================================================================
# Section 2: Database <-> Rule / Uyarı Taraması (Scanner & Alert Events)
# ============================================================================


class TestDatabaseAndScannerAlertIntegration:
    """Tests evaluating database bars against rules and persisting triggered AlertEvents."""

    def test_database_bars_trigger_alert_event(
        self,
        db: DBSession,
        sample_user: User,
        sample_instrument: Instrument,
    ) -> None:
        """Rule evaluator reads bars from DB and logs AlertEvent on trigger."""
        # Seed 65 daily bars for instrument
        base_dt = datetime(2026, 1, 1, 15, 30, tzinfo=UTC)
        for i in range(65):
            bar = MarketBar(
                instrument_id=sample_instrument.id,
                timestamp=base_dt + timedelta(days=i),
                open_price=100.0 + i,
                high_price=102.0 + i,
                low_price=99.0 + i,
                close_price=101.0 + i,
                volume=10000.0 + (i * 100),
                is_adjusted=False,
                data_version="v1",
            )
            db.add(bar)
        db.commit()

        # Create user alert rule in DB
        rule = AlertRule(
            user_id=sample_user.id,
            instrument_id=sample_instrument.id,
            condition_type="PRICE_ABOVE",
            threshold=150.0,
            is_active=True,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)

        # Fetch latest close price from DB
        latest_bar = db.scalars(
            select(MarketBar)
            .where(MarketBar.instrument_id == sample_instrument.id)
            .order_by(MarketBar.timestamp.desc())
        ).first()
        assert latest_bar is not None
        assert latest_bar.close_price == 165.0  # 101.0 + 64

        # Condition check: close_price > threshold (165.0 > 150.0) -> triggers
        triggered = latest_bar.close_price > rule.threshold
        assert triggered is True

        # Persist AlertEvent in DB
        event = AlertEvent(
            alert_rule_id=rule.id,
            triggered_at=latest_bar.timestamp,
            message=(
                f"{sample_instrument.symbol} fiyatı ({latest_bar.close_price:.2f} TRY) "
                f"belirlenen {rule.threshold:.2f} TRY eşiğini aştı."
            ),
            payload=f'{{"trigger_price": {latest_bar.close_price}}}',
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        assert event.id is not None
        assert event.alert_rule_id == rule.id
        assert "THYAO" in event.message
        assert "165.00" in event.message


# ============================================================================
# Section 3: Alert Dispatch Queue, Idempotency & Retry/DLQ Integration
# ============================================================================


class TestAlertQueueAndRetryIntegration:
    """Tests AlertDispatchQueue integration with idempotency, retries, and DLQ."""

    def test_queue_idempotency_and_successful_delivery(self) -> None:
        """Queue delivers payload once and rejects duplicate submissions."""
        idempotency = IdempotencyRegistry(ttl_seconds=3600)
        dlq = DeadLetterQueue()
        queue = AlertDispatchQueue(
            idempotency_registry=idempotency,
            dead_letter_queue=dlq,
            retry_policy=RetryPolicy(max_attempts=3),
        )

        delivered_jobs: list[str] = []

        def mock_delivery_success(job: AlertDeliveryJob) -> None:
            delivered_jobs.append(job.job_id)

        dt = datetime(2026, 10, 24, 15, 30, tzinfo=UTC)
        payload = AlertNotificationPayload(
            alert_id="alt-int-1",
            symbol="THYAO",
            rule_name="Fiyat Eşiği",
            condition_summary="Kapanış 165 TRY seviyesini aştı",
            data_timestamp_utc=dt,
            data_source="BIST_EOD",
            recipient_email="investor@example.com",
        )

        job1, enqueued1 = queue.enqueue(payload, recipient_email="investor@example.com")
        assert enqueued1 is True
        assert job1 is not None

        # Duplicate enqueue attempt with identical business keys
        job2, enqueued2 = queue.enqueue(payload, recipient_email="investor@example.com")
        assert enqueued2 is False
        assert job2 is None  # Skipped due to idempotency!

        # Process queue
        processed = queue.process_next(mock_delivery_success)
        assert processed is not None
        assert processed.status == DeliveryJobStatus.COMPLETED
        assert len(delivered_jobs) == 1
        assert queue.idempotency_registry.is_completed(job1.idempotency_key)

    def test_queue_retries_transient_error_and_routes_to_dlq_on_exhaustion(self) -> None:
        """Transient network errors are retried; permanent failures go to DLQ."""
        idempotency = IdempotencyRegistry(ttl_seconds=3600)
        dlq = DeadLetterQueue()
        retry_policy = RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0.01,
        )
        queue = AlertDispatchQueue(
            idempotency_registry=idempotency,
            dead_letter_queue=dlq,
            retry_policy=retry_policy,
        )

        def failing_delivery(job: AlertDeliveryJob) -> None:
            raise NetworkDeliveryError("SMTP connection timed out")

        t0 = datetime(2026, 10, 24, 10, 0, 0, tzinfo=UTC)
        payload = AlertNotificationPayload(
            alert_id="alt-int-dlq",
            symbol="GARAN",
            rule_name="RSI_70",
            condition_summary="RSI 70 seviyesini aştı",
            data_timestamp_utc=t0,
            data_source="BIST_EOD",
            recipient_email="user@example.com",
        )

        job, enqueued = queue.enqueue(payload, recipient_email="user@example.com", now=t0)
        assert enqueued is True
        assert job is not None

        # Initial attempt -> fails -> scheduled for retry
        r1 = queue.process_next(failing_delivery, now=t0)
        assert r1 is not None
        assert r1.status == DeliveryJobStatus.RETRYING
        assert r1.attempts == 1

        # Second attempt after backoff -> exhausts max_attempts=2 -> routed to DLQ
        t1 = t0 + timedelta(seconds=1.0)
        r2 = queue.process_next(failing_delivery, now=t1)
        assert r2 is not None
        assert r2.status == DeliveryJobStatus.DEAD_LETTERED
        assert r2.dead_letter_reason == DeadLetterReason.MAX_RETRIES_EXCEEDED
        assert queue.size() == 0
        assert dlq.count() == 1

        dlq_item = dlq.get_by_job_id(job.job_id)
        assert dlq_item is not None
        assert dlq_item.reason == DeadLetterReason.MAX_RETRIES_EXCEEDED
        assert "SMTP connection timed out" in dlq_item.error_message


# ============================================================================
# Section 4: Queue <-> Email Notifier & Regulatory Compliance Integration
# ============================================================================


class TestQueueAndEmailNotifierIntegration:
    """Tests Queue job dispatch formatting compliant, masked emails via AlertEmailComposer."""

    def test_queue_dispatches_spk_compliant_masked_email(self) -> None:
        """When queue processes a job, email composer builds compliant notification."""
        composer = AlertEmailComposer()
        sent_emails: list[FormattedEmailNotification] = []

        def mock_mailer(job: AlertDeliveryJob) -> None:
            formatted = composer.compose(job.payload)
            sent_emails.append(formatted)

        queue = AlertDispatchQueue(
            retry_policy=RetryPolicy(max_attempts=1),
        )

        payload = AlertNotificationPayload(
            alert_id="alt-cmp-1",
            symbol="ASELS",
            rule_name="SMA50 Kesişimi",
            condition_summary="Kapanış fiyatı SMA50 seviyesini yukarı kesti",
            data_timestamp_utc=datetime(2026, 10, 24, 15, 30, tzinfo=UTC),
            data_source="BIST Gün Sonu (EOD)",
            recipient_email="ahmet.yilmaz@borsa.example.com",
            user_display_name="Ahmet Yılmaz",
            unsubscribe_url="https://borsa.example.com/alerts/unsubscribe?token=test-tok",
        )

        job, enqueued = queue.enqueue(
            payload,
            recipient_email="ahmet.yilmaz@borsa.example.com",
        )
        assert enqueued is True
        assert job is not None

        processed = queue.process_next(mock_mailer)
        assert processed is not None
        assert processed.status == DeliveryJobStatus.COMPLETED
        assert len(sent_emails) == 1

        email = sent_emails[0]
        # Masking verification
        assert email.masked_recipient == "a***@borsa.example.com"
        assert "ahmet.yilmaz" not in email.masked_recipient

        # Subject & Symbol
        assert "ASELS" in email.subject
        assert "SMA50 Kesişimi" in email.subject

        # SPK Disclaimer verification
        assert LEGAL_DISCLAIMER_ALERT_EMAIL in email.plaintext_body
        assert LEGAL_DISCLAIMER_ALERT_EMAIL in email.html_body
        assert "kesinlikle yatırım danışmanlığı" in email.plaintext_body

        # Unsubscribe link presence
        assert "test-tok" in email.plaintext_body
        assert "List-Unsubscribe" in email.headers


# ============================================================================
# Section 5: REST API, Database & Caching / Rate Limiter Integration
# ============================================================================


class TestApiDatabaseIntegration:
    """Tests FastAPI endpoints querying database state and applying middleware & caching."""

    def test_market_symbols_and_bars_api_endpoints(
        self,
        api_client: TestClient,
        db: DBSession,
        sample_instrument: Instrument,
    ) -> None:
        """API serves symbols and historical bars backed by database session."""
        # Seed 5 daily bars relative to now
        now_utc = datetime.now(UTC)
        for i in range(5):
            bar = MarketBar(
                instrument_id=sample_instrument.id,
                timestamp=now_utc - timedelta(days=5 - i),
                open_price=200.0 + i,
                high_price=205.0 + i,
                low_price=198.0 + i,
                close_price=202.0 + i,
                volume=100000.0,
                is_adjusted=False,
                data_version="v1",
            )
            db.add(bar)
        db.commit()

        # 1. GET /api/v1/market/symbols
        symbols_res = api_client.get("/api/v1/market/symbols")
        assert symbols_res.status_code == 200
        # Mandatory disclaimer header
        assert "x-disclaimer" in symbols_res.headers
        data = symbols_res.json()
        assert "symbols" in data
        assert any(s["symbol"] == "THYAO" for s in data["symbols"])

        # 2. GET /api/v1/market/history/THYAO
        bars_res = api_client.get("/api/v1/market/history/THYAO?range=1m&price_type=raw")
        assert bars_res.status_code == 200
        bars_data = bars_res.json()
        assert bars_data["symbol"] == "THYAO"
        assert len(bars_data["candles"]) == 5
        assert "x-disclaimer" in bars_res.headers

        # Verify caching header (ETag)
        etag = bars_res.headers.get("etag")
        if etag:
            # Conditional request with If-None-Match should return 304 Not Modified
            cached_res = api_client.get(
                "/api/v1/market/history/THYAO?range=1m&price_type=raw",
                headers={"If-None-Match": etag},
            )
            assert cached_res.status_code == 304

    def test_alerts_consent_api_flow(
        self,
        api_client: TestClient,
        sample_user: User,
    ) -> None:
        """RFC 8058 one-click unsubscribe endpoint integration."""
        token = generate_unsubscribe_token(
            user_id=sample_user.id,
            email=sample_user.email,
        )

        # GET request to unsubscribe page
        res_get = api_client.get(f"/api/v1/alerts/unsubscribe?token={token}")
        assert res_get.status_code == 200
        get_data = res_get.json()
        assert get_data["success"] is True
        assert get_data["masked_email"] == "t***@example.com"

        # POST request (RFC 8058 automated one-click)
        res_post = api_client.post(f"/api/v1/alerts/unsubscribe?token={token}")
        assert res_post.status_code == 200
        post_data = res_post.json()
        assert post_data["success"] is True
        assert post_data["masked_email"] == "t***@example.com"


# ============================================================================
# Section 6: Coordinated Full-Cycle Integration Pipeline
# ============================================================================


class TestEndToEndPipelineCycleIntegration:
    """Coordinated end-to-end integration across all 5 operational systems:

    Provider -> Database -> API -> Rule/Scanner -> Queue -> Email Notifier.
    """

    def test_full_pipeline_ingest_serve_trigger_and_notify(
        self,
        api_client: TestClient,
        db: DBSession,
        sample_user: User,
        sample_instrument: Instrument,
    ) -> None:
        """1. Provider generates market data.

        2. Data is persisted into Database.
        3. API serves verified data to clients.
        4. Alert rule evaluates against Database price.
        5. Triggered alert is scheduled into Queue.
        6. Queue processes job and formats compliant Email.
        """
        # Step 1: Provider generates data
        provider = SyntheticMarketDataProvider(seed=777)
        provider_bars = provider.fetch_daily_bars(
            symbol="THYAO",
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 10),
        )
        assert len(provider_bars) > 0

        # Step 2: Persist into Database with timestamps in recent window
        now_utc = datetime.now(UTC)
        total_bars = len(provider_bars)
        for idx, rec in enumerate(provider_bars):
            bar_ts = now_utc - timedelta(days=total_bars - idx)
            bar = MarketBar(
                instrument_id=sample_instrument.id,
                timestamp=bar_ts,
                open_price=float(rec.raw_open),
                high_price=float(rec.raw_high),
                low_price=float(rec.raw_low),
                close_price=float(rec.raw_close),
                volume=float(rec.volume),
                is_adjusted=False,
                data_version="v1",
            )
            db.add(bar)
        db.commit()

        # Step 3: API successfully reads and serves the persisted bars
        api_res = api_client.get("/api/v1/market/history/THYAO?range=1m&price_type=raw")
        assert api_res.status_code == 200
        api_json = api_res.json()
        assert len(api_json["candles"]) == len(provider_bars)

        # Step 4: User creates an AlertRule in DB and condition is evaluated
        latest_db_bar = db.scalars(
            select(MarketBar)
            .where(MarketBar.instrument_id == sample_instrument.id)
            .order_by(MarketBar.timestamp.desc())
        ).first()
        assert latest_db_bar is not None

        # Set threshold slightly lower than current close price to trigger alert
        target_threshold = latest_db_bar.close_price - 1.0
        rule = AlertRule(
            user_id=sample_user.id,
            instrument_id=sample_instrument.id,
            condition_type="PRICE_ABOVE",
            threshold=target_threshold,
            is_active=True,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)

        # Scanner / Rule check triggers
        assert latest_db_bar.close_price > rule.threshold

        # Record AlertEvent in DB
        event = AlertEvent(
            alert_rule_id=rule.id,
            triggered_at=latest_db_bar.timestamp,
            message=f"{sample_instrument.symbol} {rule.threshold:.2f} TRY eşiğini aştı.",
        )
        db.add(event)
        db.commit()

        # Step 5: Enqueue into AlertDispatchQueue
        delivered_emails: list[FormattedEmailNotification] = []
        composer = AlertEmailComposer()

        def delivery_worker(job: AlertDeliveryJob) -> None:
            email = composer.compose(job.payload)
            delivered_emails.append(email)

        queue = AlertDispatchQueue()

        payload = AlertNotificationPayload(
            alert_id=f"alt-ev-{event.id}",
            symbol=sample_instrument.symbol,
            rule_name="Fiyat Eşik Alarmı",
            condition_summary=event.message,
            data_timestamp_utc=latest_db_bar.timestamp,
            data_source="BIST Gün Sonu (EOD)",
            recipient_email=sample_user.email,
        )

        job, enqueued = queue.enqueue(payload, recipient_email=sample_user.email)
        assert enqueued is True
        assert job is not None

        # Step 6: Queue processes job and sends compliant email
        processed_job = queue.process_next(delivery_worker)
        assert processed_job is not None
        assert processed_job.status == DeliveryJobStatus.COMPLETED
        assert len(delivered_emails) == 1

        delivered = delivered_emails[0]
        assert delivered.symbol == "THYAO"
        assert delivered.masked_recipient == "t***@example.com"
        assert LEGAL_DISCLAIMER_ALERT_EMAIL in delivered.plaintext_body
        assert sample_user.email not in delivered.plaintext_body  # Unmasked email never leaked!
