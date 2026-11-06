from pathlib import Path

import pytest

from stock_analysis.models.gatekeeper import PublishingDecision
from stock_analysis.models.governance import (
    GovernanceIntegrityError,
    ModelGovernanceRegistry,
    ModelLifecycleStage,
    ModelStageTransitionError,
)
from stock_analysis.models.shadow_mode import ShadowPromotionDecision


@pytest.fixture
def temp_registry(tmp_path: Path) -> ModelGovernanceRegistry:
    return ModelGovernanceRegistry(base_dir=tmp_path / "models")


def test_candidate_registration_and_retrieval(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    record = temp_registry.register_candidate(
        model_id="bist_mlp_v1.0.0",
        target_type="direction",
        target_horizon="5d",
        registered_by="ci_pipeline",
        metadata={"features": ["rsi_14", "sma_20"]},
    )

    assert record.model_id == "bist_mlp_v1.0.0"
    assert record.stage == ModelLifecycleStage.CANDIDATE
    assert record.target_type == "direction"
    assert record.target_horizon == "5d"
    assert record.registered_by == "ci_pipeline"
    assert len(record.audit_trail) == 1
    assert record.audit_trail[0]["action"] == "REGISTER_CANDIDATE"

    # Geri getirme kontrolü
    fetched = temp_registry.get_record("bist_mlp_v1.0.0")
    assert fetched is not None
    assert fetched.model_id == "bist_mlp_v1.0.0"
    assert fetched.stage == ModelLifecycleStage.CANDIDATE


def test_duplicate_registration_prevented(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    temp_registry.register_candidate(
        model_id="model_dup",
        target_type="return",
        target_horizon="1d",
    )
    with pytest.raises(GovernanceIntegrityError, match="zaten kayıtlı"):
        temp_registry.register_candidate(
            model_id="model_dup",
            target_type="return",
            target_horizon="1d",
        )


def test_full_lifecycle_happy_path(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    # 1. Register candidate
    temp_registry.register_candidate(
        model_id="model_happy_path",
        target_type="log_return",
        target_horizon="1d",
    )

    # 2. Mark evaluated
    evaluated = temp_registry.mark_evaluated(
        model_id="model_happy_path",
        performed_by="eval_runner",
        metrics_summary={"mae": 0.012, "directional_accuracy": 0.58},
    )
    assert evaluated.stage == ModelLifecycleStage.EVALUATED
    assert evaluated.metadata["evaluation_metrics"]["mae"] == 0.012

    # 3. Start shadow mode
    shadow = temp_registry.start_shadow_mode(
        model_id="model_happy_path",
        performed_by="shadow_bot",
    )
    assert shadow.stage == ModelLifecycleStage.SHADOW

    # 4. Approve model with passing decisions
    gate_decision = PublishingDecision(
        model_name="model_happy_path",
        symbol="THYAO",
        is_approved=True,
        beats_baseline=True,
        beats_friction_cost=True,
        rejection_reasons=[],
        baseline_report=None,
        backtest_report=None,
        simulation_result=None,
        recommended_forecast_enabled=True,
        user_facing_status="ACTIVE",
        user_facing_message="Model yayın onayı aldı.",
    )
    shadow_decision = ShadowPromotionDecision.PROCEED_TO_LIVE

    approved = temp_registry.approve_model(
        model_id="model_happy_path",
        approved_by="risk_officer_ali",
        approval_notes="Model tüm testleri ve gölge modunu başarıyla tamamladı.",
        gatekeeper_decision=gate_decision,
        shadow_decision=shadow_decision,
    )
    assert approved.stage == ModelLifecycleStage.APPROVED
    assert approved.approved_by == "risk_officer_ali"
    assert approved.gatekeeper_passed is True
    assert approved.shadow_mode_passed is True

    # 5. Promote to active
    active = temp_registry.promote_to_active(
        model_id="model_happy_path",
        promoted_by="lead_ml_engineer",
    )
    assert active.stage == ModelLifecycleStage.ACTIVE

    # Doğrudan get_active_model ile sorgula
    current_active = temp_registry.get_active_model(target_type="log_return", target_horizon="1d")
    assert current_active is not None
    assert current_active.model_id == "model_happy_path"

    # Audit trail uzunluğu (REGISTER -> EVALUATED -> SHADOW -> APPROVED -> ACTIVE = 5 adım)
    assert len(current_active.audit_trail) == 5


def test_invalid_stage_transition_disallowed(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    temp_registry.register_candidate(
        model_id="model_skip",
        target_type="volatility",
        target_horizon="20d",
    )

    # CANDIDATE doğrudan ACTIVE yapılamaz!
    with pytest.raises(ModelStageTransitionError, match="Yalnızca APPROVED"):
        temp_registry.promote_to_active(
            model_id="model_skip",
            promoted_by="attacker",
        )

    # CANDIDATE doğrudan APPROVED yapılamaz
    with pytest.raises(ModelStageTransitionError, match="Geçersiz durum geçişi"):
        temp_registry.approve_model(
            model_id="model_skip",
            approved_by="manager",
            approval_notes="Direct approval attempt",
        )


def test_approval_fails_if_gatekeeper_or_shadow_fails(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    temp_registry.register_candidate("model_fail_gate", "return", "5d")
    temp_registry.mark_evaluated("model_fail_gate")
    temp_registry.start_shadow_mode("model_fail_gate")

    # Gatekeeper başarısız ise
    failed_gate = PublishingDecision(
        model_name="model_fail_gate",
        symbol="THYAO",
        is_approved=False,
        beats_baseline=False,
        beats_friction_cost=False,
        rejection_reasons=["Model naif baseline'ı geçemedi"],
        baseline_report=None,
        backtest_report=None,
        simulation_result=None,
        recommended_forecast_enabled=False,
        user_facing_status="REJECTED",
        user_facing_message="Model reddedildi.",
    )

    with pytest.raises(
        GovernanceIntegrityError, match="Gatekeeper yayın kriterlerini karşılamıyor"
    ):
        temp_registry.approve_model(
            model_id="model_fail_gate",
            approved_by="validator",
            approval_notes="Onay denemesi",
            gatekeeper_decision=failed_gate,
        )

    # Shadow mode başarısız / uzatma gerekiyorsa
    passing_gate = PublishingDecision(
        model_name="model_fail_gate",
        symbol="THYAO",
        is_approved=True,
        beats_baseline=True,
        beats_friction_cost=True,
        rejection_reasons=[],
        baseline_report=None,
        backtest_report=None,
        simulation_result=None,
        recommended_forecast_enabled=True,
        user_facing_status="ACTIVE",
        user_facing_message="Model geçerli.",
    )
    with pytest.raises(GovernanceIntegrityError, match="Gölge Modu terfi kriterini karşılamıyor"):
        temp_registry.approve_model(
            model_id="model_fail_gate",
            approved_by="validator",
            approval_notes="Onay denemesi",
            gatekeeper_decision=passing_gate,
            shadow_decision=ShadowPromotionDecision.EXTEND_SHADOW,
        )


def test_rejection_requires_reason_and_sets_stage(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    temp_registry.register_candidate("model_to_reject", "direction", "1d")
    temp_registry.mark_evaluated("model_to_reject")

    with pytest.raises(GovernanceIntegrityError, match="açık bir gerekçe"):
        temp_registry.reject_model(
            model_id="model_to_reject",
            rejected_by="auditor",
            rejection_reason="",
        )

    rejected = temp_registry.reject_model(
        model_id="model_to_reject",
        rejected_by="auditor",
        rejection_reason="Test setinde yüksek overfitting tespit edildi.",
    )
    assert rejected.stage == ModelLifecycleStage.REJECTED
    assert rejected.rejection_reason == "Test setinde yüksek overfitting tespit edildi."


def test_active_model_exclusivity_and_auto_archiving(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    """Yeni bir model aktifleşince aynı hedefteki eski model otomatik arşivlenir."""
    # Model 1
    temp_registry.register_candidate("m1", "return", "1d")
    temp_registry.mark_evaluated("m1")
    temp_registry.start_shadow_mode("m1")
    temp_registry.approve_model("m1", "admin", "M1 Onaylandı")
    temp_registry.promote_to_active("m1", "admin")

    assert temp_registry.get_active_model("return", "1d") is not None
    active_record = temp_registry.get_active_model("return", "1d")
    assert active_record is not None and active_record.model_id == "m1"

    # Model 2
    temp_registry.register_candidate("m2", "return", "1d")
    temp_registry.mark_evaluated("m2")
    temp_registry.start_shadow_mode("m2")
    temp_registry.approve_model("m2", "admin", "M2 Onaylandı")
    temp_registry.promote_to_active("m2", "admin")

    # Artık m2 aktif olmalı, m1 arşivlenmiş olmalı
    active_model = temp_registry.get_active_model("return", "1d")
    assert active_model is not None and active_model.model_id == "m2"

    m1_rec = temp_registry.get_record("m1")
    assert m1_rec is not None and m1_rec.stage == ModelLifecycleStage.ARCHIVED

    # Yalnızca bir tane aktif model olmalı
    active_list = temp_registry.list_records(
        stage=ModelLifecycleStage.ACTIVE,
        target_type="return",
        target_horizon="1d",
    )
    assert len(active_list) == 1
    assert active_list[0].model_id == "m2"


def test_rollback_production_workflow(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    # m1 aktif yapılmış, sonra m2 gelmiş ve m1 arşivlenmişti
    temp_registry.register_candidate("m1", "direction", "5d")
    temp_registry.mark_evaluated("m1")
    temp_registry.start_shadow_mode("m1")
    temp_registry.approve_model("m1", "admin", "M1 Onaylandı")
    temp_registry.promote_to_active("m1", "admin")

    temp_registry.register_candidate("m2", "direction", "5d")
    temp_registry.mark_evaluated("m2")
    temp_registry.start_shadow_mode("m2")
    temp_registry.approve_model("m2", "admin", "M2 Onaylandı")
    temp_registry.promote_to_active("m2", "admin")

    active_before = temp_registry.get_active_model("direction", "5d")
    assert active_before is not None and active_before.model_id == "m2"

    # M2'de canlıda anomali görüldü, M1'e geri dön (rollback)
    rolled_back = temp_registry.rollback_production(
        target_type="direction",
        target_horizon="5d",
        rollback_to_model_id="m1",
        executed_by="lead_sre",
        reason="M2 canlıda veri kayması nedeniyle beklenmedik sinyaller üretti.",
    )

    assert rolled_back.stage == ModelLifecycleStage.ACTIVE
    assert rolled_back.model_id == "m1"

    # M2 arşivlenmiş olmalı
    m2_rec = temp_registry.get_record("m2")
    assert m2_rec is not None and m2_rec.stage == ModelLifecycleStage.ARCHIVED

    # Aktif model sorgusu M1 dönmeli
    current_active = temp_registry.get_active_model("direction", "5d")
    assert current_active is not None and current_active.model_id == "m1"


def test_no_silent_fallback_when_no_active_model(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    """Aktif onaylı model yoksa kesinlikle sessiz fallback yapılmaz; None döner."""
    active = temp_registry.get_active_model(target_type="non_existent", target_horizon="1d")
    assert active is None


def test_security_validations(
    temp_registry: ModelGovernanceRegistry,
) -> None:
    with pytest.raises(GovernanceIntegrityError, match="Güvensiz"):
        temp_registry.register_candidate(
            model_id="../../etc/passwd", target_type="t", target_horizon="h"
        )

    with pytest.raises(GovernanceIntegrityError, match="Güvensiz"):
        temp_registry.get_record("../bad_id")
