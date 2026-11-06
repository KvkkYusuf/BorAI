"""Unit and integration test suite for Closed Beta & Phased Rollout Gatekeeper (FAZ-16).

Verifies:
1. Allowlist access control, user addition, removal, and restriction.
2. Invite token lifecycle (issuance, quota exhaustion, expiration, validation).
3. Statutory consent gating (mandatory SPK non-advice disclaimer and KVKK consent).
4. Deterministic cohort hashing (SHA-256 bucket 0..99 across stages).
5. Phased rollout progression (CLOSED_BETA -> CANARY_5 -> 25 -> 50 -> GA_100).
6. Automated circuit breaker tripping (>1% error rate threshold) and recovery.
7. ClosedBetaGatekeeper pre-deployment evaluation (6/6 checks PASS).
8. REST API endpoints (/api/v1/beta/status, /validate-invite, etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.security.closed_beta_gatekeeper import (
    CLOSED_BETA_MANAGER,
    BetaGateStatus,
    ClosedBetaGatekeeper,
    ClosedBetaRolloutManager,
    RolloutStage,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(create_app())


@pytest.fixture
def fresh_manager() -> ClosedBetaRolloutManager:
    """Provide an isolated rollout manager instance."""
    return ClosedBetaRolloutManager(salt="test-salt-abc-123", error_threshold_percent=1.0)


def test_allowlist_management(fresh_manager: ClosedBetaRolloutManager) -> None:
    """Test adding, checking, and removing users from closed beta allowlist."""
    user = "Investor@Example.COM"
    fresh_manager.add_to_allowlist(user)
    assert "investor@example.com" in fresh_manager.allowlist

    eligible, reason = fresh_manager.is_user_eligible(user)
    assert eligible is True
    assert "allowlist" in reason.lower()

    fresh_manager.remove_from_allowlist(user)
    assert "investor@example.com" not in fresh_manager.allowlist

    eligible_after, _ = fresh_manager.is_user_eligible(user)
    assert eligible_after is False


def test_invite_token_lifecycle(fresh_manager: ClosedBetaRolloutManager) -> None:
    """Test invite creation, validation, and usage limits."""
    code = "INVITE-2026-VIP"
    fresh_manager.issue_invite_code(code, max_uses=1, expires_in_days=7)

    # Valid check
    valid, msg = fresh_manager.validate_invite_code("invite-2026-vip")
    assert valid is True

    # Unknown code
    valid_fake, msg_fake = fresh_manager.validate_invite_code("NONEXISTENT-CODE")
    assert valid_fake is False
    assert "Geçersiz" in msg_fake

    # Redeem once
    redeem_ok, redeem_msg = fresh_manager.redeem_invite_code(
        code=code,
        user_identifier="vip_beta_user@example.com",
        agreed_to_terms=True,
        agreed_to_spk_disclaimer=True,
    )
    assert redeem_ok is True
    assert "vip_beta_user@example.com" in fresh_manager.allowlist

    # Second redemption attempt should fail (quota exceeded)
    redeem_again, again_msg = fresh_manager.redeem_invite_code(
        code=code,
        user_identifier="another_user@example.com",
        agreed_to_terms=True,
        agreed_to_spk_disclaimer=True,
    )
    assert redeem_again is False
    assert "maksimum kullanım" in again_msg


def test_statutory_consent_enforcement(fresh_manager: ClosedBetaRolloutManager) -> None:
    """Verify that redemption strictly requires both SPK disclaimer and KVKK consent."""
    code = "STATUTORY-TEST-001"
    fresh_manager.issue_invite_code(code, max_uses=5)

    # Missing KVKK terms
    ok1, msg1 = fresh_manager.redeem_invite_code(
        code=code,
        user_identifier="u1@example.com",
        agreed_to_terms=False,
        agreed_to_spk_disclaimer=True,
    )
    assert ok1 is False
    assert "KVKK" in msg1

    # Missing SPK investment advice disclaimer
    ok2, msg2 = fresh_manager.redeem_invite_code(
        code=code,
        user_identifier="u2@example.com",
        agreed_to_terms=True,
        agreed_to_spk_disclaimer=False,
    )
    assert ok2 is False
    assert "SPK" in msg2

    # Compliant submission
    ok3, msg3 = fresh_manager.redeem_invite_code(
        code=code,
        user_identifier="u3@example.com",
        agreed_to_terms=True,
        agreed_to_spk_disclaimer=True,
    )
    assert ok3 is True
    assert "u3@example.com" in fresh_manager.allowlist


def test_deterministic_cohort_hashing(fresh_manager: ClosedBetaRolloutManager) -> None:
    """Verify deterministic hash bucket assignment (0..99)."""
    user_id = "trader_999@borsa.local"
    bucket1 = fresh_manager.get_cohort_bucket(user_id)
    bucket2 = fresh_manager.get_cohort_bucket(user_id)
    assert bucket1 == bucket2
    assert 0 <= bucket1 < 100


def test_phased_rollout_ladder_progression(fresh_manager: ClosedBetaRolloutManager) -> None:
    """Test user eligibility across the 5 deployment ladder stages."""
    # Find two user identifiers: one with small bucket, one with large bucket
    user_small = None
    user_large = None
    for idx in range(100):
        uid = f"candidate_user_{idx}@test.org"
        b = fresh_manager.get_cohort_bucket(uid)
        if b < 5 and user_small is None:
            user_small = uid
        elif b >= 50 and user_large is None:
            user_large = uid
        if user_small and user_large:
            break

    assert user_small is not None
    assert user_large is not None

    # Stage 1: CLOSED_BETA
    fresh_manager.current_stage = RolloutStage.CLOSED_BETA
    el_s, _ = fresh_manager.is_user_eligible(user_small)
    el_l, _ = fresh_manager.is_user_eligible(user_large)
    assert el_s is False
    assert el_l is False

    # Stage 2: CANARY_5
    fresh_manager.advance_stage(RolloutStage.CANARY_5)
    el_s, _ = fresh_manager.is_user_eligible(user_small)
    el_l, _ = fresh_manager.is_user_eligible(user_large)
    assert el_s is True  # bucket < 5
    assert el_l is False  # bucket >= 50

    # Stage 3: CANARY_50
    fresh_manager.advance_stage(RolloutStage.CANARY_50)
    el_s, _ = fresh_manager.is_user_eligible(user_small)
    assert el_s is True

    # Stage 4: GA_100
    fresh_manager.advance_stage(RolloutStage.GA_100)
    el_s, _ = fresh_manager.is_user_eligible(user_small)
    el_l, _ = fresh_manager.is_user_eligible(user_large)
    assert el_s is True
    assert el_l is True


def test_circuit_breaker_trip_and_recovery(fresh_manager: ClosedBetaRolloutManager) -> None:
    """Test that error spike automatically trips circuit breaker and halts rollout."""
    fresh_manager.advance_stage(RolloutStage.GA_100)
    fresh_manager.add_to_allowlist("admin@ops.local")

    # Healthy telemetry (0.5% errors < 1.0% threshold)
    ok = fresh_manager.evaluate_error_rate_telemetry(total_requests=1000, error_requests=5)
    assert ok is True
    assert fresh_manager.circuit_breaker_tripped is False

    # Unhealthy telemetry (3.0% errors > 1.0% threshold)
    not_ok = fresh_manager.evaluate_error_rate_telemetry(total_requests=1000, error_requests=30)
    assert not_ok is False
    assert fresh_manager.circuit_breaker_tripped is True

    # General traffic blocked during circuit trip
    el_gen, _ = fresh_manager.is_user_eligible("regular_user@example.com")
    assert el_gen is False

    # Emergency allowlist still maintains access
    el_admin, _ = fresh_manager.is_user_eligible("admin@ops.local")
    assert el_admin is True

    # Cannot advance stage while tripped
    with pytest.raises(RuntimeError):
        fresh_manager.advance_stage(RolloutStage.CANARY_5)

    # Reset recovery
    fresh_manager.reset_circuit_breaker()
    assert fresh_manager.circuit_breaker_tripped is False
    el_gen_restored, _ = fresh_manager.is_user_eligible("regular_user@example.com")
    assert el_gen_restored is True


def test_gatekeeper_evaluate_gate_pass(tmp_path: Path) -> None:
    """Verify that ClosedBetaGatekeeper produces a clean PASS with 6/6 checks."""
    report_file = tmp_path / "CLOSED_BETA_VERIFICATION_REPORT.md"
    gatekeeper = ClosedBetaGatekeeper(report_output_path=report_file)

    report = gatekeeper.evaluate_gate(workspace_root=Path.cwd())
    assert report.gate_status == BetaGateStatus.PASS
    assert report.total_checks == 6
    assert report.passed_checks == 6
    assert report.failed_checks == 0
    assert report.sign_off_ready is True
    assert len(report.blocking_violations) == 0

    # Ensure report file was written to disk
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "ONAYLANDI (PASS)" in content
    assert "BETA-01-ALLOWLIST-ACCESS-CONTROL" in content
    assert "BETA-06-OPERATIONAL-DOCUMENTATION" in content


def test_beta_api_endpoints(client: TestClient) -> None:
    """Integration test for beta REST API routers."""
    # 1. Rollout status
    res_status = client.get("/api/v1/beta/status")
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert "current_stage" in status_data
    assert "stage_percentage" in status_data
    assert status_data["circuit_breaker_active"] is False

    # 2. Issue a test code on the global manager
    code = "API-TEST-CODE-99"
    CLOSED_BETA_MANAGER.issue_invite_code(code, max_uses=2)

    # 3. Validate code
    res_val = client.post("/api/v1/beta/validate-invite", json={"code": code})
    assert res_val.status_code == 200
    assert res_val.json()["is_valid"] is True

    # 4. Redeem code with missing legal consent (should fail 400)
    res_red_fail = client.post(
        "/api/v1/beta/redeem-invite",
        json={
            "code": code,
            "user_identifier": "apitester@example.com",
            "agreed_to_terms": False,
            "agreed_to_spk_disclaimer": True,
        },
    )
    assert res_red_fail.status_code == 400

    # 5. Redeem code with full statutory consent (should succeed 200)
    res_red_ok = client.post(
        "/api/v1/beta/redeem-invite",
        json={
            "code": code,
            "user_identifier": "apitester@example.com",
            "agreed_to_terms": True,
            "agreed_to_spk_disclaimer": True,
        },
    )
    assert res_red_ok.status_code == 200
    assert res_red_ok.json()["success"] is True

    # 6. Check eligibility
    res_elig = client.get("/api/v1/beta/check-eligibility?user_id=apitester@example.com")
    assert res_elig.status_code == 200
    assert res_elig.json()["eligible"] is True

    # 7. Pre-deployment verify endpoint
    res_verify = client.get("/api/v1/pre-deployment/beta-rollout/verify")
    assert res_verify.status_code == 200
    gate_data = res_verify.json()
    assert gate_data["gate_status"] == "PASS"
    assert gate_data["passed_checks"] == 6

    # 8. Pre-deployment markdown report endpoint
    res_report = client.get("/api/v1/pre-deployment/beta-rollout/report")
    assert res_report.status_code == 200
    assert "text/markdown" in res_report.headers["content-type"]
    assert "ONAYLANDI (PASS)" in res_report.text
