"""Contract testing suite for provider data models and API OpenAPI schemas (FAZ-14).

Verifies:
1. Provider Data Contracts:
   - MarketBarRecord required fields, price constraints, decimal precision.
   - Provider schema drift detection (missing mandatory fields, renamed fields, invalid status).
   - Corporate action schema contracts (splits, dividends).
   - Schema transition & reconciliation contracts (LiveSchemaTransitionValidator).

2. API & OpenAPI Schema Contracts:
   - OpenAPI 3.1.0 specification validity, info metadata, paths.
   - Critical response models contract adherence (Symbols, History, Forecasts, Preferences, Alerts).
   - Sensitive credential isolation contract (no password, hash, or secret exposure in schemas).
   - Error response contracts (HTTPValidationError standard format).

3. Breaking Changes Detection Engine (OpenAPI Diff Guard):
   - Removed endpoints detection (breaking).
   - Removed response fields detection (breaking).
   - Incompatible property type mutation detection (breaking).
   - Newly added mandatory request body fields detection (breaking).
   - Additive non-breaking changes tolerance (optional fields, new endpoints).

4. Versioning & Header Contracts:
   - /api/v1/version semantic versioning contract.
"""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from stock_analysis.api.app import create_app
from stock_analysis.api.schemas import (
    DISCLAIMER_TEXT,
    AlertCreateRequest,
)
from stock_analysis.api.versioning import API_SEMANTIC_VERSION, CURRENT_MAJOR_VERSION
from stock_analysis.pipeline.corporate_actions import (
    CorporateAction,
    CorporateActionType,
)
from stock_analysis.pipeline.schema_transition import (
    LiveSchemaTransitionValidator,
    SchemaDifferenceType,
    SchemaDiffSeverity,
    SchemaFieldMapping,
    SchemaTransitionAdapter,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord

# ==============================================================================
# Helper: OpenAPI Breaking Change Detection Engine
# ==============================================================================


class BreakingChangeSeverity:
    BREAKING = "BREAKING"
    NON_BREAKING = "NON_BREAKING"


class ContractDiffViolation:
    """Represents a detected contract change between two OpenAPI specs."""

    def __init__(
        self,
        change_type: str,
        path: str,
        description: str,
        severity: str = BreakingChangeSeverity.BREAKING,
    ) -> None:
        self.change_type = change_type
        self.path = path
        self.description = description
        self.severity = severity

    def __repr__(self) -> str:
        return f"[{self.severity}] {self.change_type} at {self.path}: {self.description}"


class OpenApiContractAuditor:
    """Compares baseline and current OpenAPI specifications to detect breaking changes."""

    @staticmethod
    def audit(
        baseline: dict[str, Any],
        current: dict[str, Any],
    ) -> list[ContractDiffViolation]:
        violations: list[ContractDiffViolation] = []

        # 1. Endpoint / Path deletions (BREAKING)
        baseline_paths = baseline.get("paths", {})
        current_paths = current.get("paths", {})

        for path, methods in baseline_paths.items():
            if path not in current_paths:
                violations.append(
                    ContractDiffViolation(
                        change_type="ENDPOINT_REMOVED",
                        path=path,
                        description=f"Endpoint '{path}' was removed from API specification",
                        severity=BreakingChangeSeverity.BREAKING,
                    )
                )
                continue

            # Method check (e.g. GET / POST)
            curr_methods = current_paths[path]
            for method in methods:
                if method.lower() not in [m.lower() for m in curr_methods]:
                    violations.append(
                        ContractDiffViolation(
                            change_type="HTTP_METHOD_REMOVED",
                            path=f"{path} [{method.upper()}]",
                            description=(
                                f"Method '{method.upper()}' on endpoint '{path}' was removed"
                            ),
                            severity=BreakingChangeSeverity.BREAKING,
                        )
                    )

        # 2. Schema Component deletions and mutations
        baseline_schemas = baseline.get("components", {}).get("schemas", {})
        current_schemas = current.get("components", {}).get("schemas", {})

        for schema_name, base_schema in baseline_schemas.items():
            if schema_name not in current_schemas:
                violations.append(
                    ContractDiffViolation(
                        change_type="SCHEMA_REMOVED",
                        path=f"components.schemas.{schema_name}",
                        description=f"Model schema '{schema_name}' was removed",
                        severity=BreakingChangeSeverity.BREAKING,
                    )
                )
                continue

            curr_schema = current_schemas[schema_name]
            base_props = base_schema.get("properties", {})
            curr_props = curr_schema.get("properties", {})

            base_required = set(base_schema.get("required", []))
            curr_required = set(curr_schema.get("required", []))

            # Check for removed properties from models
            for prop_name, prop_spec in base_props.items():
                if prop_name not in curr_props:
                    violations.append(
                        ContractDiffViolation(
                            change_type="PROPERTY_REMOVED",
                            path=f"components.schemas.{schema_name}.{prop_name}",
                            description=f"Property '{prop_name}' removed from '{schema_name}'",
                            severity=BreakingChangeSeverity.BREAKING,
                        )
                    )
                else:
                    # Check for incompatible type changes
                    base_type = prop_spec.get("type")
                    curr_type = curr_props[prop_name].get("type")
                    if base_type and curr_type and base_type != curr_type:
                        violations.append(
                            ContractDiffViolation(
                                change_type="PROPERTY_TYPE_CHANGED",
                                path=f"components.schemas.{schema_name}.{prop_name}",
                                description=(
                                    f"Type mutated from '{base_type}' to '{curr_type}' "
                                    f"for '{schema_name}.{prop_name}'"
                                ),
                                severity=BreakingChangeSeverity.BREAKING,
                            )
                        )

            # Check if new REQUIRED properties were added to request models
            new_required = curr_required - base_required
            for new_req in new_required:
                if new_req not in base_required and "Request" in schema_name:
                    violations.append(
                        ContractDiffViolation(
                            change_type="NEW_REQUIRED_REQUEST_FIELD",
                            path=f"components.schemas.{schema_name}.{new_req}",
                            description=(
                                f"Newly required property '{new_req}' added to '{schema_name}', "
                                "breaking backwards compatibility for existing callers"
                            ),
                            severity=BreakingChangeSeverity.BREAKING,
                        )
                    )

        return violations


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture(scope="module")
def openapi_spec() -> dict[str, Any]:
    """Generate and cache the application OpenAPI schema specification."""
    app = create_app()
    spec = app.openapi()
    assert isinstance(spec, dict)
    return spec


# ==============================================================================
# 1. Provider Data Contracts Tests
# ==============================================================================


@pytest.mark.contract
class TestProviderDataContracts:
    """Verifies market data provider contracts, data invariants, and schema transition."""

    def test_market_bar_record_valid_contract(self) -> None:
        """Complete and valid market bar record satisfies provider contract."""
        bar = MarketBarRecord(
            symbol="THYAO",
            trade_date=date(2026, 10, 24),
            raw_open=Decimal("300.00"),
            raw_high=Decimal("308.50"),
            raw_low=Decimal("298.00"),
            raw_close=Decimal("305.20"),
            adj_open=Decimal("300.00"),
            adj_high=Decimal("308.50"),
            adj_low=Decimal("298.00"),
            adj_close=Decimal("305.20"),
            volume=15_420_000,
            turnover_try=Decimal("4706184000.00"),
            currency="TRY",
            exchange="BIST",
            data_status=DataStatus.VERIFIED,
            source_name="licensed_eod_feed",
        )
        assert bar.symbol == "THYAO"
        assert bar.trade_date == date(2026, 10, 24)
        assert bar.raw_close == Decimal("305.20")
        assert bar.turnover_try == Decimal("4706184000.00")
        assert bar.data_status == DataStatus.VERIFIED
        assert bar.volume > 0

    def test_market_bar_record_price_invariants_rejected(self) -> None:
        """Provider bars violating mathematical OHLC rules must fail contract validation."""
        # Negative price
        with pytest.raises(ValidationError):
            MarketBarRecord(
                symbol="THYAO",
                trade_date=date(2026, 10, 24),
                raw_open=Decimal("-10.00"),
                raw_high=Decimal("300.00"),
                raw_low=Decimal("290.00"),
                raw_close=Decimal("295.00"),
                adj_open=Decimal("-10.00"),
                adj_high=Decimal("300.00"),
                adj_low=Decimal("290.00"),
                adj_close=Decimal("295.00"),
                volume=1000,
                turnover_try=Decimal("295000.00"),
                source_name="test_feed",
            )

        # High < Low violation
        with pytest.raises(ValidationError):
            MarketBarRecord(
                symbol="THYAO",
                trade_date=date(2026, 10, 24),
                raw_open=Decimal("300.00"),
                raw_high=Decimal("280.00"),  # invalid: High < Low
                raw_low=Decimal("290.00"),
                raw_close=Decimal("295.00"),
                adj_open=Decimal("300.00"),
                adj_high=Decimal("280.00"),
                adj_low=Decimal("290.00"),
                adj_close=Decimal("295.00"),
                volume=1000,
                turnover_try=Decimal("295000.00"),
                source_name="test_feed",
            )

    def test_market_bar_record_decimal_precision_preservation(self) -> None:
        """Provider bar prices must maintain Decimal precision without IEEE-754 drift."""
        bar = MarketBarRecord(
            symbol="KCHOL",
            trade_date=date(2026, 10, 24),
            raw_open=Decimal("175.1234"),
            raw_high=Decimal("180.9999"),
            raw_low=Decimal("174.0001"),
            raw_close=Decimal("178.5555"),
            adj_open=Decimal("175.1234"),
            adj_high=Decimal("180.9999"),
            adj_low=Decimal("174.0001"),
            adj_close=Decimal("178.5555"),
            volume=500_000,
            turnover_try=Decimal("89277750.00"),
            source_name="test_feed",
        )
        assert str(bar.raw_close) == "178.5555"
        assert bar.raw_close != 178.5555  # exact Decimal check

    def test_corporate_action_contract_validation(self) -> None:
        """CorporateAction contract validates required fields and types."""
        ca_split = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 5, 10),
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
        )
        assert ca_split.symbol == "THYAO"
        assert ca_split.action_type == CorporateActionType.SPLIT
        assert ca_split.ratio == Decimal("2.0")

        ca_dividend = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 6, 15),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("4.50"),
        )
        assert ca_dividend.symbol == "THYAO"
        assert ca_dividend.action_type == CorporateActionType.CASH_DIVIDEND
        assert ca_dividend.amount_try == Decimal("4.50")

    def test_provider_schema_drift_missing_mandatory_field(self) -> None:
        """LiveSchemaTransitionValidator flags missing mandatory fields as critical."""
        validator = LiveSchemaTransitionValidator()
        incomplete_cols = ["Date", "Symbol", "Open", "High"]  # Missing Low, Close, Volume

        report = validator.compare_schemas(
            dataset_name="bist_incoming_drifted_provider",
            source_columns=incomplete_cols,
        )

        assert report.is_transition_safe is False
        assert report.risk_score >= 0.5
        critical_types = [
            d.diff_type
            for d in report.differences
            if d.diff_type == SchemaDifferenceType.MISSING_REQUIRED_FIELD
        ]
        assert len(critical_types) >= 3

    def test_provider_schema_drift_renamed_or_unmapped_field(self) -> None:
        """Unexpected extra fields from providers are detected with INFO severity."""
        validator = LiveSchemaTransitionValidator()
        cols_with_extra = [
            "Date",
            "Symbol",
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "VendorExtraScore",
        ]

        report = validator.compare_schemas(
            dataset_name="vendor_extended_feed",
            source_columns=cols_with_extra,
        )

        unmapped = [
            d
            for d in report.differences
            if d.diff_type == SchemaDifferenceType.EXTRA_UNMAPPED_FIELD
        ]
        assert len(unmapped) == 1
        assert unmapped[0].field_name == "VendorExtraScore"
        assert unmapped[0].severity == SchemaDiffSeverity.INFO

    def test_schema_transition_adapter_tolerant_transformation(self) -> None:
        """SchemaTransitionAdapter tolerates mapped fields and safely converts valid rows."""
        adapter = SchemaTransitionAdapter(
            mapping=SchemaFieldMapping(),
        )

        source_row = {
            "Symbol": "THYAO",
            "Date": "2026-10-24",
            "Open": 300.0,
            "High": 305.0,
            "Low": 298.0,
            "Close": 304.5,
            "Volume": 100000,
        }

        record = adapter.adapt_record(source_row)
        assert record.symbol == "THYAO"
        assert record.trade_date == date(2026, 10, 24)
        assert record.raw_close == Decimal("304.5")
        assert record.volume == 100000
        assert record.turnover_try == Decimal("30450000.0")


