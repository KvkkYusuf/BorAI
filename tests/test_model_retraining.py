"""Otomatik yeniden eğitim kalite kapısı ve insan onayı testleri."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from stock_analysis.models.governance import (
    ModelGovernanceRegistry,
    ModelLifecycleStage,
)
from stock_analysis.models.retraining import (
    InvalidWorkflowTransitionError,
    QualityGateFailedError,
    RetrainingQualityGate,
    RetrainingRequestStatus,
    RetrainingTriggerType,
    RetrainingWorkflowManager,
    UnauthorizedApprovalError,
)


def test_retraining_quality_gate_passes_when_all_conditions_met() -> None:
    gate = RetrainingQualityGate(
        min_new_bars=60,
        max_acceptable_integrity_errors=0,
        min_drift_psi=0.20,
    )
    report = gate.evaluate(
        new_bars_count=100,
        integrity_error_count=0,
        is_leakage_free=True,
        trigger_type=RetrainingTriggerType.DATA_DRIFT,
        trigger_metrics={"psi": 0.28},
    )
    assert report.passed is True
    assert len(report.checks) == 4
    assert all(c.passed for c in report.checks)
    assert "Ön kalite kapısı başarıyla geçildi" in report.summary
    assert "SPK mevzuatı" in report.disclaimer


def test_retraining_quality_gate_fails_on_insufficient_volume() -> None:
    gate = RetrainingQualityGate(min_new_bars=60)
    report = gate.evaluate(
        new_bars_count=25,
        integrity_error_count=0,
        is_leakage_free=True,
        trigger_type=RetrainingTriggerType.DATA_VOLUME_THRESHOLD,
        trigger_metrics={},
    )
    assert report.passed is False
    vol_check = next(c for c in report.checks if c.name == "DATA_VOLUME")
    assert vol_check.passed is False
    assert "Asgari gerekli: 60" in vol_check.message


def test_retraining_quality_gate_fails_on_integrity_error() -> None:
    gate = RetrainingQualityGate(max_acceptable_integrity_errors=0)
    report = gate.evaluate(
        new_bars_count=80,
        integrity_error_count=2,
        is_leakage_free=True,
        trigger_type=RetrainingTriggerType.SCHEDULED_REVIEW,
        trigger_metrics={},
    )
    assert report.passed is False
    int_check = next(c for c in report.checks if c.name == "DATA_INTEGRITY")
    assert int_check.passed is False


def test_retraining_quality_gate_fails_on_leakage() -> None:
    gate = RetrainingQualityGate()
    report = gate.evaluate(
        new_bars_count=80,
        integrity_error_count=0,
        is_leakage_free=False,
        trigger_type=RetrainingTriggerType.PERFORMANCE_DEGRADATION,
        trigger_metrics={"metric_drop": 0.15},
    )
    assert report.passed is False
    leak_check = next(c for c in report.checks if c.name == "TEMPORAL_LEAKAGE_GUARD")
    assert leak_check.passed is False
    assert "hedef sızıntısı" in leak_check.message


def test_retraining_quality_gate_fails_on_insufficient_justification() -> None:
    gate = RetrainingQualityGate(min_drift_psi=0.20)
    # PSI sadece 0.05 ise kayma önemsizdir, yeniden eğitime gerek yoktur
    report = gate.evaluate(
        new_bars_count=80,
        integrity_error_count=0,
        is_leakage_free=True,
        trigger_type=RetrainingTriggerType.DATA_DRIFT,
        trigger_metrics={"psi": 0.05},
    )
    assert report.passed is False
    just_check = next(c for c in report.checks if c.name == "TRIGGER_JUSTIFICATION")
    assert just_check.passed is False
    assert "Yetersiz kayma" in just_check.message


def test_workflow_two_stage_human_approval_full_lifecycle() -> None:
    manager = RetrainingWorkflowManager()

    # 1. Talep oluşturma
    req = manager.create_request(
        model_name="bist30_mlp_v1",
        target_type="direction",
        target_horizon="5d",
        trigger_type=RetrainingTriggerType.DATA_DRIFT,
        trigger_reason="PSI değeri 0.27 olarak hesaplandı ve dağılım kayması uyarısı alındı.",
        trigger_metrics={"psi": 0.27},
    )
    assert req.status == RetrainingRequestStatus.PENDING_QUALITY_GATE

    # 2. Kalite kapısı değerlendirmesi (Geçer)
    gate_report = manager.evaluate_quality_gate(
        request_id=req.request_id,
        new_bars_count=120,
        integrity_error_count=0,
        is_leakage_free=True,
    )
    assert gate_report.passed is True
    req = manager.get_request(req.request_id)
    assert req.status == RetrainingRequestStatus.PENDING_TRAINING_APPROVAL

    # 3. Aşama 1 İnsan Onayı: Yetkisiz rol reddedilmeli
    with pytest.raises(UnauthorizedApprovalError):
        manager.approve_training_initiation(
            request_id=req.request_id,
            approver_id="user_123",
            approver_role="viewer",
            reason="Modeli güncelleyelim",
        )

    # 4. Aşama 1 İnsan Onayı: Yetkili ML mühendisi onaylar
    req = manager.approve_training_initiation(
        request_id=req.request_id,
        approver_id="ml_lead_ayse",
        approver_role="ml_lead",
        reason="Kalite kapısı geçti ve PSI artışı incelendi, eğitim onaylandı.",
    )
    assert req.status == RetrainingRequestStatus.TRAINING_APPROVED
    assert len(req.approvals) == 1
    assert req.approvals[0].stage == "TRAINING_INITIATION"
    assert req.approvals[0].decision == "APPROVED"

    # 5. Eğitimi başlatma ve tamamlama
    manager.start_training(req.request_id)
    req = manager.get_request(req.request_id)
    assert req.status == RetrainingRequestStatus.TRAINING_IN_PROGRESS

    manager.complete_training(
        request_id=req.request_id,
        trained_model_version="bist30_mlp_v2.0.0",
        notes="Eğitim 30 epoch tamamlandı, early stopping tetiklendi.",
    )
    req = manager.get_request(req.request_id)
    assert req.status == RetrainingRequestStatus.PENDING_PROMOTION_APPROVAL

    # 6. Aşama 2 İnsan Onayı: Gatekeeper/Shadow doğrulanmadan onaylanamaz
    with pytest.raises(QualityGateFailedError):
        manager.approve_promotion_to_active(
            request_id=req.request_id,
            approver_id="admin_mehmet",
            approver_role="system_admin",
            reason="Doğrulama yapılmadan canlıya alınmak istendi",
            gatekeeper_verified=False,
            shadow_verified=True,
        )

    # 7. Aşama 2 İnsan Onayı: Her iki doğrulama tamamlandıktan sonra onaylanır
    req = manager.approve_promotion_to_active(
        request_id=req.request_id,
        approver_id="admin_mehmet",
        approver_role="system_admin",
        reason="Model Gatekeeper naif baseline ve gölge testlerini başarıyla geçti.",
        gatekeeper_verified=True,
        shadow_verified=True,
    )
    assert req.status == RetrainingRequestStatus.PROMOTED_TO_ACTIVE
    assert len(req.approvals) == 2
    assert req.approvals[1].stage == "PROMOTION_TO_ACTIVE"
    assert req.approvals[1].decision == "APPROVED"


def test_workflow_governance_registry_integration() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        gov_registry = ModelGovernanceRegistry(base_dir=tmpdir)

        # Aday model oluştur
        gov_registry.register_candidate(
            model_id="bist_model_v2",
            target_type="direction",
            target_horizon="5d",
            registered_by="trainer_service",
        )

        wf_manager = RetrainingWorkflowManager(governance_registry=gov_registry)
        req = wf_manager.create_request(
            model_name="bist_model",
            target_type="direction",
            target_horizon="5d",
            trigger_type=RetrainingTriggerType.PERFORMANCE_DEGRADATION,
            trigger_reason="Son 20 gün directional pnl düşüşü tespit edildi.",
            trigger_metrics={"metric_drop": 0.12},
        )
        wf_manager.evaluate_quality_gate(
            req.request_id, new_bars_count=100, integrity_error_count=0, is_leakage_free=True
        )
        wf_manager.approve_training_initiation(
            req.request_id, "lead_1", "ml_lead", "Yeniden eğitim onaylandı."
        )
        wf_manager.start_training(req.request_id)
        wf_manager.complete_training(req.request_id, "bist_model_v2")

        # Canlıya terfi onayı verildiğinde registry'deki durum ACTIVE olmalı
        wf_manager.approve_promotion_to_active(
            request_id=req.request_id,
            approver_id="compliance_officer_ali",
            approver_role="compliance_officer",
            reason="Tüm regülasyon ve teknik testler onaylandı.",
            gatekeeper_verified=True,
            shadow_verified=True,
        )

        record = gov_registry.get_record("bist_model_v2")
        assert record is not None
        assert record.stage == ModelLifecycleStage.ACTIVE


def test_workflow_rejection_flows() -> None:
    manager = RetrainingWorkflowManager()
    req = manager.create_request(
        model_name="bist100_cnn",
        target_type="return",
        target_horizon="1d",
        trigger_type=RetrainingTriggerType.SCHEDULED_REVIEW,
        trigger_reason="Aylık teknik model incelemesi.",
    )
    manager.evaluate_quality_gate(
        req.request_id, new_bars_count=70, integrity_error_count=0, is_leakage_free=True
    )

    # 1. Aşama Reddi
    manager.reject_training_initiation(
        request_id=req.request_id,
        approver_id="lead_1",
        approver_role="ml_lead",
        reason="Mevcut model kararlı, eğitim ertelendi.",
    )
    assert req.status == RetrainingRequestStatus.TRAINING_REJECTED

    # Reddedilmiş talep üzerinde eğitim başlatılamaz
    with pytest.raises(InvalidWorkflowTransitionError):
        manager.start_training(req.request_id)


def test_spk_compliance_in_retraining_reasons() -> None:
    manager = RetrainingWorkflowManager()
    # SPK yasaklı kesin kazanç / tavsiye terimi içeren gerekçe reddedilmeli
    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        manager.create_request(
            model_name="bist30_model",
            target_type="direction",
            target_horizon="5d",
            trigger_type=RetrainingTriggerType.DATA_DRIFT,
            trigger_reason="Bu model kesinlikle garanti kazandırır.",
        )


def test_persistence_json_save_load() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = Path(tmpdir) / "retraining_requests.json"
        mgr1 = RetrainingWorkflowManager()

        req = mgr1.create_request(
            model_name="bist_persist_model",
            target_type="direction",
            target_horizon="5d",
            trigger_type=RetrainingTriggerType.DATA_VOLUME_THRESHOLD,
            trigger_reason="Yeni 90 adet seans barı eklendi.",
            trigger_metrics={"bars": 90},
        )
        mgr1.evaluate_quality_gate(
            req.request_id, new_bars_count=90, integrity_error_count=0, is_leakage_free=True
        )
        mgr1.approve_training_initiation(
            req.request_id, "admin_1", "system_admin", "Test onay kaydı."
        )
        mgr1.save_to_json(json_file)

        mgr2 = RetrainingWorkflowManager()
        mgr2.load_from_json(json_file)

        loaded_req = mgr2.get_request(req.request_id)
        assert loaded_req.request_id == req.request_id
        assert loaded_req.status == RetrainingRequestStatus.TRAINING_APPROVED
        assert loaded_req.quality_gate_report is not None
        assert loaded_req.quality_gate_report.passed is True
        assert len(loaded_req.approvals) == 1
        assert loaded_req.approvals[0].approver_id == "admin_1"
