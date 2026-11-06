"""Test suite for live market data provider contracts and entitlement verification."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.providers.live_contract import (
    LIVE_DATA_CONTRACT_AUDITOR,
    ContractStatus,
    ContractTier,
    LiveDataContractAuditor,
    LiveDataEntitlementError,
    ProviderContractMetadata,
    RedistributionScope,
    create_default_contract_auditor,
)


@pytest.fixture
def app_client() -> TestClient:
    """Fixture providing initialized FastAPI test client."""
    app = create_app()
    return TestClient(app)


def test_default_contract_auditor_bootstrapped() -> None:
    """Test that default auditor comes pre-configured with licensed BIST and sandbox contracts."""
    contracts = LIVE_DATA_CONTRACT_AUDITOR.list_contracts()
    assert len(contracts) >= 2

    matriks = LIVE_DATA_CONTRACT_AUDITOR.get_contract("matriks_bist_primary")
    assert matriks is not None
    assert matriks.contract_id == "BIST-VND-2026-TR-881"
    assert matriks.vendor_license_number == "BIST-DIST-2026/044"
    assert ContractTier.BIST_EQUITY_DELAYED_15MIN in matriks.feed_tiers
    assert ContractTier.BIST_INDEX_EOD in matriks.feed_tiers
    assert matriks.verify_signature() is True

    sandbox = LIVE_DATA_CONTRACT_AUDITOR.get_contract("synthetic")
    assert sandbox is not None
    assert ContractTier.SYNTHETIC_SANDBOX in sandbox.feed_tiers
    assert sandbox.verify_signature() is True


def test_entitlement_success_delayed_feed() -> None:
    """Verify that primary BIST feed passes entitlement check for delayed data."""
    auditor = create_default_contract_auditor()
    result = auditor.verify_entitlement(
        provider_name="matriks_bist_primary",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
        scope=RedistributionScope.DISPLAY,
        disclaimer_acknowledged=True,
    )
    assert result.is_authorized is True
    assert result.status == ContractStatus.VALID
    assert result.contract_id == "BIST-VND-2026-TR-881"
    assert len(result.errors) == 0
    assert result.days_until_expiration > 0


def test_entitlement_fails_for_unauthorized_tier() -> None:
    """Verify that requesting an unauthorized tier (real-time) on delayed-only contract fails."""
    auditor = create_default_contract_auditor()
    result = auditor.verify_entitlement(
        provider_name="matriks_bist_primary",
        requested_tier=ContractTier.BIST_EQUITY_REALTIME,
        scope=RedistributionScope.DISPLAY,
        disclaimer_acknowledged=True,
    )
    assert result.is_authorized is False
    assert result.status == ContractStatus.UNAUTHORIZED_FEED
    assert any("yetkilendirilmemiştir" in err for err in result.errors)


def test_entitlement_fails_missing_disclaimer() -> None:
    """Verify that unacknowledged disclaimer blocks data delivery."""
    auditor = create_default_contract_auditor()
    result = auditor.verify_entitlement(
        provider_name="matriks_bist_primary",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
        scope=RedistributionScope.DISPLAY,
        disclaimer_acknowledged=False,
    )
    assert result.is_authorized is False
    assert result.status == ContractStatus.MISSING_DISCLAIMER
    assert any("yasal uyarı" in err for err in result.errors)


def test_entitlement_fails_for_expired_contract() -> None:
    """Verify that an expired provider contract fails validation."""
    auditor = LiveDataContractAuditor()
    expired_contract = ProviderContractMetadata(
        contract_id="OLD-BIST-2024",
        provider_name="legacy_feed",
        vendor_license_number="BIST-DIST-2024/001",
        feed_tiers=[ContractTier.BIST_EQUITY_DELAYED_15MIN],
        redistribution_scopes=[RedistributionScope.DISPLAY],
        effective_date=date(2024, 1, 1),
        expiration_date=date(2024, 12, 31),
        commercial_use_approved=True,
        disclaimer_required=True,
    )
    expired_contract.signature_hash = expired_contract.compute_signature_hash()
    auditor.register_contract(expired_contract)

    result = auditor.verify_entitlement(
        provider_name="legacy_feed",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
        as_of_date=date(2026, 1, 1),
    )
    assert result.is_authorized is False
    assert result.status == ContractStatus.EXPIRED
    assert any("süresi dolmuştur" in err for err in result.errors)


def test_entitlement_fails_for_revoked_contract() -> None:
    """Verify that a revoked provider contract immediately fails."""
    auditor = LiveDataContractAuditor()
    contract = ProviderContractMetadata(
        contract_id="TERMINATED-2026",
        provider_name="bad_actor",
        vendor_license_number="REVOKED-001",
        feed_tiers=[ContractTier.BIST_EQUITY_DELAYED_15MIN],
        redistribution_scopes=[RedistributionScope.DISPLAY],
        effective_date=date(2026, 1, 1),
        expiration_date=date(2027, 12, 31),
        is_revoked=True,
    )
    contract.signature_hash = contract.compute_signature_hash()
    auditor.register_contract(contract)

    result = auditor.verify_entitlement(
        provider_name="bad_actor",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
    )
    assert result.is_authorized is False
    assert result.status == ContractStatus.REVOKED


def test_entitlement_fails_for_tampered_signature() -> None:
    """Verify that modifying contract metadata breaks signature check."""
    auditor = LiveDataContractAuditor()
    contract = ProviderContractMetadata(
        contract_id="TAMPER-TEST",
        provider_name="tampered_provider",
        vendor_license_number="LIC-123",
        feed_tiers=[ContractTier.BIST_EQUITY_DELAYED_15MIN],
        redistribution_scopes=[RedistributionScope.DISPLAY],
        effective_date=date(2026, 1, 1),
        expiration_date=date(2027, 12, 31),
        signature_hash="invalid_hash_signature",
    )
    auditor.register_contract(contract)

    result = auditor.verify_entitlement(
        provider_name="tampered_provider",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
    )
    assert result.is_authorized is False
    assert result.status == ContractStatus.SIGNATURE_MISMATCH


def test_assert_entitled_raises_exception() -> None:
    """Test that assert_entitled raises LiveDataEntitlementError on failure."""
    auditor = create_default_contract_auditor()
    with pytest.raises(LiveDataEntitlementError) as exc_info:
        auditor.assert_entitled(
            provider_name="matriks_bist_primary",
            requested_tier=ContractTier.BIST_EQUITY_REALTIME,
        )
    assert "UNAUTHORIZED_FEED" in str(exc_info.value)


def test_unregistered_provider_fails() -> None:
    """Test that querying an unregistered provider returns UNREGISTERED."""
    auditor = create_default_contract_auditor()
    result = auditor.verify_entitlement(
        provider_name="non_existent_provider",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
    )
    assert result.is_authorized is False
    assert result.status == ContractStatus.UNREGISTERED


def test_prometheus_metrics_emitted() -> None:
    """Test that entitlement verification records Prometheus metrics."""
    auditor = create_default_contract_auditor()
    auditor.verify_entitlement(
        provider_name="matriks_bist_primary",
        requested_tier=ContractTier.BIST_EQUITY_DELAYED_15MIN,
    )
    metrics_text = REGISTRY.generate_prometheus_text()
    assert "live_data_contract_status_gauge" in metrics_text
    assert "live_data_contract_expiry_days_gauge" in metrics_text
    assert "live_data_entitlement_checks_total" in metrics_text


def test_api_get_contracts(app_client: TestClient) -> None:
    """Test REST API endpoint GET /api/v1/market/contracts."""
    resp = app_client.get("/api/v1/market/contracts")
    assert resp.status_code == 200
    contracts = resp.json()
    assert isinstance(contracts, list)
    assert len(contracts) >= 2
    contract_ids = [c["contract_id"] for c in contracts]
    assert "BIST-VND-2026-TR-881" in contract_ids


def test_api_verify_contract_endpoint_success(app_client: TestClient) -> None:
    """Test REST API endpoint POST /api/v1/market/contracts/verify success case."""
    payload = {
        "provider_name": "matriks_bist_primary",
        "requested_tier": "BIST_EQUITY_DELAYED_15MIN",
        "scope": "DISPLAY",
        "disclaimer_acknowledged": True,
    }
    resp = app_client.post("/api/v1/market/contracts/verify", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_authorized"] is True
    assert data["status"] == "VALID"
    assert data["contract_id"] == "BIST-VND-2026-TR-881"


def test_api_verify_contract_endpoint_invalid_tier(app_client: TestClient) -> None:
    """Test REST API endpoint POST /api/v1/market/contracts/verify with invalid tier string."""
    payload = {
        "provider_name": "matriks_bist_primary",
        "requested_tier": "NON_EXISTENT_TIER_INVALID",
        "scope": "DISPLAY",
    }
    resp = app_client.post("/api/v1/market/contracts/verify", json=payload)
    assert resp.status_code == 400
    err = resp.json()
    assert "code" in err["error"]
    assert err["error"]["code"] == "INVALID_FEED_TIER"