# ==============================================================================
# 2. API & OpenAPI Schema Contracts Tests
# ==============================================================================


@pytest.mark.contract
class TestApiOpenApiSchemaContracts:
    """Verifies FastAPI OpenAPI 3.1.0 specification integrity, endpoints, and models."""

    def test_openapi_spec_version_and_metadata(self, openapi_spec: dict[str, Any]) -> None:
        """OpenAPI spec conforms to OpenAPI 3.1 and contains required service metadata."""
        assert "openapi" in openapi_spec
        assert openapi_spec["openapi"].startswith("3.1")
        assert openapi_spec["info"]["title"] == "Borsa Analiz Sistemi API"
        assert openapi_spec["info"]["version"] == API_SEMANTIC_VERSION
        assert "paths" in openapi_spec
        assert "components" in openapi_spec
        assert "schemas" in openapi_spec["components"]

    def test_mandatory_legal_disclaimer_in_openapi_description(
        self, openapi_spec: dict[str, Any]
    ) -> None:
        """Mandatory disclaimer text must be permanently present in the OpenAPI root description."""
        description = openapi_spec["info"]["description"]
        assert DISCLAIMER_TEXT in description
        assert "yatirim tavsiyesi" in description.lower()

    def test_critical_endpoints_exist_in_openapi_spec(self, openapi_spec: dict[str, Any]) -> None:
        """All critical business routes must be registered in the API paths."""
        paths = openapi_spec["paths"]
        expected_routes = [
            "/api/v1/market/symbols",
            "/api/v1/market/history/{symbol}",
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/user/preferences",
            "/api/v1/alerts/unsubscribe",
            "/api/v1/version",
        ]
        for route in expected_routes:
            assert route in paths, f"Mandatory route '{route}' missing from OpenAPI spec!"

    def test_symbols_response_schema_contract(self, openapi_spec: dict[str, Any]) -> None:
        """SymbolsResponse schema contract matches model properties and required attributes."""
        schemas = openapi_spec["components"]["schemas"]
        assert "SymbolsResponse" in schemas
        schema = schemas["SymbolsResponse"]

        props = schema["properties"]
        assert "universe" in props
        assert "count" in props
        assert "total" in props
        assert "symbols" in props
        assert "disclaimer" in props

        assert "SymbolItem" in schemas
        item_props = schemas["SymbolItem"]["properties"]
        assert "symbol" in item_props
        assert "title" in item_props
        assert "indices" in item_props

    def test_history_response_schema_contract(self, openapi_spec: dict[str, Any]) -> None:
        """HistoryResponse and CandleItem schemas maintain strict contract."""
        schemas = openapi_spec["components"]["schemas"]
        assert "HistoryResponse" in schemas
        props = schemas["HistoryResponse"]["properties"]

        assert "symbol" in props
        assert "currency" in props
        assert "price_type" in props
        assert "candles" in props
        assert "disclaimer" in props

        assert "CandleItem" in schemas
        candle_props = schemas["CandleItem"]["properties"]
        assert "date" in candle_props
        assert "open" in candle_props
        assert "high" in candle_props
        assert "low" in candle_props
        assert "close" in candle_props
        assert "volume" in candle_props

    def test_user_preferences_schema_contract(self, openapi_spec: dict[str, Any]) -> None:
        """UserPreferencesResponse schema adheres to localization and UX settings contract."""
        schemas = openapi_spec["components"]["schemas"]
        assert "UserPreferencesResponse" in schemas
        props = schemas["UserPreferencesResponse"]["properties"]

        assert "theme" in props
        assert "language" in props
        assert "timezone" in props
        assert "email_notifications" in props
        assert "alert_frequency" in props

    def test_security_schema_contract_no_password_or_token_leaks(
        self, openapi_spec: dict[str, Any]
    ) -> None:
        """No response model in OpenAPI components leaks passwords, hashes, salts, or secrets."""
        schemas = openapi_spec["components"]["schemas"]
        forbidden_substrings = [
            "password_hash",
            "hashed_password",
            "salt",
            "totp_secret",
            "raw_secret",
        ]

        for name, schema in schemas.items():
            if name.endswith("Response") or name.endswith("Item"):
                props = schema.get("properties", {})
                for prop_name in props:
                    for forbidden in forbidden_substrings:
                        assert forbidden not in prop_name.lower(), (
                            f"Security: Schema '{name}' exposes sensitive attribute '{prop_name}'"
                        )

    def test_error_response_schema_contract(self, openapi_spec: dict[str, Any]) -> None:
        """Standard HTTPValidationError schema conforms to FastAPI error contract."""
        schemas = openapi_spec["components"]["schemas"]
        assert "HTTPValidationError" in schemas
        assert "ValidationError" in schemas

        validation_error_props = schemas["ValidationError"]["properties"]
        assert "loc" in validation_error_props
        assert "msg" in validation_error_props
        assert "type" in validation_error_props


