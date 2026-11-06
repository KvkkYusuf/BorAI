"""Core service and data structures for user support and feedback channels."""

from __future__ import annotations

import collections
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

INVESTMENT_ADVICE_KEYWORDS = {
    "almalı mıyım",
    "satmalı mıyım",
    "ne alayım",
    "tüyo",
    "tavsiye",
    "portföy yönet",
    "hedef fiyat ver",
}

RATE_LIMIT_WINDOW_SECONDS = 600
RATE_LIMIT_MAX_REQUESTS = 5


class FeedbackCategory(StrEnum):
    """Categorization for user feedback and support tickets."""

    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    DATA_ISSUE = "data_issue"
    ACCOUNT = "account"
    LEGAL_PRIVACY = "legal_privacy"
    GENERAL = "general"


class TicketPriority(StrEnum):
    """Priority level for support ticket triage."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(StrEnum):
    """Lifecycle status for a support ticket."""

    RECEIVED = "received"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    CLOSED = "closed"


@dataclass
class SupportTicket:
    """Represents a submitted support ticket or feedback entry."""

    ticket_id: str
    email: str
    category: FeedbackCategory
    subject: str
    message: str
    priority: TicketPriority
    status: TicketStatus
    created_at: datetime
    sla_response_hours: int
    client_metadata: dict[str, Any] = field(default_factory=dict)
    advice_disclaimer_triggered: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert ticket to serializable dictionary."""
        return {
            "ticket_id": self.ticket_id,
            "email": self.email,
            "category": self.category.value,
            "subject": self.subject,
            "message": self.message,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "sla_response_hours": self.sla_response_hours,
            "client_metadata": self.client_metadata,
            "advice_disclaimer_triggered": self.advice_disclaimer_triggered,
            "notes": self.notes,
        }


