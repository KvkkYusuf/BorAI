"""Uyarı iletim kuyruğu, yeniden deneme (retry), dead-letter ve idempotency modülü.

Bu modül bildirim iletim altyapısıdır; yatırım tavsiyesi, al/sat sinyali veya
portföy yönetimi içermez.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from stock_analysis.alerts.email_notifier import (
    AlertNotificationPayload,
    SensitiveDataLeakError,
)
from stock_analysis.observability.metrics import QUEUE_PENDING_TASKS
from stock_analysis.observability.operational import OPERATIONAL_MONITOR

logger = logging.getLogger(__name__)

LEGAL_DISCLAIMER_ALERT_QUEUE: str = (
    "SPK Uyarısı: Bu bildirim altyapısı yalnızca teknik kuyruk ve teslimat yönetimini "
    "sağlar. İletilen veriler hiçbir koşulda yatırım tavsiyesi, al/sat önerisi veya "
    "portföy yöneticiliği kapsamında değerlendirilemez."
)


class DeliveryJobStatus(StrEnum):
    """Uyarı bildirim işi durumu."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    RETRYING = "retrying"
    DEAD_LETTERED = "dead_lettered"
    DISCARDED = "discarded"


class DeadLetterReason(StrEnum):
    """Dead-Letter kuyruğuna alınma gerekçeleri."""

    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    FATAL_NON_RETRYABLE_ERROR = "fatal_non_retryable_error"
    SENSITIVE_DATA_VIOLATION = "sensitive_data_violation"
    CONSENT_REVOKED = "consent_revoked"
    INVALID_PAYLOAD = "invalid_payload"


class NonRetryableDeliveryError(Exception):
    """Yeniden denenmemesi gereken, kalıcı teslimat hatası."""


class DeliveryTimeoutError(Exception):
    """Geçici teslimat zaman aşımı hatası (yeniden denenebilir)."""


class NetworkDeliveryError(Exception):
    """Geçici ağ veya SMTP bağlantı hatası (yeniden denenebilir)."""


@dataclass(frozen=True)
class RetryPolicy:
    """Üstel geri çekilme ve azami deneme yapılandırması."""

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    exponential_factor: float = 2.0
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts en az 1 olmalıdır.")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds pozitif olmalıdır.")
        if self.exponential_factor < 1.0:
            raise ValueError("exponential_factor en az 1.0 olmalıdır.")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds, base_delay_seconds'tan küçük olamaz.")

    def calculate_delay(self, attempt: int) -> float:
        """Deneme sırasına göre bekleme süresini hesaplar (1-indexed attempt)."""
        if attempt <= 1:
            return 0.0
        exponent = attempt - 1
        delay = self.base_delay_seconds * (self.exponential_factor ** (exponent - 1))
        return min(delay, self.max_delay_seconds)

    def is_retryable(self, exc: Exception) -> bool:
        """Hatanın yeniden denenebilir (transient) olup olmadığını belirler."""
        if isinstance(exc, (SensitiveDataLeakError, NonRetryableDeliveryError, ValueError)):
            return False
        return True


@dataclass
class AlertDeliveryJob:
    """Kuyruktaki uyarı teslimat işi."""

    job_id: str
    idempotency_key: str
    payload: AlertNotificationPayload
    recipient_email: str
    status: DeliveryJobStatus = DeliveryJobStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    next_attempt_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_error: str | None = None
    dead_letter_reason: DeadLetterReason | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def generate_idempotency_key(
        cls,
        user_id: str,
        symbol: str,
        rule_name: str,
        data_timestamp: str,
    ) -> str:
        """Kullanıcı, sembol, kural ve veri zamanına göre idempotency anahtarı üretir."""
        raw = f"{user_id}|{symbol.upper()}|{rule_name}|{data_timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeadLetterRecord:
    """DLQ (Dead-Letter Queue) kayıt girdisi."""

    job_id: str
    idempotency_key: str
    symbol: str
    recipient_email: str
    failure_timestamp: datetime
    reason: DeadLetterReason
    error_message: str
    total_attempts: int
    payload_snapshot: dict[str, Any]