# ==============================================================================
# 3. Breaking Changes Detection Engine Tests (OpenAPI Diff Guard)
# ==============================================================================


@pytest.mark.contract
class TestApiBreakingChangeDetector:
    """Verifies OpenApiContractAuditor detects breaking changes and permits additive changes."""

    @pytest.fixture
    def baseline_spec(self) -> dict[str, Any]:
        """Provides an immutable snapshot of baseline OpenAPI spec for comparison."""
        app = create_app()
        return copy.deepcopy(app.openapi())

    def test_breaking_change_detected_on_removed_endpoint(
        self, baseline_spec: dict[str, Any]
    ) -> None:
        """Removing an existing endpoint is flagged as a BREAKING contract violation."""
        mutated_spec = copy.deepcopy(baseline_spec)
        del mutated_spec["paths"]["/api/v1/market/symbols"]

        violations = OpenApiContractAuditor.audit(baseline_spec, mutated_spec)
        assert len(violations) >= 1
        assert any(
            v.change_type == "ENDPOINT_REMOVED"
            and v.path == "/api/v1/market/symbols"
            and v.severity == BreakingChangeSeverity.BREAKING
            for v in violations
        )

    def test_breaking_change_detected_on_removed_response_property(
        self, baseline_spec: dict[str, Any]
    ) -> None:
        """Removing a property from a response model is flagged as a BREAKING contract violation."""
        mutated_spec = copy.deepcopy(baseline_spec)
        del mutated_spec["components"]["schemas"]["SymbolsResponse"]["properties"]["symbols"]

        violations = OpenApiContractAuditor.audit(baseline_spec, mutated_spec)
        assert len(violations) >= 1
        assert any(
            v.change_type == "PROPERTY_REMOVED" and "SymbolsResponse.symbols" in v.path
            for v in violations
        )

    def test_breaking_change_detected_on_incompatible_type_change(
        self, baseline_spec: dict[str, Any]
    ) -> None:
        """Mutating property type (e.g. integer count to string) is flagged as BREAKING."""
        mutated_spec = copy.deepcopy(baseline_spec)
        mutated_spec["components"]["schemas"]["SymbolsResponse"]["properties"]["count"]["type"] = (
            "string"
        )

        violations = OpenApiContractAuditor.audit(baseline_spec, mutated_spec)
        assert len(violations) >= 1
        assert any(
            v.change_type == "PROPERTY_TYPE_CHANGED" and "SymbolsResponse.count" in v.path
            for v in violations
        )

    def test_breaking_change_detected_on_new_required_request_field(
        self, baseline_spec: dict[str, Any]
    ) -> None:
        """Adding a new required field to a Request model is flagged as BREAKING."""
        mutated_spec = copy.deepcopy(baseline_spec)
        login_schema = mutated_spec["components"]["schemas"]["LoginRequest"]
        login_schema["properties"]["mandatory_fingerprint"] = {"type": "string"}
        login_schema["required"] = login_schema.get("required", []) + ["mandatory_fingerprint"]

        violations = OpenApiContractAuditor.audit(baseline_spec, mutated_spec)
        assert len(violations) >= 1
        assert any(
            v.change_type == "NEW_REQUIRED_REQUEST_FIELD"
            and "LoginRequest.mandatory_fingerprint" in v.path
            for v in violations
        )

    def test_non_breaking_changes_permitted(self, baseline_spec: dict[str, Any]) -> None:
        """Adding a new optional field or a new endpoint produces zero BREAKING violations."""
        mutated_spec = copy.deepcopy(baseline_spec)

        # 1. Add new optional field to SymbolsResponse
        mutated_spec["components"]["schemas"]["SymbolsResponse"]["properties"]["server_load"] = {
            "type": "string",
            "description": "Optional telemetry header",
        }

        # 2. Add new route
        mutated_spec["paths"]["/api/v1/market/top-gainers"] = {
            "get": {
                "summary": "Non-breaking new route",
                "responses": {"200": {"description": "OK"}},
            }
        }

        violations = OpenApiContractAuditor.audit(baseline_spec, mutated_spec)
        assert len(violations) == 0, (
            f"Expected 0 violations for non-breaking additions, got: {violations}"
        )

    def test_current_application_satisfies_own_baseline(
        self, baseline_spec: dict[str, Any]
    ) -> None:
        """Comparing current API spec against itself produces zero violations."""
        app = create_app()
        current_spec = app.openapi()
        violations = OpenApiContractAuditor.audit(baseline_spec, current_spec)
        assert len(violations) == 0