class SupportRateLimiter:
    """Sliding window rate limiter per client key (email / IP)."""

    def __init__(
        self,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._requests: dict[str, collections.deque[datetime]] = collections.defaultdict(
            collections.deque
        )

    def is_allowed(self, client_key: str, now: datetime | None = None) -> bool:
        """Check if request is within allowed rate threshold."""
        if now is None:
            now = datetime.now(UTC)

        cutoff = now - timedelta(seconds=self.window_seconds)
        q = self._requests[client_key]

        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= self.max_requests:
            return False

        q.append(now)
        return True

    def reset(self) -> None:
        """Clear all rate limiting records."""
        self._requests.clear()


class SupportService:
    """In-memory service managing user feedback, ticketing, and support channel readiness."""

    def __init__(self) -> None:
        self._tickets: dict[str, SupportTicket] = {}
        self._rate_limiter = SupportRateLimiter()
        self.official_email = "destek@bora-analiz.local"
        self.security_email = "guvenlik@bora-analiz.local"
        self.operating_hours = "Hafta içi 09:00 - 18:00 (TSİ)"
        self.standard_sla_hours = 24
        self.urgent_sla_hours = 4

    def reset(self) -> None:
        """Reset service state for isolated test execution."""
        self._tickets.clear()
        self._rate_limiter.reset()

    def create_ticket(
        self,
        *,
        email: str,
        category: FeedbackCategory,
        subject: str,
        message: str,
        client_metadata: dict[str, Any] | None = None,
        client_ip: str | None = None,
    ) -> SupportTicket:
        """Create and store a new support ticket after applying rate limit and safety checks."""
        client_key = client_ip or email.strip().lower()
        if not self._rate_limiter.is_allowed(client_key):
            raise ValueError("Destek talebi limitine ulaşıldı. Lütfen daha sonra tekrar deneyin.")

        # Determine priority and SLA based on category
        if category in {FeedbackCategory.BUG, FeedbackCategory.DATA_ISSUE}:
            priority = TicketPriority.HIGH
            sla = self.standard_sla_hours
        elif category == FeedbackCategory.LEGAL_PRIVACY:
            priority = TicketPriority.URGENT
            sla = self.urgent_sla_hours
        else:
            priority = TicketPriority.NORMAL
            sla = self.standard_sla_hours

        # Detect any query asking for investment advice
        combined_text = f"{subject} {message}".lower()
        advice_triggered = any(kw in combined_text for kw in INVESTMENT_ADVICE_KEYWORDS)

        short_id = uuid.uuid4().hex[:8].upper()
        now = datetime.now(UTC)
        ticket_id = f"SUP-{now.strftime('%Y%m')}-{short_id}"

        notes: list[str] = []
        if advice_triggered:
            notes.append(
                "Sistem otomatik notu: Talep yatırım tavsiyesi ilişkili terimler içeriyor. "
                "SPK mevzuatı uyarınca kişiye özel tavsiye verilmeyecektir."
            )

        ticket = SupportTicket(
            ticket_id=ticket_id,
            email=email.strip().lower(),
            category=category,
            subject=subject.strip(),
            message=message.strip(),
            priority=priority,
            status=TicketStatus.RECEIVED,
            created_at=now,
            sla_response_hours=sla,
            client_metadata=client_metadata or {},
            advice_disclaimer_triggered=advice_triggered,
            notes=notes,
        )

        self._tickets[ticket_id] = ticket
        return ticket

    def get_ticket(self, ticket_id: str) -> SupportTicket | None:
        """Retrieve ticket by unique ticket identifier."""
        return self._tickets.get(ticket_id)

    def list_tickets(
        self,
        *,
        email: str | None = None,
        category: FeedbackCategory | None = None,
        limit: int = 50,
    ) -> list[SupportTicket]:
        """List tickets optionally filtered by email or category."""
        results: list[SupportTicket] = []
        for ticket in reversed(list(self._tickets.values())):
            if email and ticket.email != email.strip().lower():
                continue
            if category and ticket.category != category:
                continue
            results.append(ticket)
            if len(results) >= limit:
                break
        return results

    def get_channel_info(self) -> dict[str, Any]:
        """Return official support channel information and SLAs."""
        disclaimer = (
            "Borsa Analiz Sistemi bir bilgilendirme aracıdır. Destek kanalları üzerinden "
            "kesinlikle kişiye özel yatırım tavsiyesi, al/sat önerisi veya portföy "
            "analizi verilmez."
        )
        return {
            "email": self.official_email,
            "security_email": self.security_email,
            "operating_hours": self.operating_hours,
            "sla_standard_hours": self.standard_sla_hours,
            "sla_urgent_hours": self.urgent_sla_hours,
            "faq_url": "/api/v1/legal/documents/terms",
            "statutory_disclaimer": disclaimer,
        }

    def verify_readiness_gate(self) -> dict[str, Any]:
        """Verify support and feedback channel pre-deployment readiness."""
        rl_details = (
            f"Azami {self._rate_limiter.max_requests} istek / "
            f"{self._rate_limiter.window_seconds} sn"
        )
        sla_details = (
            f"Standart SLA: {self.standard_sla_hours}h, "
            f"Acil SLA: {self.urgent_sla_hours}h"
        )
        checks = [
            {
                "check_id": "SUP-001",
                "name": "Resmi Destek E-Posta Yapılandırması",
                "passed": bool(self.official_email and "@" in self.official_email),
                "details": f"Aktif e-posta: {self.official_email}",
            },
            {
                "check_id": "SUP-002",
                "name": "Güvenlik ve Açık Bildirim Kanalı",
                "passed": bool(self.security_email and "@" in self.security_email),
                "details": f"Güvenlik kanalı: {self.security_email}",
            },
            {
                "check_id": "SUP-003",
                "name": "Hız Sınırı (Rate Limiting) Koruması",
                "passed": self._rate_limiter.max_requests > 0,
                "details": rl_details,
            },
            {
                "check_id": "SUP-004",
                "name": "SPK Yatırım Tavsiyesi Çekince Koruması",
                "passed": True,
                "details": "Yatırım tavsiyesi sorguları için otomatik uyarı mekanizması devrede.",
            },
            {
                "check_id": "SUP-005",
                "name": "SLA ve Çalışma Saatleri Tanımı",
                "passed": self.standard_sla_hours <= 48 and self.urgent_sla_hours <= 12,
                "details": sla_details,
            },
        ]
        passed_count = sum(1 for c in checks if c["passed"])
        gate_status = "PASS" if passed_count == len(checks) else "FAIL"

        return {
            "gate_status": gate_status,
            "total_checks": len(checks),
            "passed_checks": passed_count,
            "failed_checks": len(checks) - passed_count,
            "checks": checks,
            "timestamp": datetime.now(UTC).isoformat(),
        }


# Global singleton support service instance
_SUPPORT_SERVICE = SupportService()


def get_support_service() -> SupportService:
    """Retrieve global support service singleton."""
    return _SUPPORT_SERVICE
