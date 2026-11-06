"""Tests for alert dispatch queue, retry policy, DLQ, and idempotency."""

from datetime import UTC, datetime, timedelta

import pytest

from stock_analysis.alerts.email_notifier import (
    AlertNotificationPayload,
    SensitiveDataLeakError,
)
from stock_analysis.alerts.queue import (
    LEGAL_DISCLAIMER_ALERT_QUEUE,
    AlertDeliveryJob,
    AlertDispatchQueue,
    DeadLetterQueue,
    DeadLetterReason,
    DeliveryJobStatus,
    DeliveryTimeoutError,
    IdempotencyRegistry,
    NetworkDeliveryError,
    NonRetryableDeliveryError,
    RetryPolicy,
)


def _make_sample_payload(
    symbol: str = "THYAO",
    rule_name: str = "Teknik Trend Gostergesi",
    data_source: str = "matriks_licensed",
    recipient_email: str = "yatirimci@example.com",
    alert_id: str = "alt_12345",
    dt: datetime | None = None,
) -> AlertNotificationPayload:
    return AlertNotificationPayload(
        alert_id=alert_id,
        symbol=symbol,
        rule_name=rule_name,
        condition_summary="RSI 14 esigi ve SMA 50 buyuktur SMA 200 kosulu gerceklesti",
        data_timestamp_utc=dt or datetime(2026, 10, 24, 15, 30, tzinfo=UTC),
        data_source=data_source,
        recipient_email=recipient_email,
    )


def test_idempotency_key_generation() -> None:
    """Idempotency key is deterministic and changes when inputs vary."""
    key1 = AlertDeliveryJob.generate_idempotency_key(
        user_id="user1@example.com",
        symbol="THYAO",
        rule_name="RSI_Rule",
        data_timestamp="2026-10-24T15:30:00+00:00",
    )
    key2 = AlertDeliveryJob.generate_idempotency_key(
        user_id="user1@example.com",
        symbol="thyao",  # upper case normalization
        rule_name="RSI_Rule",
        data_timestamp="2026-10-24T15:30:00+00:00",
    )
    key_different_rule = AlertDeliveryJob.generate_idempotency_key(
        user_id="user1@example.com",
        symbol="THYAO",
        rule_name="MACD_Rule",
        data_timestamp="2026-10-24T15:30:00+00:00",
    )

    assert key1 == key2
    assert key1 != key_different_rule
    assert len(key1) == 64  # SHA256 hex


def test_retry_policy_validation_and_delays() -> None:
    """Retry policy validates inputs and calculates backoff intervals."""
    with pytest.raises(ValueError, match="max_attempts en az 1"):
        RetryPolicy(max_attempts=0)

    with pytest.raises(ValueError, match="base_delay_seconds pozitif"):
        RetryPolicy(base_delay_seconds=-1.0)

    policy = RetryPolicy(
        max_attempts=4,
        base_delay_seconds=2.0,
        exponential_factor=2.0,
        max_delay_seconds=10.0,
    )

    # Attempt 1 -> 0 delay
    assert policy.calculate_delay(1) == 0.0
    # Attempt 2 -> 2.0 * 2^0 = 2.0
    assert policy.calculate_delay(2) == 2.0
    # Attempt 3 -> 2.0 * 2^1 = 4.0
    assert policy.calculate_delay(3) == 4.0
    # Attempt 4 -> 2.0 * 2^2 = 8.0
    assert policy.calculate_delay(4) == 8.0
    # Attempt 5 -> capped at 10.0 (max_delay)
    assert policy.calculate_delay(5) == 10.0


def test_retry_policy_error_classification() -> None:
    """Classifies retryable transient errors vs fatal non-retryable errors."""
    policy = RetryPolicy()

    # Retryable
    assert policy.is_retryable(NetworkDeliveryError("Connection reset by peer"))
    assert policy.is_retryable(DeliveryTimeoutError("SMTP timed out"))
    assert policy.is_retryable(RuntimeError("Temporary 503 provider failure"))

    # Non-retryable
    assert not policy.is_retryable(SensitiveDataLeakError("Hassas bakiye sızıntısı"))
    assert not policy.is_retryable(NonRetryableDeliveryError("Recipient invalid"))
    assert not policy.is_retryable(ValueError("Invalid format"))


def test_idempotency_registry_duplicate_prevention() -> None:
    """Registry prevents double submission with same key."""
    registry = IdempotencyRegistry(ttl_seconds=3600)
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

    # First attempt succeeds
    is_new, job_id, status = registry.check_or_register("key_1", "job_123", now=now)
    assert is_new is True
    assert job_id is None

    # Second attempt with same key fails
    is_new2, job_id2, status2 = registry.check_or_register("key_1", "job_456", now=now)
    assert is_new2 is False
    assert job_id2 == "job_123"
    assert status2 == DeliveryJobStatus.PENDING

    # Update status to completed
    registry.update_status("key_1", DeliveryJobStatus.COMPLETED)
    assert registry.is_completed("key_1") is True

    # Check after TTL expiration (1 hour + 1 second)
    future = now + timedelta(seconds=3601)
    is_new3, _, _ = registry.check_or_register("key_1", "job_789", now=future)
    assert is_new3 is True