# ==============================================================================
# 4. Versioning and Alert Rules Contract Tests
# ==============================================================================


@pytest.mark.contract
class TestApiVersioningContract:
    """Verifies API semantic versioning and compatibility headers contract."""

    def test_api_version_spec_contract(self) -> None:
        """Verify API version metadata contract."""
        assert API_SEMANTIC_VERSION == "1.0.0"
        assert CURRENT_MAJOR_VERSION == "v1"

    def test_alert_rule_pydantic_contract(self) -> None:
        """Verify AlertCreateRequest contract accepts valid inputs and rejects invalid ones."""
        # Valid request
        req = AlertCreateRequest(
            symbol="THYAO",
            condition_type="PRICE_ABOVE",
            target_value=320.50,
            channel="EMAIL",
        )
        assert req.symbol == "THYAO"
        assert req.target_value == 320.50
        assert req.channel == "EMAIL"

        # Missing symbol rejected
        with pytest.raises(ValidationError):
            AlertCreateRequest.model_validate(
                {"condition_type": "PRICE_ABOVE", "target_value": 300.0}
            )

        # Empty symbol rejected (min_length=1)
        with pytest.raises(ValidationError):
            AlertCreateRequest(
                symbol="",
                condition_type="PRICE_ABOVE",
                target_value=300.0,
            )

        # Non-numeric target_value rejected
        with pytest.raises(ValidationError):
            AlertCreateRequest.model_validate(
                {
                    "symbol": "THYAO",
                    "condition_type": "PRICE_ABOVE",
                    "target_value": "not-a-number",
                }
            )
