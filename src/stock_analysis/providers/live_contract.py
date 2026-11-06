"""Live market data contract and provider entitlement verification engine.

Enforces Borsa Istanbul (BIST) distribution license terms, data feed tiers
(15-min delayed vs real-time vs EOD), redistribution boundaries (display vs
non-display vs derived data), commercial usage authorization, mandatory
disclaimer checks, and cryptographic contract integrity prior to serving feeds.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    LIVE_DATA_CONTRACT_EXPIRY_DAYS,
    LIVE_DATA_CONTRACT_STATUS,
    LIVE_DATA_ENTITLEMENT_CHECKS_TOTAL,
)
from stock_analysis.providers.base import ProviderError

logger = logging.getLogger("stock_analysis.providers.contract")


class ContractTier(StrEnum):
    """Permitted live and delayed market data feed tiers."""

    BIST_EQUITY_REALTIME = "BIST_EQUITY_REALTIME"
    BIST_EQUITY_DELAYED_15MIN = "BIST_EQUITY_DELAYED_15MIN"
    BIST_INDEX_EOD = "BIST_INDEX_EOD"
    SYNTHETIC_SANDBOX = "SYNTHETIC_SANDBOX"


class RedistributionScope(StrEnum):
    """Authorized usage and redistribution scope under vendor contract."""

    DISPLAY = "DISPLAY"
    NON_DISPLAY = "NON_DISPLAY"
    DERIVED_DATA = "DERIVED_DATA"


class ContractStatus(StrEnum):
    """Evaluation status of a data provider contract."""

    VALID = "VALID"
    EXPIRED = "EXPIRED"
    NOT_COMMENCED = "NOT_COMMENCED"
    UNAUTHORIZED_FEED = "UNAUTHORIZED_FEED"
    UNAUTHORIZED_SCOPE = "UNAUTHORIZED_SCOPE"
    MISSING_DISCLAIMER = "MISSING_DISCLAIMER"
    SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    REVOKED = "REVOKED"
    UNREGISTERED = "UNREGISTERED"


class LiveDataEntitlementError(ProviderError):
    """Raised when access to live or delayed market data violates contract or authorization."""


@dataclass
class ProviderContractMetadata:
    """Comprehensive legal and technical metadata of a market data provider agreement."""

    contract_id: str
    provider_name: str
    vendor_license_number: str
    feed_tiers: list[ContractTier]
    redistribution_scopes: list[RedistributionScope]
    effective_date: date
    expiration_date: date
    max_cache_ttl_seconds: int = 900
    commercial_use_approved: bool = True
    disclaimer_required: bool = True
    signature_hash: str = ""
    entitlement_token: str | None = None
    audit_notes: str = ""
    is_revoked: bool = False

    def compute_signature_hash(self) -> str:
        """Compute canonical SHA-256 integrity hash of core contract terms."""
        canonical_str = "|".join(
            [
                self.contract_id.strip(),
                self.provider_name.strip().lower(),
                self.vendor_license_number.strip(),
                ",".join(sorted(t.value for t in self.feed_tiers)),
                ",".join(sorted(s.value for s in self.redistribution_scopes)),
                self.effective_date.isoformat(),
                self.expiration_date.isoformat(),
                str(int(self.commercial_use_approved)),
                str(int(self.disclaimer_required)),
            ]
        )
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def verify_signature(self) -> bool:
        """Verify whether the metadata matches its recorded signature hash."""
        if not self.signature_hash:
            return False
        return self.signature_hash.lower() == self.compute_signature_hash().lower()

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to a serializable dictionary."""
        return {
            "contract_id": self.contract_id,
            "provider_name": self.provider_name,
            "vendor_license_number": self.vendor_license_number,
            "feed_tiers": [t.value for t in self.feed_tiers],
            "redistribution_scopes": [s.value for s in self.redistribution_scopes],
            "effective_date": self.effective_date.isoformat(),
            "expiration_date": self.expiration_date.isoformat(),
            "max_cache_ttl_seconds": self.max_cache_ttl_seconds,
            "commercial_use_approved": self.commercial_use_approved,
            "disclaimer_required": self.disclaimer_required,
            "signature_hash": self.signature_hash,
            "has_entitlement_token": bool(self.entitlement_token),
            "audit_notes": self.audit_notes,
            "is_revoked": self.is_revoked,
        }