def test_enqueue_and_duplicate_suppression_in_queue() -> None:
    """Queue drops duplicate enqueue attempts and increments duplicate metric."""
    queue = AlertDispatchQueue()
    payload = _make_sample_payload()
    recipient = "yatirimci@example.com"

    job1, enqueued1 = queue.enqueue(payload, recipient_email=recipient)
    assert enqueued1 is True
    assert job1 is not None
    assert queue.size() == 1
    assert queue.metrics.enqueued_count == 1
    assert queue.metrics.duplicate_dropped_count == 0

    # Duplicate enqueue with same recipient, symbol, rule, and timestamp
    job2, enqueued2 = queue.enqueue(payload, recipient_email=recipient)
    assert enqueued2 is False
    assert job2 is None
    assert queue.size() == 1
    assert queue.metrics.duplicate_dropped_count == 1


def test_successful_dispatch_processing() -> None:
    """Queue processes job, calls handler, and marks status as completed."""
    queue = AlertDispatchQueue()
    payload = _make_sample_payload()
    recipient = "yatirimci@example.com"
    job, _ = queue.enqueue(payload, recipient_email=recipient)
    assert job is not None

    dispatched_jobs: list[AlertDeliveryJob] = []

    def mock_handler(j: AlertDeliveryJob) -> None:
        dispatched_jobs.append(j)

    processed = queue.process_next(mock_handler)
    assert processed is not None
    assert processed.job_id == job.job_id
    assert processed.status == DeliveryJobStatus.COMPLETED
    assert processed.attempts == 1
    assert len(dispatched_jobs) == 1
    assert queue.size() == 0
    assert queue.metrics.processed_count == 1
    assert queue.idempotency_registry.is_completed(job.idempotency_key)


def test_retry_on_transient_failure_then_success() -> None:
    """Queue retries on transient errors with backoff and succeeds on attempt 2."""
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=2.0)
    queue = AlertDispatchQueue(retry_policy=policy)
    t0 = datetime(2026, 10, 24, 10, 0, 0, tzinfo=UTC)
    payload = _make_sample_payload()
    job, _ = queue.enqueue(payload, recipient_email="user@test.com", now=t0)
    assert job is not None

    calls = 0

    def flaky_handler(j: AlertDeliveryJob) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DeliveryTimeoutError("SMTP connection timed out")
        # Second call succeeds

    # First attempt fails
    result1 = queue.process_next(flaky_handler, now=t0)
    assert result1 is not None
    assert result1.status == DeliveryJobStatus.RETRYING
    assert result1.attempts == 1
    assert "SMTP connection timed out" in (result1.last_error or "")
    assert queue.size() == 1  # Re-enqueued
    assert queue.metrics.retry_count == 1

    # Before backoff expires, job is not yet eligible to process
    t_too_early = t0 + timedelta(seconds=1.0)
    result_early = queue.process_next(flaky_handler, now=t_too_early)
    assert result_early is None  # Not yet ready

    # After backoff (2.0s delay for attempt 2)
    t_ready = t0 + timedelta(seconds=2.1)
    result2 = queue.process_next(flaky_handler, now=t_ready)
    assert result2 is not None
    assert result2.status == DeliveryJobStatus.COMPLETED
    assert result2.attempts == 2
    assert calls == 2
    assert queue.size() == 0
    assert queue.metrics.processed_count == 1


def test_max_retries_exceeded_routes_to_dlq() -> None:
    """When retry attempts are exhausted, job is moved to DeadLetterQueue."""
    policy = RetryPolicy(max_attempts=2, base_delay_seconds=1.0)
    dlq = DeadLetterQueue()
    queue = AlertDispatchQueue(retry_policy=policy, dead_letter_queue=dlq)

    t0 = datetime(2026, 10, 24, 10, 0, 0, tzinfo=UTC)
    payload = _make_sample_payload()
    job, _ = queue.enqueue(payload, recipient_email="user@test.com", now=t0)
    assert job is not None

    def failing_handler(j: AlertDeliveryJob) -> None:
        raise NetworkDeliveryError("Network down")

    # Attempt 1 -> fails, retry scheduled
    r1 = queue.process_next(failing_handler, now=t0)
    assert r1 is not None
    assert r1.status == DeliveryJobStatus.RETRYING

    # Attempt 2 -> fails, max attempts reached -> sent to DLQ
    t1 = t0 + timedelta(seconds=2.0)
    r2 = queue.process_next(failing_handler, now=t1)
    assert r2 is not None
    assert r2.status == DeliveryJobStatus.DEAD_LETTERED
    assert r2.dead_letter_reason == DeadLetterReason.MAX_RETRIES_EXCEEDED
    assert queue.size() == 0
    assert queue.metrics.dead_letter_count == 1

    # Verify DLQ record
    assert dlq.count() == 1
    dlq_record = dlq.get_by_job_id(job.job_id)
    assert dlq_record is not None
    assert dlq_record.symbol == "THYAO"
    assert dlq_record.reason == DeadLetterReason.MAX_RETRIES_EXCEEDED
    assert dlq_record.total_attempts == 2
    assert "Network down" in dlq_record.error_message


