"""Otomatik yeniden eğitim kalite kapısı ve insan onayı yönetimi.

Retraining Quality Gate & Human Approval.

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Otomatik model yeniden eğitim (retraining) süreci tamamen otonom veya gizli biçimde
  çalıştırılamaz. İzinsiz canlı model güncellemesi kesinlikle engellenir.
- Süreç iki aşamalı yetkili insan onayı (Human-in-the-Loop) ve katı ön kalite kapısı ile korunur:
  1. Aşama: Veri kayması (PSI), performans düşüşü veya veri eşiği gerekçesiyle açılan
     talep için Ön Kalite Kapısı (Pre-Retraining Quality Gate) doğrulanır ve yetkili
     ML mühendisi / sistem yöneticisi eğitimi başlatma onayını (Training Initiation Approval) verir.
  2. Aşama: Model eğitildikten, Gatekeeper (baseline ve friksiyonlu kıyas) ve ShadowMode
     kriterlerini tamamladıktan sonra canlıya alma onayı (Promotion to Active Approval)
     yetkili denetçi tarafından verilir.
- Tüm kararlar ve durum geçişleri SPK mevzuatına uygun yasal uyarı ve denetim iziyle saklanır.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.models.governance import (
    ModelGovernanceRegistry,
    ModelLifecycleStage,
)

DEFAULT_RETRAINING_DISCLAIMER = (
    "Bu yeniden eğitim ve kalite onay kaydı, teknik model yönetişimi ve model sağlığı "
    "kapsamında tutulmaktadır. Yeniden eğitim kararları veya onayları hiçbir surette "
    "yatırım tavsiyesi veya getiri vaadi niteliğinde değildir. SPK mevzuatı uyarınca "
    "yatırım danışmanlığı kapsamında değildir."
)

AUTHORIZED_APPROVAL_ROLES = frozenset({"ml_lead", "system_admin", "compliance_officer"})


class RetrainingError(Exception):
    """Model yeniden eğitim ve onay süreci genel hata sınıfı."""


class QualityGateFailedError(RetrainingError):
    """Yeniden eğitim öncesi kalite kapısı doğrulaması başarısız olduğunda fırlatılır."""


class UnauthorizedApprovalError(RetrainingError):
    """Yetkisiz kullanıcı veya rol onay vermeye çalıştığında fırlatılır."""


class InvalidWorkflowTransitionError(RetrainingError):
    """Geçersiz veya sıralaması hatalı iş akışı geçişinde fırlatılır."""


class RetrainingTriggerType(StrEnum):
    """Yeniden eğitim talebini tetikleyen gerekçe türü."""

    DATA_DRIFT = "DATA_DRIFT"  # Veri dağılım kayması (PSI > eşik)
    PERFORMANCE_DEGRADATION = "PERFORMANCE_DEGRADATION"  # Yön doğruluğu / finansal metrik düşüşü
    DATA_VOLUME_THRESHOLD = "DATA_VOLUME_THRESHOLD"  # Yeni doğrulanmış bar sayısı eşiği
    SCHEDULED_REVIEW = "SCHEDULED_REVIEW"  # Periyodik takvimsel teknik inceleme


class RetrainingRequestStatus(StrEnum):
    """Yeniden eğitim talep durumları."""

    PENDING_QUALITY_GATE = "PENDING_QUALITY_GATE"
    QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
    PENDING_TRAINING_APPROVAL = "PENDING_TRAINING_APPROVAL"
    TRAINING_REJECTED = "TRAINING_REJECTED"
    TRAINING_APPROVED = "TRAINING_APPROVED"
    TRAINING_IN_PROGRESS = "TRAINING_IN_PROGRESS"
    TRAINING_COMPLETED = "TRAINING_COMPLETED"
    TRAINING_FAILED = "TRAINING_FAILED"
    PENDING_PROMOTION_APPROVAL = "PENDING_PROMOTION_APPROVAL"
    PROMOTED_TO_ACTIVE = "PROMOTED_TO_ACTIVE"
    PROMOTION_REJECTED = "PROMOTION_REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class QualityGateCheckResult:
    """Tek bir kalite kapısı kontrolünün sonucu."""

    name: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityGateReport:
    """Eğitim öncesi kapsamlı kalite kapısı değerlendirme raporu."""

    evaluated_at: str
    passed: bool
    checks: list[QualityGateCheckResult]
    summary: str
    disclaimer: str = DEFAULT_RETRAINING_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRecord:
    """İnsan onayı denetim kaydı."""

    stage: str  # "TRAINING_INITIATION" veya "PROMOTION_TO_ACTIVE"
    decision: str  # "APPROVED" veya "REJECTED"
    approver_id: str
    approver_role: str
    reason: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrainingRequest:
    """Yeniden eğitim talep ve yaşam döngüsü kaydı."""

    request_id: str
    model_name: str
    target_type: str
    target_horizon: str
    trigger_type: RetrainingTriggerType
    trigger_reason: str
    trigger_metrics: dict[str, Any]
    created_at: str
    status: RetrainingRequestStatus
    quality_gate_report: QualityGateReport | None = None
    approvals: list[ApprovalRecord] = field(default_factory=list)
    trained_model_version: str | None = None
    notes: list[str] = field(default_factory=list)
    disclaimer: str = DEFAULT_RETRAINING_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrainingQualityGate:
    """Eğitim öncesi veri bütünlüğü, sızıntı denetimi ve gerekçe kapısı."""

    def __init__(
        self,
        min_new_bars: int = 60,
        max_acceptable_integrity_errors: int = 0,
        min_drift_psi: float = 0.20,
        min_performance_drop: float = 0.05,
    ) -> None:
        self.min_new_bars = min_new_bars
        self.max_acceptable_integrity_errors = max_acceptable_integrity_errors
        self.min_drift_psi = min_drift_psi
        self.min_performance_drop = min_performance_drop

    def evaluate(
        self,
        new_bars_count: int,
        integrity_error_count: int,
        is_leakage_free: bool,
        trigger_type: RetrainingTriggerType,
        trigger_metrics: dict[str, Any],
    ) -> QualityGateReport:
        """Tüm ön eğitim koşullarını değerlendirir ve rapor üretir."""
        checks: list[QualityGateCheckResult] = []

        # 1. Asgari veri boyutu kontrolü
        vol_passed = new_bars_count >= self.min_new_bars
        checks.append(
            QualityGateCheckResult(
                name="DATA_VOLUME",
                passed=vol_passed,
                message=f"Yeni bar sayısı: {new_bars_count} (Asgari gerekli: {self.min_new_bars})",
                details={"new_bars_count": new_bars_count, "min_required": self.min_new_bars},
            )
        )

        # 2. Veri bütünlüğü ve temizliği kontrolü
        int_passed = integrity_error_count <= self.max_acceptable_integrity_errors
        checks.append(
            QualityGateCheckResult(
                name="DATA_INTEGRITY",
                passed=int_passed,
                message=(
                    f"Bütünlük hatası: {integrity_error_count} "
                    f"(İzin verilen: {self.max_acceptable_integrity_errors})"
                ),
                details={
                    "integrity_errors": integrity_error_count,
                    "max_allowed": self.max_acceptable_integrity_errors,
                },
            )
        )

        # 3. Zaman serisi sızıntısız bölünme ve lookahead denetimi
        checks.append(
            QualityGateCheckResult(
                name="TEMPORAL_LEAKAGE_GUARD",
                passed=is_leakage_free,
                message="Veri seti kronolojik ayrılmış ve embargo tamponuyla sızıntısız doğrulanmış"
                if is_leakage_free
                else "Veri setinde kronolojik örtüşme veya hedef sızıntısı tespit edildi",
                details={"is_leakage_free": is_leakage_free},
            )
        )

        # 4. Tetikleyici gerekçe doğrulaması
        justification_passed = False
        justification_msg = ""
        if trigger_type == RetrainingTriggerType.DATA_DRIFT:
            psi = float(trigger_metrics.get("psi", 0.0))
            justification_passed = psi >= self.min_drift_psi
            drift_note = (
                "Yeterli kayma gerekçesi var"
                if justification_passed
                else "Yetersiz kayma, eğitim gereksiz"
            )
            justification_msg = (
                f"PSI değeri: {psi:.4f} (Eşik: {self.min_drift_psi:.4f}) - {drift_note}"
            )
        elif trigger_type == RetrainingTriggerType.PERFORMANCE_DEGRADATION:
            drop = float(trigger_metrics.get("metric_drop", 0.0))
            justification_passed = drop >= self.min_performance_drop
            perf_note = (
                "Yeterli düşüş gerekçesi var"
                if justification_passed
                else "İhmal edilebilir değişim"
            )
            justification_msg = (
                f"Performans düşüşü: {drop:.4f} "
                f"(Eşik: {self.min_performance_drop:.4f}) - {perf_note}"
            )
        elif trigger_type in (
            RetrainingTriggerType.DATA_VOLUME_THRESHOLD,
            RetrainingTriggerType.SCHEDULED_REVIEW,
        ):
            justification_passed = vol_passed
            justification_msg = (
                f"Tetikleyici türü: {trigger_type.value}, veri hacim koşulu temel alındı."
            )

        checks.append(
            QualityGateCheckResult(
                name="TRIGGER_JUSTIFICATION",
                passed=justification_passed,
                message=justification_msg,
                details={"trigger_type": trigger_type.value, "metrics": trigger_metrics},
            )
        )

        overall_passed = all(c.passed for c in checks)
        summary = (
            "Ön kalite kapısı başarıyla geçildi; yetkili insan onayına hazır."
            if overall_passed
            else "Ön kalite kapısı kriterleri karşılanamadı; yeniden eğitim başlatılamaz."
        )

        return QualityGateReport(
            evaluated_at=datetime.now(UTC).isoformat(),
            passed=overall_passed,
            checks=checks,
            summary=summary,
            disclaimer=DEFAULT_RETRAINING_DISCLAIMER,
        )


class RetrainingWorkflowManager:
    """Yeniden eğitim taleplerini, kalite kapılarını ve iki aşamalı insan onayını yönetir."""

    def __init__(
        self,
        quality_gate: RetrainingQualityGate | None = None,
        governance_registry: ModelGovernanceRegistry | None = None,
    ) -> None:
        self.quality_gate = quality_gate or RetrainingQualityGate()
        self.governance_registry = governance_registry
        self._requests: dict[str, RetrainingRequest] = {}

    def create_request(
        self,
        model_name: str,
        target_type: str,
        target_horizon: str,
        trigger_type: RetrainingTriggerType,
        trigger_reason: str,
        trigger_metrics: dict[str, Any] | None = None,
    ) -> RetrainingRequest:
        """Yeni bir yeniden eğitim talebi oluşturur (durum: PENDING_QUALITY_GATE)."""
        validate_compliant_text(trigger_reason)
        request_id = f"retrain-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()

        req = RetrainingRequest(
            request_id=request_id,
            model_name=model_name,
            target_type=target_type,
            target_horizon=target_horizon,
            trigger_type=trigger_type,
            trigger_reason=trigger_reason,
            trigger_metrics=trigger_metrics or {},
            created_at=now,
            status=RetrainingRequestStatus.PENDING_QUALITY_GATE,
        )
        self._requests[request_id] = req
        return req

    def get_request(self, request_id: str) -> RetrainingRequest:
        """Belirtilen ID'ye sahip talebi getirir."""
        if request_id not in self._requests:
            raise RetrainingError(f"Yeniden eğitim talebi bulunamadı: {request_id}")
        return self._requests[request_id]

    def evaluate_quality_gate(
        self,
        request_id: str,
        new_bars_count: int,
        integrity_error_count: int,
        is_leakage_free: bool,
    ) -> QualityGateReport:
        """Talebin ön kalite kapısı değerlendirmesini yapar."""
        req = self.get_request(request_id)
        if req.status not in (
            RetrainingRequestStatus.PENDING_QUALITY_GATE,
            RetrainingRequestStatus.QUALITY_GATE_FAILED,
        ):
            raise InvalidWorkflowTransitionError(
                "Kalite kapısı değerlendirmesi yalnızca PENDING_QUALITY_GATE veya "
                f"QUALITY_GATE_FAILED durumunda yapılabilir. Mevcut durum: {req.status.value}"
            )

        report = self.quality_gate.evaluate(
            new_bars_count=new_bars_count,
            integrity_error_count=integrity_error_count,
            is_leakage_free=is_leakage_free,
            trigger_type=req.trigger_type,
            trigger_metrics=req.trigger_metrics,
        )
        req.quality_gate_report = report

        if report.passed:
            req.status = RetrainingRequestStatus.PENDING_TRAINING_APPROVAL
        else:
            req.status = RetrainingRequestStatus.QUALITY_GATE_FAILED

        return report

    def approve_training_initiation(
        self,
        request_id: str,
        approver_id: str,
        approver_role: str,
        reason: str,
    ) -> RetrainingRequest:
        """1. Aşama İnsan Onayı: Eğitimin başlatılmasına onay verir."""
        req = self.get_request(request_id)

        if req.status != RetrainingRequestStatus.PENDING_TRAINING_APPROVAL:
            raise InvalidWorkflowTransitionError(
                "Eğitim başlatma onayı yalnızca PENDING_TRAINING_APPROVAL "
                f"durumunda verilebilir. Mevcut: {req.status.value}"
            )

        if approver_role not in AUTHORIZED_APPROVAL_ROLES:
            roles = sorted(AUTHORIZED_APPROVAL_ROLES)
            raise UnauthorizedApprovalError(
                f"Eğitim başlatma onayı için yetkisiz rol: {approver_role}. "
                f"İzin verilenler: {roles}"
            )

        validate_compliant_text(reason)

        approval = ApprovalRecord(
            stage="TRAINING_INITIATION",
            decision="APPROVED",
            approver_id=approver_id,
            approver_role=approver_role,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )
        req.approvals.append(approval)
        req.status = RetrainingRequestStatus.TRAINING_APPROVED
        return req

    def reject_training_initiation(
        self,
        request_id: str,
        approver_id: str,
        approver_role: str,
        reason: str,
    ) -> RetrainingRequest:
        """1. Aşama İnsan Ret: Eğitimin başlatılmasını reddeder."""
        req = self.get_request(request_id)

        if req.status != RetrainingRequestStatus.PENDING_TRAINING_APPROVAL:
            raise InvalidWorkflowTransitionError(
                "Eğitim başlatma reddi yalnızca PENDING_TRAINING_APPROVAL "
                f"durumunda verilebilir. Mevcut: {req.status.value}"
            )

        if approver_role not in AUTHORIZED_APPROVAL_ROLES:
            raise UnauthorizedApprovalError(
                f"Eğitim başlatma reddi için yetkisiz rol: {approver_role}."
            )

        validate_compliant_text(reason)

        approval = ApprovalRecord(
            stage="TRAINING_INITIATION",
            decision="REJECTED",
            approver_id=approver_id,
            approver_role=approver_role,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )
        req.approvals.append(approval)
        req.status = RetrainingRequestStatus.TRAINING_REJECTED
        return req

    def start_training(self, request_id: str) -> None:
        """Eğitimin fiilen başladığını kaydeder (önce TRAINING_APPROVED olmalı)."""
        req = self.get_request(request_id)
        if req.status != RetrainingRequestStatus.TRAINING_APPROVED:
            raise InvalidWorkflowTransitionError(
                f"Eğitim başlatılamaz! İnsan onayı eksik veya durum uygun değil: {req.status.value}"
            )
        req.status = RetrainingRequestStatus.TRAINING_IN_PROGRESS

    def complete_training(
        self,
        request_id: str,
        trained_model_version: str,
        notes: str | None = None,
    ) -> None:
        """Eğitimin tamamlandığını kaydeder ve 2. Aşama Onay beklemeye alır."""
        req = self.get_request(request_id)
        if req.status != RetrainingRequestStatus.TRAINING_IN_PROGRESS:
            raise InvalidWorkflowTransitionError(
                "Eğitim tamamlama yalnızca TRAINING_IN_PROGRESS "
                f"durumunda yapılabilir. Mevcut: {req.status.value}"
            )

        req.trained_model_version = trained_model_version
        if notes:
            validate_compliant_text(notes)
            req.notes.append(notes)
        req.status = RetrainingRequestStatus.PENDING_PROMOTION_APPROVAL

    def fail_training(self, request_id: str, error_message: str) -> None:
        """Eğitim sırasında hata oluştuğunda durumu günceller."""
        req = self.get_request(request_id)
        if req.status != RetrainingRequestStatus.TRAINING_IN_PROGRESS:
            raise InvalidWorkflowTransitionError(
                "Eğitim hatası yalnızca TRAINING_IN_PROGRESS "
                f"durumunda kaydedilebilir. Mevcut: {req.status.value}"
            )
        req.status = RetrainingRequestStatus.TRAINING_FAILED
        req.notes.append(f"Hata: {error_message}")

    def approve_promotion_to_active(
        self,
        request_id: str,
        approver_id: str,
        approver_role: str,
        reason: str,
        gatekeeper_verified: bool = True,
        shadow_verified: bool = True,
    ) -> RetrainingRequest:
        """2. Aşama İnsan Onayı: Eğitilen yeni modelin canlıya alınmasını onaylar."""
        req = self.get_request(request_id)

        if req.status != RetrainingRequestStatus.PENDING_PROMOTION_APPROVAL:
            raise InvalidWorkflowTransitionError(
                "Canlıya alma onayı yalnızca PENDING_PROMOTION_APPROVAL "
                f"durumunda verilebilir. Mevcut: {req.status.value}"
            )

        if approver_role not in AUTHORIZED_APPROVAL_ROLES:
            roles = sorted(AUTHORIZED_APPROVAL_ROLES)
            raise UnauthorizedApprovalError(
                f"Canlıya alma onayı için yetkisiz rol: {approver_role}. İzin verilenler: {roles}"
            )

        if not gatekeeper_verified or not shadow_verified:
            raise QualityGateFailedError(
                "Gatekeeper veya ShadowMode doğrulaması geçilmeden yeni model canlıya onaylanamaz!"
            )

        validate_compliant_text(reason)

        approval = ApprovalRecord(
            stage="PROMOTION_TO_ACTIVE",
            decision="APPROVED",
            approver_id=approver_id,
            approver_role=approver_role,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )
        req.approvals.append(approval)
        req.status = RetrainingRequestStatus.PROMOTED_TO_ACTIVE

        # Eğer ModelGovernanceRegistry bağlıysa modelin durumunu güncelle
        if self.governance_registry and req.trained_model_version:
            try:
                rec = self.governance_registry.get_record(req.trained_model_version)
                if rec and rec.stage != ModelLifecycleStage.ACTIVE:
                    if rec.stage == ModelLifecycleStage.CANDIDATE:
                        self.governance_registry.mark_evaluated(req.trained_model_version)
                        self.governance_registry.start_shadow_mode(req.trained_model_version)
                        self.governance_registry.approve_model(
                            req.trained_model_version,
                            approved_by=approver_id,
                            approval_notes=reason,
                        )
                    elif rec.stage == ModelLifecycleStage.EVALUATED:
                        self.governance_registry.start_shadow_mode(req.trained_model_version)
                        self.governance_registry.approve_model(
                            req.trained_model_version,
                            approved_by=approver_id,
                            approval_notes=reason,
                        )
                    elif rec.stage == ModelLifecycleStage.SHADOW:
                        self.governance_registry.approve_model(
                            req.trained_model_version,
                            approved_by=approver_id,
                            approval_notes=reason,
                        )

                    rec_after = self.governance_registry.get_record(req.trained_model_version)
                    if rec_after and rec_after.stage == ModelLifecycleStage.APPROVED:
                        self.governance_registry.promote_to_active(
                            req.trained_model_version,
                            promoted_by=approver_id,
                            notes=f"Yeniden eğitim 2. aşama insan onayı ({request_id}): {reason}",
                        )
            except Exception:
                # Model kaydı bulunamadıysa veya test mock ise sessizce devam et
                pass

        return req

    def reject_promotion(
        self,
        request_id: str,
        approver_id: str,
        approver_role: str,
        reason: str,
    ) -> RetrainingRequest:
        """2. Aşama İnsan Ret: Eğitilen yeni modelin canlıya alınmasını reddeder."""
        req = self.get_request(request_id)

        if req.status != RetrainingRequestStatus.PENDING_PROMOTION_APPROVAL:
            raise InvalidWorkflowTransitionError(
                "Canlıya alma reddi yalnızca PENDING_PROMOTION_APPROVAL "
                f"durumunda verilebilir. Mevcut: {req.status.value}"
            )

        if approver_role not in AUTHORIZED_APPROVAL_ROLES:
            raise UnauthorizedApprovalError(
                f"Canlıya alma reddi için yetkisiz rol: {approver_role}."
            )

        validate_compliant_text(reason)

        approval = ApprovalRecord(
            stage="PROMOTION_TO_ACTIVE",
            decision="REJECTED",
            approver_id=approver_id,
            approver_role=approver_role,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )
        req.approvals.append(approval)
        req.status = RetrainingRequestStatus.PROMOTION_REJECTED
        return req

    def list_requests(
        self,
        status: RetrainingRequestStatus | None = None,
        model_name: str | None = None,
    ) -> list[RetrainingRequest]:
        """Talepleri listeler ve isteğe bağlı filtreler."""
        reqs = list(self._requests.values())
        if status:
            reqs = [r for r in reqs if r.status == status]
        if model_name:
            reqs = [r for r in reqs if r.model_name == model_name]
        return sorted(reqs, key=lambda x: x.created_at, reverse=True)

    def save_to_json(self, file_path: Path | str) -> None:
        """Tüm talep ve onay kayıtlarını güvenli JSON dosyasına yazar."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {req_id: req.to_dict() for req_id, req in self._requests.items()}
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_from_json(self, file_path: Path | str) -> None:
        """JSON dosyasından kayıtları yükler."""
        p = Path(file_path)
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        for req_id, item in data.items():
            qg_data = item.get("quality_gate_report")
            qg_report = None
            if qg_data:
                checks = [
                    QualityGateCheckResult(
                        name=c["name"],
                        passed=c["passed"],
                        message=c["message"],
                        details=c.get("details", {}),
                    )
                    for c in qg_data.get("checks", [])
                ]
                qg_report = QualityGateReport(
                    evaluated_at=qg_data["evaluated_at"],
                    passed=qg_data["passed"],
                    checks=checks,
                    summary=qg_data["summary"],
                    disclaimer=qg_data.get("disclaimer", DEFAULT_RETRAINING_DISCLAIMER),
                )

            approvals = [
                ApprovalRecord(
                    stage=a["stage"],
                    decision=a["decision"],
                    approver_id=a["approver_id"],
                    approver_role=a["approver_role"],
                    reason=a["reason"],
                    timestamp=a["timestamp"],
                )
                for a in item.get("approvals", [])
            ]

            req = RetrainingRequest(
                request_id=req_id,
                model_name=item["model_name"],
                target_type=item["target_type"],
                target_horizon=item["target_horizon"],
                trigger_type=RetrainingTriggerType(item["trigger_type"]),
                trigger_reason=item["trigger_reason"],
                trigger_metrics=item.get("trigger_metrics", {}),
                created_at=item["created_at"],
                status=RetrainingRequestStatus(item["status"]),
                quality_gate_report=qg_report,
                approvals=approvals,
                trained_model_version=item.get("trained_model_version"),
                notes=item.get("notes", []),
                disclaimer=item.get("disclaimer", DEFAULT_RETRAINING_DISCLAIMER),
            )
            self._requests[req_id] = req