class IdempotencyRegistry:
    """Mükerrer işleri önleyen bellek içi idempotency kayıt defteri."""

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self.ttl_seconds = ttl_seconds
        self._records: dict[str, tuple[str, DeliveryJobStatus, datetime]] = {}
        self._lock = threading.Lock()

    def check_or_register(
        self,
        key: str,
        job_id: str,
        now: datetime | None = None,
    ) -> tuple[bool, str | None, DeliveryJobStatus | None]:
        """Anahtarı kontrol eder.

        Varsa (False, existing_job_id, status) döner.
        Yoksa yeni işi kaydeder ve (True, None, None) döner.
        """
        current_time = now or datetime.now(UTC)
        with self._lock:
            self._cleanup_expired(current_time)

            if key in self._records:
                existing_job_id, status, _ = self._records[key]
                return False, existing_job_id, status

            self._records[key] = (job_id, DeliveryJobStatus.PENDING, current_time)
            return True, None, None

    def update_status(
        self,
        key: str,
        status: DeliveryJobStatus,
    ) -> None:
        """İşin idempotency kaydındaki durumunu günceller."""
        with self._lock:
            if key in self._records:
                job_id, _, registered_at = self._records[key]
                self._records[key] = (job_id, status, registered_at)

    def is_completed(self, key: str) -> bool:
        """İşin başarıyla tamamlanıp tamamlanmadığını kontrol eder."""
        with self._lock:
            if key in self._records:
                return self._records[key][1] == DeliveryJobStatus.COMPLETED
            return False

    def remove(self, key: str) -> None:
        """Gerektiğinde anahtarı kayıttan kaldırır (ör. geri alma durumunda)."""
        with self._lock:
            self._records.pop(key, None)

    def clear(self) -> None:
        """Tüm idempotency kayıtlarını temizler."""
        with self._lock:
            self._records.clear()

    def _cleanup_expired(self, current_time: datetime) -> None:
        expired_keys = [
            k
            for k, (_, _, registered_at) in self._records.items()
            if (current_time - registered_at).total_seconds() > self.ttl_seconds
        ]
        for k in expired_keys:
            del self._records[k]


class DeadLetterQueue:
    """Başarısız ve işlenemeyen bildirimlerin tutulduğu ve incelendiği kuyruk."""

    def __init__(self, max_capacity: int = 1000) -> None:
        self.max_capacity = max_capacity
        self._records: list[DeadLetterRecord] = []
        self._lock = threading.Lock()

    def push(self, record: DeadLetterRecord) -> None:
        """Kayıt ekler; kapasite aşımında en eski kayıt çıkarılır."""
        with self._lock:
            if len(self._records) >= self.max_capacity:
                self._records.pop(0)
            self._records.append(record)
            logger.warning(
                f"[DLQ] Bildirim dead-letter kuyruğuna alındı: job_id={record.job_id}, "
                f"symbol={record.symbol}, sebep={record.reason.value}"
            )

    def list_records(self, symbol: str | None = None) -> list[DeadLetterRecord]:
        """Kuyruktaki kayıtları listeler."""
        with self._lock:
            if symbol is None:
                return list(self._records)
            return [r for r in self._records if r.symbol.upper() == symbol.upper()]

    def get_by_job_id(self, job_id: str) -> DeadLetterRecord | None:
        """Belirtilen job_id kaydını bulur."""
        with self._lock:
            for r in self._records:
                if r.job_id == job_id:
                    return r
            return None

    def remove(self, job_id: str) -> DeadLetterRecord | None:
        """Kayıt kuyruktan çıkarılır (ör. yeniden işletilmek istendiğinde)."""
        with self._lock:
            for idx, r in enumerate(self._records):
                if r.job_id == job_id:
                    return self._records.pop(idx)
            return None

    def purge(self) -> int:
        """Tüm DLQ kayıtlarını temizler ve silinen kayıt sayısını döner."""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            return count

    def count(self) -> int:
        """DLQ içindeki kayıt sayısını döner."""
        with self._lock:
            return len(self._records)


@dataclass
class QueueMetrics:
    """Kuyruk metrikleri."""

    enqueued_count: int = 0
    duplicate_dropped_count: int = 0
    processed_count: int = 0
    retry_count: int = 0
    dead_letter_count: int = 0
    discarded_count: int = 0