def test_fatal_non_retryable_error_routed_immediately_to_dlq() -> None:
    """SensitiveDataLeakError or NonRetryableDeliveryError immediately routes to DLQ."""
    queue = AlertDispatchQueue()
    payload = _make_sample_payload()
    job, _ = queue.enqueue(payload, recipient_email="user@test.com")
    assert job is not None

    def leak_handler(j: AlertDeliveryJob) -> None:
        raise SensitiveDataLeakError("Hassas bakiye sızıntısı yakalandı!")

    res = queue.process_next(leak_handler)
    assert res is not None
    assert res.status == DeliveryJobStatus.DEAD_LETTERED
    assert res.dead_letter_reason == DeadLetterReason.SENSITIVE_DATA_VIOLATION
    assert res.attempts == 1
    assert queue.size() == 0  # Not retried!
    assert queue.metrics.retry_count == 0
    assert queue.metrics.dead_letter_count == 1

    dlq_record = queue.dead_letter_queue.get_by_job_id(job.job_id)
    assert dlq_record is not None
    assert dlq_record.reason == DeadLetterReason.SENSITIVE_DATA_VIOLATION


def test_dlq_capacity_and_listing_and_purge() -> None:
    """DLQ respects capacity, allows querying by symbol, and purging."""
    dlq = DeadLetterQueue(max_capacity=2)
    queue = AlertDispatchQueue(dead_letter_queue=dlq)

    # Push 3 records to capacity-2 DLQ
    for sym in ["THYAO", "GARAN", "SISE"]:
        payload = _make_sample_payload(symbol=sym)
        job, _ = queue.enqueue(
            payload, recipient_email=f"u_{sym}@test.com", custom_job_id=f"job_{sym}"
        )
        error_msg = f"Bad {sym}"

        def bad_handler(j: AlertDeliveryJob, err: str = error_msg) -> None:
            raise NonRetryableDeliveryError(err)

        queue.process_next(bad_handler)

    assert dlq.count() == 2  # Oldest (THYAO) popped
    symbols = [r.symbol for r in dlq.list_records()]
    assert "GARAN" in symbols
    assert "SISE" in symbols
    assert "THYAO" not in symbols

    # Filter by symbol
    garan_records = dlq.list_records(symbol="GARAN")
    assert len(garan_records) == 1
    assert garan_records[0].symbol == "GARAN"

    # Purge
    purged = dlq.purge()
    assert purged == 2
    assert dlq.count() == 0


def test_dlq_replay_mechanism() -> None:
    """Dead-lettered jobs can be replayed back into queue with reset attempts."""
    queue = AlertDispatchQueue()
    payload = _make_sample_payload(symbol="KCHOL")
    job, _ = queue.enqueue(payload, recipient_email="kchol@test.com", custom_job_id="job_kchol")

    def fatal_handler(j: AlertDeliveryJob) -> None:
        raise NonRetryableDeliveryError("Temporary outage")

    # Send to DLQ with fatal error
    queue.process_next(fatal_handler)
    assert queue.dead_letter_queue.count() == 1
    assert queue.size() == 0

    # Replay job from DLQ
    replayed_job = queue.replay_dead_letter("job_kchol")
    assert replayed_job is not None
    assert replayed_job.job_id == "job_kchol"
    assert replayed_job.attempts == 0
    assert queue.dead_letter_queue.count() == 0
    assert queue.size() == 1

    # Now handler succeeds
    handled = queue.process_next(lambda j: None)
    assert handled is not None
    assert handled.status == DeliveryJobStatus.COMPLETED
    assert queue.size() == 0


def test_process_all_batch_execution() -> None:
    """process_all processes batch of ready jobs up to max_jobs limit."""
    queue = AlertDispatchQueue()
    for i in range(5):
        payload = _make_sample_payload(
            symbol=f"SYM{i}", dt=datetime(2026, 10, 24, 15, i, tzinfo=UTC)
        )
        queue.enqueue(payload, recipient_email=f"user{i}@test.com")

    assert queue.size() == 5

    processed = queue.process_all(lambda j: None, max_jobs=3)
    assert len(processed) == 3
    assert queue.size() == 2

    processed_rest = queue.process_all(lambda j: None, max_jobs=10)
    assert len(processed_rest) == 2
    assert queue.size() == 0


def test_legal_disclaimer() -> None:
    """Queue module contains explicit SPK disclaimer."""
    assert "SPK Uyarısı" in LEGAL_DISCLAIMER_ALERT_QUEUE
    assert "yatırım tavsiyesi" in LEGAL_DISCLAIMER_ALERT_QUEUE
