"""Unit and integration test suite for User Support and Feedback Channel (FAZ-16).

Tests:
1. Public support channels, business hours, and statutory disclaimer verification.
2. Successful feedback / support ticket submission with 201 Created and SLA response assignment.
3. Ticket retrieval by unique reference ID.
4. Input validation: invalid email regex, too short message, invalid category.
5. Statutory investment advice detection and advisory warning attachment.
6. Sliding window rate limiting enforcement (HTTP 429 on abuse).
7. Non-existent ticket handling (HTTP 404).
8. Pre-deployment support channel readiness gate audit (PASS with 5/5 checks).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.support.feedback_service import (
    TicketPriority,
    TicketStatus,
    get_support_service,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def reset_support_state() -> None:
    """Reset support service repository and rate limiter before each test."""
    service = get_support_service()
    service.reset()


def test_support_channels_endpoint(client: TestClient) -> None:
    """Verify that official support channels and statutory disclaimer are publicly accessible."""
    response = client.get("/api/v1/support/channels")
    assert response.status_code == 200
    data = response.json()

    assert "destek@bora-analiz.local" in data["email"]
    assert "guvenlik@bora-analiz.local" in data["security_email"]
    assert data["sla_standard_hours"] == 24
    assert data["sla_urgent_hours"] == 4
    assert "tavsiye" in data["statutory_disclaimer"].lower()


def test_create_support_ticket_success(client: TestClient) -> None:
    """Test standard ticket creation and subsequent lookup by ticket ID."""
    payload = {
        "email": "investor@example.com",
        "category": "bug",
        "subject": "Grafik yüklenirken gecikme yaşanıyor",
        "message": "BIST100 grafiğinde son 15 dakikalık veriler geç güncelleniyor.",
        "client_metadata": {"browser": "Chrome", "version": "122.0", "os": "Windows"},
    }

    response = client.post("/api/v1/support/tickets", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["ticket_id"].startswith("SUP-")
    assert data["email"] == "investor@example.com"
    assert data["category"] == "bug"
    assert data["priority"] == TicketPriority.HIGH.value
    assert data["status"] == TicketStatus.RECEIVED.value
    assert data["sla_response_hours"] == 24
    assert data["advice_disclaimer_triggered"] is False

    # Lookup ticket by ID
    ticket_id = data["ticket_id"]
    get_res = client.get(f"/api/v1/support/tickets/{ticket_id}")
    assert get_res.status_code == 200
    assert get_res.json()["ticket_id"] == ticket_id


def test_create_support_ticket_validation_failures(client: TestClient) -> None:
    """Verify schema validation on invalid email, too short message, and invalid category."""
    # Invalid email
    res1 = client.post(
        "/api/v1/support/tickets",
        json={
            "email": "not-an-email",
            "category": "bug",
            "subject": "Geçerli başlık",
            "message": "Detaylı açıklama metni buradadır.",
        },
    )
    assert res1.status_code == 422

    # Message too short (< 10 chars)
    res2 = client.post(
        "/api/v1/support/tickets",
        json={
            "email": "valid@example.com",
            "category": "bug",
            "subject": "Geçerli başlık",
            "message": "Kısa",
        },
    )
    assert res2.status_code == 422

    # Invalid category
    res3 = client.post(
        "/api/v1/support/tickets",
        json={
            "email": "valid@example.com",
            "category": "invalid_category_type",
            "subject": "Geçerli başlık",
            "message": "Detaylı açıklama metni buradadır en az on karakter.",
        },
    )
    assert res3.status_code == 400
    assert "INVALID_CATEGORY" in res3.json()["error"]["code"]


def test_investment_advice_warning_detection(client: TestClient) -> None:
    """Verify that asking for personalized stock picks attaches SPK compliance warning."""
    payload = {
        "email": "trader@example.com",
        "category": "general",
        "subject": "THYAO hissesi almalı mıyım?",
        "message": "Gelecek hafta için portföy yönetimi ve hedef fiyat tavsiyesi alabilir miyim?",
    }

    response = client.post("/api/v1/support/tickets", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["advice_disclaimer_triggered"] is True
    assert any("yatırım tavsiyesi" in n.lower() for n in data["notes"])


def test_support_rate_limiting_enforcement(client: TestClient) -> None:
    """Verify that spamming requests from the same user triggers HTTP 429."""
    for i in range(5):
        res = client.post(
            "/api/v1/support/tickets",
            json={
                "email": "spammer@example.com",
                "category": "general",
                "subject": f"Destek sorusu #{i}",
                "message": "Bu bir test geri bildirim mesajıdır ve geçerlidir.",
            },
        )
        assert res.status_code == 201

    # 6th request should trigger rate limit 429
    res_blocked = client.post(
        "/api/v1/support/tickets",
        json={
            "email": "spammer@example.com",
            "category": "general",
            "subject": "Aşırı istek denemesi",
            "message": "Bu istek hız sınırına takılmalıdır.",
        },
    )
    assert res_blocked.status_code == 429
    assert "RATE_LIMIT_EXCEEDED" in res_blocked.json()["error"]["code"]


def test_ticket_not_found(client: TestClient) -> None:
    """Verify HTTP 404 for non-existent ticket identifier."""
    res = client.get("/api/v1/support/tickets/SUP-202699-UNKNOWN99")
    assert res.status_code == 404
    assert "TICKET_NOT_FOUND" in res.json()["error"]["code"]


def test_support_channel_readiness_gate(client: TestClient) -> None:
    """Verify pre-deployment readiness gate for support channels."""
    response = client.get("/api/v1/support/gate")
    assert response.status_code == 200
    gate = response.json()

    assert gate["gate_status"] == "PASS"
    assert gate["total_checks"] == 5
    assert gate["passed_checks"] == 5
    assert gate["failed_checks"] == 0
    assert len(gate["checks"]) == 5
