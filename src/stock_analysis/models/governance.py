"""Model kayıt defteri yönetişimi ve model onay süreci (Model Governance & Approval Workflow).

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Her modelin yaşam döngüsü şeffaf, izlenebilir ve denetlenebilir aşamalardan oluşur:
  CANDIDATE -> EVALUATED -> SHADOW -> APPROVED -> ACTIVE -> ARCHIVED (veya REJECTED).
- Naif baseline'ı ve friksiyonlu kıyası geçemeyen (Gatekeeper ret) veya gölge modunda
  asgari süre ve SLA kriterlerini karşılamayan (ShadowMode ret) hiçbir model onaylanamaz.
- Canlıya alma (ACTIVE) insan onayı / yetkili denetimi gerektirir; denetim izi (audit trail)
  kimin ne zaman onayladığını veya reddettiğini kayıt altına alır.
- Her hedef türü ve ufuk çifti (target_type, target_horizon) için aynı anda yalnızca
  TEK bir model ACTIVE olabilir. Yeni bir model terfi ettirildiğinde önceki model
  otomatik olarak ARCHIVED durumuna alınır. Acil durumda geri alma (rollback) desteklenir.
- Model başarısızsa veya onaylı aktif model yoksa sessizce eski/uydurma tahmin üretilmez;
  aktif model bulunamadığında açıkça None döner.
- Tüm süreçler ve kayıtlar SPK mevzuatına uygun yasal uyarı içerir.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.models.gatekeeper import PublishingDecision
from stock_analysis.models.shadow_mode import ShadowPromotionDecision


class GovernanceError(Exception):
    """Model yönetişimi ve onay süreci genel hata sınıfı."""


class ModelStageTransitionError(GovernanceError):
    """Geçersiz veya yetkisiz yaşam döngüsü durum geçişinde fırlatılır."""


class GovernanceIntegrityError(GovernanceError):
    """Yönetişim kayıt bütünlüğü bozulduğunda veya güvenlik kuralı ihlal edildiğinde fırlatılır."""


DEFAULT_GOVERNANCE_DISCLAIMER = (
    "Bu model onay ve yönetişim kaydı, sistem kalite güvencesi ve teknik denetim "
    "kapsamında tutulmaktadır. Model onay veya terfi kararları hiçbir surette yatırım "
    "danışmanlığı, yönlendirme veya getiri vaadi niteliği taşımaz. Geçmiş veya gölge "
    "modu performansı gelecekteki sonuçların aynen gerçekleşeceğini ifade etmez. "
    "SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında değildir."
)


class ModelLifecycleStage(StrEnum):
    """Model yaşam döngüsü durumları."""

    CANDIDATE = "CANDIDATE"  # Yeni eğitilmiş ve kaydedilmiş aday model
    EVALUATED = "EVALUATED"  # Geriye dönük metrikler ve naif baseline kıyasları tamamlandı
    SHADOW = "SHADOW"  # Kullanıcıya açılmadan gölge modunda izleniyor
    APPROVED = "APPROVED"  # Kapı ve gölge testlerini geçip yetkili onayını aldı
    ACTIVE = "ACTIVE"  # Canlı ortamda tahmin üreten resmi model (hedef başına en fazla 1)
    ARCHIVED = "ARCHIVED"  # Yeni model devreye girince veya kullanım dışı bırakılınca emekli edildi
    REJECTED = "REJECTED"  # Kriterleri geçemedi veya yetkili tarafından reddedildi


_VALID_TRANSITIONS: dict[ModelLifecycleStage, set[ModelLifecycleStage]] = {
    ModelLifecycleStage.CANDIDATE: {
        ModelLifecycleStage.EVALUATED,
        ModelLifecycleStage.REJECTED,
    },
    ModelLifecycleStage.EVALUATED: {
        ModelLifecycleStage.SHADOW,
        ModelLifecycleStage.REJECTED,
    },
    ModelLifecycleStage.SHADOW: {
        ModelLifecycleStage.APPROVED,
        ModelLifecycleStage.REJECTED,
    },
    ModelLifecycleStage.APPROVED: {
        ModelLifecycleStage.ACTIVE,
        ModelLifecycleStage.ARCHIVED,
        ModelLifecycleStage.REJECTED,
    },
    ModelLifecycleStage.ACTIVE: {
        ModelLifecycleStage.ARCHIVED,
    },
    ModelLifecycleStage.REJECTED: set(),  # Uç durum: yeniden aday olmak için yeni sürüm gerekir
    ModelLifecycleStage.ARCHIVED: {
        ModelLifecycleStage.ACTIVE,  # Acil durumda geri alma (rollback) için tekrar aktifleşebilir
    },
}

_SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _validate_safe_id(val: str, field_name: str) -> None:
    if not val or not _SAFE_ID_REGEX.match(val) or ".." in val:
        raise GovernanceIntegrityError(f"Güvensiz veya geçersiz {field_name}: '{val}'")


@dataclass(frozen=True)
class GovernanceAuditEntry:
    """Yönetişim denetim izi kaydı."""

    timestamp: str
    from_stage: str
    to_stage: str
    performed_by: str
    action: str
    notes: str


@dataclass
class ModelGovernanceRecord:
    """Bir modelin yönetişim, onay ve yaşam döngüsü kaydı."""

    model_id: str
    target_type: str
    target_horizon: str
    stage: ModelLifecycleStage = ModelLifecycleStage.CANDIDATE
    registered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    registered_by: str = "system"
    approved_at: str | None = None
    approved_by: str | None = None
    approval_notes: str | None = None
    rejection_reason: str | None = None
    gatekeeper_passed: bool = False
    shadow_mode_passed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    disclaimer: str = DEFAULT_GOVERNANCE_DISCLAIMER

    def __post_init__(self) -> None:
        _validate_safe_id(self.model_id, "model_id")
        validate_compliant_text(self.disclaimer)
        if self.approval_notes:
            validate_compliant_text(self.approval_notes)
        if self.rejection_reason:
            validate_compliant_text(self.rejection_reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "target_type": self.target_type,
            "target_horizon": self.target_horizon,
            "stage": str(self.stage),
            "registered_at": self.registered_at,
            "registered_by": self.registered_by,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "approval_notes": self.approval_notes,
            "rejection_reason": self.rejection_reason,
            "gatekeeper_passed": self.gatekeeper_passed,
            "shadow_mode_passed": self.shadow_mode_passed,
            "metadata": dict(self.metadata),
            "audit_trail": list(self.audit_trail),
            "disclaimer": self.disclaimer,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelGovernanceRecord:
        data_copy = dict(data)
        data_copy["stage"] = ModelLifecycleStage(data_copy["stage"])
        return cls(**data_copy)


class ModelGovernanceRegistry:
    """Model yaşam döngüsünü, onay süreçlerini ve aktif modelleri yöneten kayıt defteri."""

    def __init__(self, base_dir: Path | str = "ml/model_registry") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.base_dir / "governance_index.json"
        self._ensure_index()

    def _ensure_index(self) -> None:
        if not self.index_file.exists():
            self._write_index([])

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_file.exists():
            return []
        try:
            with self.index_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _write_index(self, records: list[dict[str, Any]]) -> None:
        temp_file = self.index_file.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        temp_file.replace(self.index_file)

    def register_candidate(
        self,
        model_id: str,
        target_type: str,
        target_horizon: str,
        registered_by: str = "pipeline",
        metadata: dict[str, Any] | None = None,
    ) -> ModelGovernanceRecord:
        """Yeni eğitilmiş bir modeli CANDIDATE olarak kayıt defterine ekler."""
        _validate_safe_id(model_id, "model_id")
        now = datetime.now(UTC).isoformat()
        records = self._read_index()

        for r in records:
            if r.get("model_id") == model_id:
                raise GovernanceIntegrityError(f"Model ID zaten kayıtlı: {model_id}")

        record = ModelGovernanceRecord(
            model_id=model_id,
            target_type=target_type,
            target_horizon=target_horizon,
            stage=ModelLifecycleStage.CANDIDATE,
            registered_at=now,
            registered_by=registered_by,
            metadata=metadata or {},
            audit_trail=[
                asdict(
                    GovernanceAuditEntry(
                        timestamp=now,
                        from_stage="NONE",
                        to_stage=str(ModelLifecycleStage.CANDIDATE),
                        performed_by=registered_by,
                        action="REGISTER_CANDIDATE",
                        notes="Model aday olarak sisteme kaydedildi.",
                    )
                )
            ],
        )

        records.append(record.to_dict())
        self._write_index(records)
        return record

    def get_record(self, model_id: str) -> ModelGovernanceRecord | None:
        """Belirtilen model ID'sine sahip kaydı döner."""
        _validate_safe_id(model_id, "model_id")
        records = self._read_index()
        for r in records:
            if r.get("model_id") == model_id:
                return ModelGovernanceRecord.from_dict(r)
        return None

    def list_records(
        self,
        stage: ModelLifecycleStage | None = None,
        target_type: str | None = None,
        target_horizon: str | None = None,
    ) -> list[ModelGovernanceRecord]:
        """Filtrelere göre modelleri listeler."""
        records = self._read_index()
        results: list[ModelGovernanceRecord] = []
        for r in records:
            try:
                rec = ModelGovernanceRecord.from_dict(r)
            except Exception:
                continue
            if stage and rec.stage != stage:
                continue
            if target_type and rec.target_type != target_type:
                continue
            if target_horizon and rec.target_horizon != target_horizon:
                continue
            results.append(rec)
        return results

    def _transition_stage(
        self,
        model_id: str,
        new_stage: ModelLifecycleStage,
        performed_by: str,
        action: str,
        notes: str,
        extra_updates: dict[str, Any] | None = None,
    ) -> ModelGovernanceRecord:
        """Modelin yaşam döngüsü aşamasını kontrollü şekilde günceller."""
        _validate_safe_id(model_id, "model_id")
        records = self._read_index()
        found_idx = -1
        for idx, r in enumerate(records):
            if r.get("model_id") == model_id:
                found_idx = idx
                break

        if found_idx == -1:
            raise GovernanceIntegrityError(f"Model bulunamadı: {model_id}")

        current_record = ModelGovernanceRecord.from_dict(records[found_idx])
        current_stage = current_record.stage

        allowed_next = _VALID_TRANSITIONS.get(current_stage, set())
        if new_stage not in allowed_next:
            raise ModelStageTransitionError(
                f"Geçersiz durum geçişi: '{current_stage}' -> '{new_stage}'. "
                f"İzin verilen sonraki aşamalar: {[str(s) for s in allowed_next]}"
            )

        now = datetime.now(UTC).isoformat()
        current_record.stage = new_stage
        current_record.audit_trail.append(
            asdict(
                GovernanceAuditEntry(
                    timestamp=now,
                    from_stage=str(current_stage),
                    to_stage=str(new_stage),
                    performed_by=performed_by,
                    action=action,
                    notes=notes,
                )
            )
        )

        if extra_updates:
            for k, v in extra_updates.items():
                setattr(current_record, k, v)

        records[found_idx] = current_record.to_dict()
        self._write_index(records)
        return current_record

    def mark_evaluated(
        self,
        model_id: str,
        performed_by: str = "evaluator",
        metrics_summary: dict[str, Any] | None = None,
    ) -> ModelGovernanceRecord:
        """Modelin offline değerlendirme testlerini geçtiğini işaretler (CANDIDATE -> EVALUATED)."""
        extra = {}
        if metrics_summary:
            rec = self.get_record(model_id)
            if rec:
                current_meta = dict(rec.metadata)
                current_meta["evaluation_metrics"] = metrics_summary
                extra["metadata"] = current_meta

        return self._transition_stage(
            model_id=model_id,
            new_stage=ModelLifecycleStage.EVALUATED,
            performed_by=performed_by,
            action="MARK_EVALUATED",
            notes="Offline metrik ve baseline kıyaslamaları tamamlandı.",
            extra_updates=extra,
        )

    def start_shadow_mode(
        self,
        model_id: str,
        performed_by: str = "shadow_coordinator",
        notes: str = "Model canlıya hazırlık için gölge moduna alındı.",
    ) -> ModelGovernanceRecord:
        """Modeli gölge moduna geçirir (EVALUATED -> SHADOW)."""
        return self._transition_stage(
            model_id=model_id,
            new_stage=ModelLifecycleStage.SHADOW,
            performed_by=performed_by,
            action="START_SHADOW_MODE",
            notes=notes,
        )

    def approve_model(
        self,
        model_id: str,
        approved_by: str,
        approval_notes: str,
        gatekeeper_decision: PublishingDecision | None = None,
        shadow_decision: ShadowPromotionDecision | None = None,
    ) -> ModelGovernanceRecord:
        """Modeli inceler ve yetkili onayı verir (SHADOW -> APPROVED).

        Katı kurallar:
        1. Eğer gatekeeper kararı verilmişse `is_publishable` True olmalıdır.
        2. Eğer shadow kararı verilmişse `PROCEED_TO_LIVE` olmalıdır.
        3. Onaylayan kişi (approved_by) ve gerekçeli onay notu zorunludur.
        """
        if not approved_by or not approved_by.strip():
            raise GovernanceIntegrityError(
                "Model onayı için onaylayan (approved_by) bilgisi zorunludur."
            )
        if not approval_notes or not approval_notes.strip():
            raise GovernanceIntegrityError(
                "Model onayı için gerekçeli onay notu (approval_notes) zorunludur."
            )

        gatekeeper_passed = False
        if gatekeeper_decision is not None:
            if not gatekeeper_decision.is_approved:
                reasons = ", ".join(gatekeeper_decision.rejection_reasons)
                raise GovernanceIntegrityError(
                    f"Model Gatekeeper yayın kriterlerini karşılamıyor: {reasons}"
                )
            gatekeeper_passed = True

        shadow_mode_passed = False
        if shadow_decision is not None:
            if shadow_decision != ShadowPromotionDecision.PROCEED_TO_LIVE:
                raise GovernanceIntegrityError(
                    f"Model Gölge Modu terfi kriterini karşılamıyor. Karar: {shadow_decision}"
                )
            shadow_mode_passed = True

        now = datetime.now(UTC).isoformat()
        extra_updates: dict[str, Any] = {
            "approved_at": now,
            "approved_by": approved_by,
            "approval_notes": approval_notes,
            "gatekeeper_passed": gatekeeper_passed,
            "shadow_mode_passed": shadow_mode_passed,
        }

        return self._transition_stage(
            model_id=model_id,
            new_stage=ModelLifecycleStage.APPROVED,
            performed_by=approved_by,
            action="APPROVE_MODEL",
            notes=f"Model onaylandı. Not: {approval_notes}",
            extra_updates=extra_updates,
        )

    def reject_model(
        self,
        model_id: str,
        rejected_by: str,
        rejection_reason: str,
    ) -> ModelGovernanceRecord:
        """Modeli reddeder (CANDIDATE/EVALUATED/SHADOW/APPROVED -> REJECTED)."""
        if not rejection_reason or not rejection_reason.strip():
            raise GovernanceIntegrityError(
                "Model reddi için açık bir gerekçe (rejection_reason) zorunludur."
            )

        extra_updates = {
            "rejection_reason": rejection_reason,
        }

        return self._transition_stage(
            model_id=model_id,
            new_stage=ModelLifecycleStage.REJECTED,
            performed_by=rejected_by,
            action="REJECT_MODEL",
            notes=f"Model reddedildi. Gerekçe: {rejection_reason}",
            extra_updates=extra_updates,
        )

    def promote_to_active(
        self,
        model_id: str,
        promoted_by: str,
        notes: str = "Model canlı üretime alındı.",
    ) -> ModelGovernanceRecord:
        """Onaylı bir modeli canlı üretime alır (APPROVED -> ACTIVE).

        Önemli Kural:
        Aynı target_type ve target_horizon için zaten bir ACTIVE model varsa,
        o model otomatik olarak ARCHIVED durumuna alınır.
        """
        rec = self.get_record(model_id)
        if not rec:
            raise GovernanceIntegrityError(f"Model bulunamadı: {model_id}")

        if rec.stage != ModelLifecycleStage.APPROVED:
            raise ModelStageTransitionError(
                "Yalnızca APPROVED durumundaki modeller ACTIVE yapılabilir. "
                f"Mevcut durum: '{rec.stage}'"
            )

        # Aynı hedef ve ufuktaki mevcut ACTIVE modelleri bul ve arşivle
        current_actives = self.list_records(
            stage=ModelLifecycleStage.ACTIVE,
            target_type=rec.target_type,
            target_horizon=rec.target_horizon,
        )

        for active_model in current_actives:
            if active_model.model_id != model_id:
                self._transition_stage(
                    model_id=active_model.model_id,
                    new_stage=ModelLifecycleStage.ARCHIVED,
                    performed_by=promoted_by,
                    action="DEPRECATE_FOR_NEW_VERSION",
                    notes=f"Yeni aktif model {model_id} devreye girdiği için arşivlendi.",
                )

        return self._transition_stage(
            model_id=model_id,
            new_stage=ModelLifecycleStage.ACTIVE,
            performed_by=promoted_by,
            action="PROMOTE_TO_ACTIVE",
            notes=notes,
        )

    def rollback_production(
        self,
        target_type: str,
        target_horizon: str,
        rollback_to_model_id: str,
        executed_by: str,
        reason: str,
    ) -> ModelGovernanceRecord:
        """Acil durumda veya model bozulmasında önceki arşivlenmiş/onaylı modele geri döner.

        Mevcut ACTIVE model ARCHIVED yapılır; rollback_to_model_id ise ACTIVE yapılır.
        """
        if not reason or not reason.strip():
            raise GovernanceIntegrityError(
                "Rollback işlemi için zorunlu gerekçe (reason) girilmelidir."
            )

        target_model = self.get_record(rollback_to_model_id)
        if not target_model:
            raise GovernanceIntegrityError(
                f"Rollback yapılacak model bulunamadı: {rollback_to_model_id}"
            )

        if target_model.target_type != target_type or target_model.target_horizon != target_horizon:
            raise GovernanceIntegrityError(
                f"Hedef/ufuk uyuşmazlığı: Model ({target_model.target_type}, "
                f"{target_model.target_horizon}) != İstenen ({target_type}, {target_horizon})"
            )

        if target_model.stage not in {ModelLifecycleStage.ARCHIVED, ModelLifecycleStage.APPROVED}:
            raise ModelStageTransitionError(
                "Rollback yalnızca ARCHIVED veya APPROVED durumundaki modellere yapılabilir. "
                f"Mevcut: {target_model.stage}"
            )

        # Mevcut aktif modeli arşivle
        current_actives = self.list_records(
            stage=ModelLifecycleStage.ACTIVE,
            target_type=target_type,
            target_horizon=target_horizon,
        )
        for active_model in current_actives:
            self._transition_stage(
                model_id=active_model.model_id,
                new_stage=ModelLifecycleStage.ARCHIVED,
                performed_by=executed_by,
                action="ARCHIVED_BY_ROLLBACK",
                notes=f"Rollback nedeniyle arşivlendi. Gerekçe: {reason}",
            )

        return self._transition_stage(
            model_id=rollback_to_model_id,
            new_stage=ModelLifecycleStage.ACTIVE,
            performed_by=executed_by,
            action="ROLLBACK_ACTIVATION",
            notes=f"Rollback ile yeniden aktif edildi. Gerekçe: {reason}",
        )

    def get_active_model(
        self,
        target_type: str,
        target_horizon: str,
    ) -> ModelGovernanceRecord | None:
        """Belirtilen hedef ve ufuk için canlıda olan onaylı aktif modeli döner.

        Aktif model yoksa kesinlikle sessiz fallback veya varsayılan model uydurulmaz;
        doğrudan None döner (kullanıcıya 'tahmin kapalı' durumu gösterilmelidir).
        """
        actives = self.list_records(
            stage=ModelLifecycleStage.ACTIVE,
            target_type=target_type,
            target_horizon=target_horizon,
        )
        if not actives:
            return None
        return actives[0]