class AlertDispatchQueue:
    """Dayanıklı uyarı dağıtım ve kuyruk yönetim sistemi."""

    def __init__(
        self,
        retry_policy: RetryPolicy | None = None,
        idempotency_registry: IdempotencyRegistry | None = None,
        dead_letter_queue: DeadLetterQueue | None = None,
    ) -> None:
        self.retry_policy = retry_policy or RetryPolicy()
        self.idempotency_registry = idempotency_registry or IdempotencyRegistry()
        self.dead_letter_queue = dead_letter_queue or DeadLetterQueue()
        self._queue: list[AlertDeliveryJob] = []
        self._lock = threading.Lock()
        self._metrics = QueueMetrics()

    @property
    def metrics(self) -> QueueMetrics:
        """Kuyruk metrikleri kopyası."""
        return self._metrics

    def size(self) -> int:
        """Kuyruktaki bekleyen iş sayısı."""
        with self._lock:
            return len(self._queue)

    def enqueue(
        self,
        payload: AlertNotificationPayload,
        recipient_email: str | None = None,
        idempotency_key: str | None = None,
        custom_job_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AlertDeliveryJob | None, bool]:
        """Bildirimi kuyruğa ekler.

        Eğer aynı idempotency key zaten kayıtlıysa ekleme yapılmaz;
        (None, False) döner.
        Yeni ekleme yapıldıysa (job, True) döner.
        """
        current_time = now or datetime.now(UTC)
        target_email = recipient_email or payload.recipient_email
        data_ts = payload.data_timestamp_utc.isoformat()
        key = idempotency_key or AlertDeliveryJob.generate_idempotency_key(
            user_id=target_email,
            symbol=payload.symbol,
            rule_name=payload.rule_name,
            data_timestamp=data_ts,
        )

        job_raw = f"{key}|{current_time.timestamp()}".encode()
        job_id = custom_job_id or hashlib.sha256(job_raw).hexdigest()[:16]

        is_new, existing_id, status = self.idempotency_registry.check_or_register(
            key=key,
            job_id=job_id,
            now=current_time,
        )

        if not is_new:
            self._metrics.duplicate_dropped_count += 1
            logger.info(
                f"[Queue] Mükerrer iş yok sayıldı: key={key[:12]}, "
                f"existing_job={existing_id}, status={status}"
            )
            return None, False

        job = AlertDeliveryJob(
            job_id=job_id,
            idempotency_key=key,
            payload=payload,
            recipient_email=target_email,
            status=DeliveryJobStatus.PENDING,
            attempts=0,
            max_attempts=self.retry_policy.max_attempts,
            created_at=current_time,
            next_attempt_at=current_time,
        )

        with self._lock:
            self._queue.append(job)
            self._metrics.enqueued_count += 1
            current_depth = len(self._queue)

        QUEUE_PENDING_TASKS.set(current_depth, labels={"queue": "alert_dispatch"})
        return job, True

    def process_next(
        self,
        handler: Callable[[AlertDeliveryJob], Any],
        now: datetime | None = None,
    ) -> AlertDeliveryJob | None:
        """Kuyruktaki ilk uygun işi çeker ve handler ile işletir."""
        current_time = now or datetime.now(UTC)

        with self._lock:
            if not self._queue:
                return None

            selected_idx = -1
            for idx, item in enumerate(self._queue):
                if item.next_attempt_at <= current_time:
                    selected_idx = idx
                    break

            if selected_idx == -1:
                return None

            job = self._queue.pop(selected_idx)
            job.status = DeliveryJobStatus.IN_PROGRESS
            job.attempts += 1
            remaining_depth = len(self._queue)

        wait_seconds = max(0.0, (current_time - job.created_at).total_seconds())

        try:
            handler(job)
            job.status = DeliveryJobStatus.COMPLETED
            self.idempotency_registry.update_status(
                job.idempotency_key, DeliveryJobStatus.COMPLETED
            )
            self._metrics.processed_count += 1
            OPERATIONAL_MONITOR.record_queue_job(
                "alert_dispatch", wait_seconds, remaining_depth, status="completed"
            )
            return job

        except Exception as exc:
            job.last_error = str(exc)
            logger.warning(
                f"[Queue] Teslimat hatası: job_id={job.job_id}, "
                f"attempt={job.attempts}/{job.max_attempts}, hata={exc}"
            )

            is_retryable = self.retry_policy.is_retryable(exc)

            if not is_retryable:
                reason = (
                    DeadLetterReason.SENSITIVE_DATA_VIOLATION
                    if isinstance(exc, SensitiveDataLeakError)
                    else DeadLetterReason.FATAL_NON_RETRYABLE_ERROR
                )
                self._send_to_dlq(job, reason, current_time)
                self._metrics.dead_letter_count += 1
                OPERATIONAL_MONITOR.record_queue_job(
                    "alert_dispatch", wait_seconds, remaining_depth, status="dead_letter"
                )
                return job

            if job.attempts < job.max_attempts:
                backoff_delay = self.retry_policy.calculate_delay(job.attempts + 1)
                job.next_attempt_at = current_time + timedelta(seconds=backoff_delay)
                job.status = DeliveryJobStatus.RETRYING
                with self._lock:
                    self._queue.append(job)
                    self._metrics.retry_count += 1
                    updated_depth = len(self._queue)
                self.idempotency_registry.update_status(
                    job.idempotency_key, DeliveryJobStatus.RETRYING
                )
                OPERATIONAL_MONITOR.record_queue_job(
                    "alert_dispatch", wait_seconds, updated_depth, status="retry"
                )
                return job
            else:
                self._send_to_dlq(job, DeadLetterReason.MAX_RETRIES_EXCEEDED, current_time)
                self._metrics.dead_letter_count += 1
                OPERATIONAL_MONITOR.record_queue_job(
                    "alert_dispatch", wait_seconds, remaining_depth, status="dead_letter"
                )
                return job

    def process_all(
        self,
        handler: Callable[[AlertDeliveryJob], Any],
        max_jobs: int = 100,
        now: datetime | None = None,
    ) -> list[AlertDeliveryJob]:
        """Kuyruktaki tüm hazır işleri belirlenen sınıra kadar sırayla işler."""
        processed: list[AlertDeliveryJob] = []
        count = 0
        while count < max_jobs:
            job = self.process_next(handler, now=now)
            if job is None:
                break
            processed.append(job)
            count += 1
        return processed

    def replay_dead_letter(
        self,
        job_id: str,
        now: datetime | None = None,
    ) -> AlertDeliveryJob | None:
        """DLQ'dan bir kaydı alıp sıfırlanmış deneme hakkıyla kuyruğa geri aktarır."""
        record = self.dead_letter_queue.remove(job_id)
        if not record:
            return None

        current_time = now or datetime.now(UTC)
        payload = AlertNotificationPayload(
            alert_id=str(record.payload_snapshot.get("alert_id", record.job_id)),
            symbol=record.symbol,
            rule_name=str(record.payload_snapshot.get("rule_name", "Teknik Trend Kurali")),
            condition_summary=str(
                record.payload_snapshot.get("condition_summary", "Teknik kosul saglandi")
            ),
            data_timestamp_utc=datetime.fromisoformat(
                str(record.payload_snapshot.get("data_timestamp_utc", current_time.isoformat()))
            ),
            data_source=str(record.payload_snapshot.get("data_source", "replayed")),
            recipient_email=record.recipient_email,
        )

        self.idempotency_registry.remove(record.idempotency_key)

        job, _ = self.enqueue(
            payload=payload,
            recipient_email=record.recipient_email,
            idempotency_key=record.idempotency_key,
            custom_job_id=record.job_id,
            now=current_time,
        )
        return job

    def _send_to_dlq(
        self,
        job: AlertDeliveryJob,
        reason: DeadLetterReason,
        current_time: datetime,
    ) -> None:
        """İşi dead-letter kuyruğuna kaydeder."""
        job.status = DeliveryJobStatus.DEAD_LETTERED
        job.dead_letter_reason = reason
        self.idempotency_registry.update_status(
            job.idempotency_key, DeliveryJobStatus.DEAD_LETTERED
        )

        dlq_record = DeadLetterRecord(
            job_id=job.job_id,
            idempotency_key=job.idempotency_key,
            symbol=job.payload.symbol,
            recipient_email=job.recipient_email,
            failure_timestamp=current_time,
            reason=reason,
            error_message=job.last_error or "Unknown failure",
            total_attempts=job.attempts,
            payload_snapshot={
                "alert_id": job.payload.alert_id,
                "rule_name": job.payload.rule_name,
                "condition_summary": job.payload.condition_summary,
                "data_timestamp_utc": job.payload.data_timestamp_utc.isoformat(),
                "data_source": job.payload.data_source,
                "recipient_email": job.payload.recipient_email,
            },
        )
        self.dead_letter_queue.push(dlq_record)