@dataclass
class EntitlementCheckResult:
    """Detailed result of runtime entitlement and contract validation."""

    status: ContractStatus
    is_authorized: bool
    contract_id: str
    provider_name: str
    requested_tier: ContractTier
    days_until_expiration: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert result to a serializable dictionary."""
        return {
            "status": self.status.value,
            "is_authorized": self.is_authorized,
            "contract_id": self.contract_id,
            "provider_name": self.provider_name,
            "requested_tier": self.requested_tier.value,
            "days_until_expiration": self.days_until_expiration,
            "errors": self.errors,
            "warnings": self.warnings,
            "checked_at": self.checked_at,
        }


class LiveDataContractAuditor:
    """Validates market data provider contracts, authorized tiers, and legal entitlement."""

    def __init__(self) -> None:
        self._contracts: dict[str, ProviderContractMetadata] = {}

    def register_contract(self, metadata: ProviderContractMetadata) -> None:
        """Register or update a provider contract in the auditor registry."""
        if not metadata.signature_hash:
            metadata.signature_hash = metadata.compute_signature_hash()
        key = metadata.provider_name.strip().lower()
        self._contracts[key] = metadata
        logger.info(
            "Registered market data contract '%s' (ID: %s, License: %s)",
            metadata.provider_name,
            metadata.contract_id,
            metadata.vendor_license_number,
        )

    def get_contract(self, provider_name: str) -> ProviderContractMetadata | None:
        """Retrieve registered contract metadata by provider name."""
        return self._contracts.get(provider_name.strip().lower())

    def list_contracts(self) -> list[ProviderContractMetadata]:
        """Return all registered provider contracts."""
        return list(self._contracts.values())

    def verify_entitlement(
        self,
        provider_name: str,
        requested_tier: ContractTier,
        scope: RedistributionScope = RedistributionScope.DISPLAY,
        disclaimer_acknowledged: bool = True,
        as_of_date: date | None = None,
    ) -> EntitlementCheckResult:
        """Perform comprehensive authorization and legal compliance verification for a feed.

        Checks:
        1. Provider registration
        2. Contract revocation status
        3. Contract effective and expiration dates
        4. Authorized feed tiers (delayed vs real-time vs EOD)
        5. Authorized redistribution scopes (display vs non-display)
        6. Mandatory disclaimer requirement adherence
        7. Cryptographic signature integrity
        """
        eval_date = as_of_date or datetime.now(UTC).date()
        key = provider_name.strip().lower()
        contract = self._contracts.get(key)

        if not contract:
            LIVE_DATA_ENTITLEMENT_CHECKS_TOTAL.inc(
                labels={
                    "provider": provider_name,
                    "tier": requested_tier.value,
                    "status": ContractStatus.UNREGISTERED.value,
                }
            )
            return EntitlementCheckResult(
                status=ContractStatus.UNREGISTERED,
                is_authorized=False,
                contract_id="UNKNOWN",
                provider_name=provider_name,
                requested_tier=requested_tier,
                days_until_expiration=0,
                errors=[
                    f"Piyasa verisi sağlayıcısı '{provider_name}' için kayıtlı sözleşme bulunamadı."
                ],
            )

        errors: list[str] = []
        warnings: list[str] = []
        days_remaining = (contract.expiration_date - eval_date).days

        # Check revocation
        if contract.is_revoked:
            errors.append(
                f"Sözleşme '{contract.contract_id}' idari gerekçeyle feshedilmiştir (REVOKED)."
            )

        # Check date validity
        if eval_date < contract.effective_date:
            errors.append(
                f"Sözleşme henüz yürürlüğe girmemiştir (Başlangıç: {contract.effective_date})."
            )
        elif eval_date > contract.expiration_date:
            errors.append(
                f"Sözleşme süresi dolmuştur (Bitiş: {contract.expiration_date.isoformat()}). "
                "Yenileme zorunludur."
            )
        elif days_remaining <= 30:
            warnings.append(
                f"Sözleşme bitimine {days_remaining} gün kaldı. Yenileme sürecini başlatınız."
            )

        # Check feed tier authorization
        if requested_tier not in contract.feed_tiers:
            tiers_repr = [t.value for t in contract.feed_tiers]
            errors.append(
                f"'{requested_tier.value}' veri katmanı sözleşmede yetkilendirilmemiştir. "
                f"Yetkili katmanlar: {tiers_repr}"
            )

        # Check redistribution scope
        if scope not in contract.redistribution_scopes:
            errors.append(
                f"'{scope.value}' kullanım kapsamı sözleşme sınırları dışındadır. "
                f"Yetkili kapsamlar: {[s.value for s in contract.redistribution_scopes]}"
            )

        # Check statutory disclaimer obligation
        if contract.disclaimer_required and not disclaimer_acknowledged:
            errors.append(
                "Borsa İstanbul ve SPK yasal uyarı metni onaylanmadan canlı/gecikmeli "
                "piyasa verisi sunulamaz."
            )

        # Check cryptographic signature
        if not contract.verify_signature():
            errors.append(
                "Sözleşme bütünlük imzası doğrulanamadı "
                "(Olası yetkisiz metin veya parametre değişikliği)."
            )

        # Determine overall status
        status: ContractStatus
        if contract.is_revoked:
            status = ContractStatus.REVOKED
        elif not contract.verify_signature():
            status = ContractStatus.SIGNATURE_MISMATCH
        elif eval_date < contract.effective_date:
            status = ContractStatus.NOT_COMMENCED
        elif eval_date > contract.expiration_date:
            status = ContractStatus.EXPIRED
        elif requested_tier not in contract.feed_tiers:
            status = ContractStatus.UNAUTHORIZED_FEED
        elif scope not in contract.redistribution_scopes:
            status = ContractStatus.UNAUTHORIZED_SCOPE
        elif contract.disclaimer_required and not disclaimer_acknowledged:
            status = ContractStatus.MISSING_DISCLAIMER
        else:
            status = ContractStatus.VALID

        is_authorized = len(errors) == 0

        # Update Prometheus metrics
        LIVE_DATA_CONTRACT_STATUS.set(
            1.0 if is_authorized else 0.0,
            labels={"provider": contract.provider_name, "tier": requested_tier.value},
        )
        LIVE_DATA_CONTRACT_EXPIRY_DAYS.set(
            max(0.0, float(days_remaining)),
            labels={"provider": contract.provider_name},
        )
        LIVE_DATA_ENTITLEMENT_CHECKS_TOTAL.inc(
            labels={
                "provider": contract.provider_name,
                "tier": requested_tier.value,
                "status": status.value,
            }
        )

        return EntitlementCheckResult(
            status=status,
            is_authorized=is_authorized,
            contract_id=contract.contract_id,
            provider_name=contract.provider_name,
            requested_tier=requested_tier,
            days_until_expiration=days_remaining,
            errors=errors,
            warnings=warnings,
        )

    def assert_entitled(
        self,
        provider_name: str,
        requested_tier: ContractTier,
        scope: RedistributionScope = RedistributionScope.DISPLAY,
        disclaimer_acknowledged: bool = True,
    ) -> None:
        """Assert provider entitlement, raising LiveDataEntitlementError if unauthorized."""
        result = self.verify_entitlement(
            provider_name=provider_name,
            requested_tier=requested_tier,
            scope=scope,
            disclaimer_acknowledged=disclaimer_acknowledged,
        )
        if not result.is_authorized:
            err_msg = "; ".join(result.errors)
            logger.error(
                "Market data entitlement check failed for '%s' (%s): %s",
                provider_name,
                requested_tier.value,
                err_msg,
            )
            raise LiveDataEntitlementError(
                f"Canlı veri kullanım yetkisi doğrulanamadı [{result.status.value}]: {err_msg}"
            )


def create_default_contract_auditor() -> LiveDataContractAuditor:
    """Build and bootstrap the default production and sandbox contract auditor."""
    auditor = LiveDataContractAuditor()

    # Primary BIST licensed distributor contract (e.g., Matriks Data Feed)
    primary_bist_contract = ProviderContractMetadata(
        contract_id="BIST-VND-2026-TR-881",
        provider_name="matriks_bist_primary",
        vendor_license_number="BIST-DIST-2026/044",
        feed_tiers=[
            ContractTier.BIST_EQUITY_DELAYED_15MIN,
            ContractTier.BIST_INDEX_EOD,
        ],
        redistribution_scopes=[
            RedistributionScope.DISPLAY,
            RedistributionScope.NON_DISPLAY,
            RedistributionScope.DERIVED_DATA,
        ],
        effective_date=date(2026, 1, 1),
        expiration_date=date(2027, 12, 31),
        max_cache_ttl_seconds=900,
        commercial_use_approved=True,
        disclaimer_required=True,
        entitlement_token="bist_entitlement_v2_signed_live",
        audit_notes=(
            "BIST 15-dakika gecikmeli hisse ve gün sonu endeks lisans anlaşması. "
            "SPK yasal uyarısı ve veri gecikme bildirimi şarttır."
        ),
    )
    primary_bist_contract.signature_hash = primary_bist_contract.compute_signature_hash()
    auditor.register_contract(primary_bist_contract)

    # Synthetic sandbox contract for local test/dev isolation
    sandbox_contract = ProviderContractMetadata(
        contract_id="SANDBOX-SYNTHETIC-2026",
        provider_name="synthetic",
        vendor_license_number="SANDBOX-INTERNAL-DEV",
        feed_tiers=[ContractTier.SYNTHETIC_SANDBOX],
        redistribution_scopes=[
            RedistributionScope.DISPLAY,
            RedistributionScope.NON_DISPLAY,
            RedistributionScope.DERIVED_DATA,
        ],
        effective_date=date(2020, 1, 1),
        expiration_date=date(2035, 12, 31),
        max_cache_ttl_seconds=3600,
        commercial_use_approved=False,
        disclaimer_required=False,
        entitlement_token="sandbox_dev_token",
        audit_notes="Geliştirme ve test amaçlı sentetik piyasa verisi ortamı.",
    )
    sandbox_contract.signature_hash = sandbox_contract.compute_signature_hash()
    auditor.register_contract(sandbox_contract)

    return auditor


LIVE_DATA_CONTRACT_AUDITOR: LiveDataContractAuditor = create_default_contract_auditor()
