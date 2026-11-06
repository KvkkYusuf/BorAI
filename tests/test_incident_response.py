"""Unit and integration tests for security incident response workflows.

Tests NIST SP 800-61 / ISO 27035 PICERL lifecycle, contact directory, containment,
KVKK 72-hour notifications, and post-mortem generation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_analysis.security.incident_response import (
    SEVERITY_SLAS,
    VALID_STAGE_TRANSITIONS,
    ContactRole,
    ContainmentAction,
    IncidentCategory,
    IncidentContactDirectory,
    IncidentResponseCoordinator,
    IncidentSeverity,
    IncidentStage,
    IncidentWorkflowError,
    LegalComplianceDeadline,
)


class TestIncidentContactDirectory:
    """Tests for SIRT personnel, escalation paths, and regulatory contacts."""

    def test_default_directory_canonical_roles(self) -> None:
        directory = IncidentContactDirectory.get_default_directory()

        for role in (
            ContactRole.INCIDENT_COMMANDER,
            ContactRole.SECURITY_LEAD,
            ContactRole.LEGAL_COMPLIANCE,
            ContactRole.COMMUNICATIONS_LEAD,
            ContactRole.INFRASTRUCTURE_LEAD,
        ):
            contact = directory.get_primary(role)
            assert contact.name != ""
            assert contact.role == role
            assert "@" in contact.email
            assert contact.phone != ""
            assert contact.backup_name is not None

        chain = directory.get_escalation_chain(ContactRole.INCIDENT_COMMANDER)
        assert len(chain) >= 1

    def test_external_authorities_configured(self) -> None:
        directory = IncidentContactDirectory.get_default_directory()
        authorities = directory.external_authorities

        assert "KVKK" in authorities
        assert authorities["KVKK"]["sla_hours"] == "72"
        assert "SPK" in authorities
        assert authorities["SPK"]["sla_hours"] == "24"
        assert "USOM" in authorities

    def test_missing_role_raises_error(self) -> None:
        empty_dir = IncidentContactDirectory()
        with pytest.raises(
            IncidentWorkflowError, match="Tanımlı INCIDENT_COMMANDER sorumlusu bulunamadı"
        ):
            empty_dir.get_primary(ContactRole.INCIDENT_COMMANDER)


class TestIncidentDeclarationAndDeadlines:
    """Tests for declaring incidents and calculating regulatory deadlines."""

    def test_declare_critical_pii_incident(self) -> None:
        coordinator = IncidentResponseCoordinator()
        detected_time = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)

        incident = coordinator.declare_incident(
            title="Kullanıcı Veritabanı Yetkisiz Erişim Şüphesi",
            category=IncidentCategory.DATA_BREACH,
            severity=IncidentSeverity.SEV_1_CRITICAL,
            lead_commander="Baş Güvenlik Sorumlusu (CISO)",
            affected_systems=["db_primary", "auth_service"],
            is_pii_affected=True,
            detected_at=detected_time,
            affected_user_count=150,
        )

        today_str = datetime.now(UTC).strftime("%Y%m%d")
        assert incident.incident_id.startswith(f"INC-{today_str}-")
        assert incident.current_stage == IncidentStage.IDENTIFICATION
        assert incident.is_pii_affected is True
        assert incident.affected_user_count == 150
        assert len(incident.timeline) == 1

        # Check KVKK 72-hour deadline
        kvkk_deadlines = [d for d in incident.legal_deadlines if d.regulatory_body == "KVKK"]
        assert len(kvkk_deadlines) == 1
        kvkk_dl = kvkk_deadlines[0]
        assert kvkk_dl.deadline_hours == 72
        assert kvkk_dl.due_at == detected_time + timedelta(hours=72)
        assert kvkk_dl.status == "PENDING"

        # Check SPK and USOM 24-hour deadlines for SEV-1
        spk_deadlines = [d for d in incident.legal_deadlines if d.regulatory_body == "SPK"]
        assert len(spk_deadlines) == 1
        assert spk_deadlines[0].due_at == detected_time + timedelta(hours=24)

    def test_declare_sev3_non_pii_incident(self) -> None:
        coordinator = IncidentResponseCoordinator()
        incident = coordinator.declare_incident(
            title="Genel Arama Ucu Yüksek Trafik Anomalisi",
            category=IncidentCategory.DENIAL_OF_SERVICE,
            severity=IncidentSeverity.SEV_3_MEDIUM,
            lead_commander="DevOps Nöbetçisi",
            affected_systems=["api_gateway"],
            is_pii_affected=False,
        )

        assert incident.severity == IncidentSeverity.SEV_3_MEDIUM
        assert incident.is_pii_affected is False
        assert len(incident.legal_deadlines) == 0

    def test_legal_deadline_overdue_and_submission(self) -> None:
        now = datetime.now(UTC)
        deadline = LegalComplianceDeadline(
            regulatory_body="KVKK",
            deadline_hours=72,
            due_at=now - timedelta(hours=1),
            status="PENDING",
        )
        assert deadline.is_overdue() is True

        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Test Breach",
            category=IncidentCategory.DATA_BREACH,
            severity=IncidentSeverity.SEV_1_CRITICAL,
            lead_commander="CISO",
            affected_systems=["core"],
            is_pii_affected=True,
        )

        sub = coordinator.record_legal_submission(
            incident_id=inc.incident_id,
            regulatory_body="KVKK",
            submission_reference="KVKK-2026-REF-99881",
            submitted_by="Hukuk Müşaviri",
            notes="Çevrimiçi bildirim portalından iletildi.",
        )
        assert sub.status == "SUBMITTED"
        assert sub.submission_reference == "KVKK-2026-REF-99881"
        assert sub.is_overdue() is False


class TestPicerlWorkflow:
    """Tests for the PICERL lifecycle state machine transitions."""

    def test_full_picerl_lifecycle(self) -> None:
        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Kötücül Bağımlılık Alarmı",
            category=IncidentCategory.SUPPLY_CHAIN,
            severity=IncidentSeverity.SEV_2_HIGH,
            lead_commander="CISO",
            affected_systems=["backend_api"],
        )
        assert inc.current_stage == IncidentStage.IDENTIFICATION

        # Stage 1: Transition to CONTAINMENT
        inc = coordinator.transition_stage(
            inc.incident_id,
            IncidentStage.CONTAINMENT,
            actor="SecOps Lead",
            notes="CI/CD boru hattı durduruldu ve servis izole edildi.",
        )
        assert inc.current_stage == IncidentStage.CONTAINMENT

        # Stage 2: Transition to ERADICATION
        inc = coordinator.transition_stage(
            inc.incident_id,
            IncidentStage.ERADICATION,
            actor="DevOps Lead",
            notes="Şüpheli paket kaldırıldı ve kilit dosyası yenilendi.",
        )
        assert inc.current_stage == IncidentStage.ERADICATION

        # Stage 3: Transition to RECOVERY
        inc = coordinator.transition_stage(
            inc.incident_id,
            IncidentStage.RECOVERY,
            actor="SRE Lead",
            notes="Konteynerler temiz imajla yeniden dağıtıldı ve sağlık testleri yeşil.",
        )
        assert inc.current_stage == IncidentStage.RECOVERY

        # Stage 4: Transition to LESSONS_LEARNED
        inc = coordinator.transition_stage(
            inc.incident_id,
            IncidentStage.LESSONS_LEARNED,
            actor="CISO",
            notes="Post-mortem analizi tamamlandı ve aksiyonlar atandı.",
        )
        assert inc.current_stage == IncidentStage.LESSONS_LEARNED
        assert inc.closed_at is not None
        assert len(inc.timeline) == 5

    def test_invalid_stage_transition_raises_error(self) -> None:
        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Geçersiz Geçiş Testi",
            category=IncidentCategory.SUSPICIOUS_ACTIVITY,
            severity=IncidentSeverity.SEV_3_MEDIUM,
            lead_commander="CISO",
            affected_systems=["api"],
        )

        # Cannot skip from IDENTIFICATION straight to RECOVERY
        with pytest.raises(
            IncidentWorkflowError, match="Geçersiz aşama geçişi: IDENTIFICATION -> RECOVERY"
        ):
            coordinator.transition_stage(
                inc.incident_id,
                IncidentStage.RECOVERY,
                actor="Operator",
                notes="Geçersiz atlama",
            )

    def test_false_positive_early_closure(self) -> None:
        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Sahte Alarm Triage",
            category=IncidentCategory.SUSPICIOUS_ACTIVITY,
            severity=IncidentSeverity.SEV_4_LOW,
            lead_commander="SecOps Analist",
            affected_systems=["waf"],
        )

        coordinator.transition_stage(
            inc.incident_id,
            IncidentStage.LESSONS_LEARNED,
            actor="SecOps Analist",
            notes="Trafiğin meşru test aracı olduğu anlaşıldı; vaka kapatıldı.",
        )
        assert inc.current_stage == IncidentStage.LESSONS_LEARNED
        assert inc.closed_at is not None


class TestContainmentActions:
    """Tests for executing and tracking incident containment actions."""

    def test_apply_containment_measures(self) -> None:
        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Kimlik Ele Geçirme Dalgası",
            category=IncidentCategory.ACCOUNT_TAKEOVER,
            severity=IncidentSeverity.SEV_1_CRITICAL,
            lead_commander="CISO",
            affected_systems=["auth_service"],
        )

        coordinator.transition_stage(
            inc.incident_id, IncidentStage.CONTAINMENT, "CISO", "Sınırlama başlatıldı"
        )

        res1 = coordinator.apply_containment_action(
            inc.incident_id,
            ContainmentAction.QUARANTINE_IP,
            operator="SecOps Lead",
            details="198.51.100.22 ve 203.0.113.45 bloklandı.",
        )
        assert res1.success is True
        assert res1.action == ContainmentAction.QUARANTINE_IP

        res2 = coordinator.apply_containment_action(
            inc.incident_id,
            ContainmentAction.REVOKE_ALL_SESSIONS,
            operator="Auth Lead",
            details="Şüpheli 45 kullanıcının tüm oturum çerezleri iptal edildi.",
        )
        assert res2.action == ContainmentAction.REVOKE_ALL_SESSIONS

        res3 = coordinator.apply_containment_action(
            inc.incident_id,
            ContainmentAction.ACTIVATE_KILL_SWITCH,
            operator="CISO",
            details="Giriş uç noktaları geçici bakım moduna alındı.",
        )
        assert res3.action == ContainmentAction.ACTIVATE_KILL_SWITCH

        assert len(inc.containment_actions) == 3


class TestReportsAndAdvisories:
    """Tests for generating KVKK breach forms, user advisories, and post-mortems."""

    def test_generate_kvkk_breach_report(self) -> None:
        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Hassas Veri İhlali",
            category=IncidentCategory.DATA_BREACH,
            severity=IncidentSeverity.SEV_1_CRITICAL,
            lead_commander="CISO",
            affected_systems=["db_replica"],
            is_pii_affected=True,
            affected_user_count=85,
        )

        coordinator.apply_containment_action(
            inc.incident_id,
            ContainmentAction.READ_ONLY_LOCKDOWN,
            operator="DBA",
            details="Replica tecrit edildi.",
        )

        report = coordinator.generate_kvkk_breach_report(inc.incident_id)
        assert report["form_standard"] == "KVKK-VERI-IHLAL-BILDIRIM-FORMU-V1"
        assert report["olay_kimligi"] == inc.incident_id
        assert report["tahmini_etkilenen_kisi_sayisi"] == 85
        assert "Uyum ve Hukuk Müşaviri" in report["irtibat_kisisu"]["ad_soyad"]
        assert len(report["alinan_teknik_ve_idari_tedbirler"]) == 1

    def test_generate_user_advisory_notice(self) -> None:
        coordinator = IncidentResponseCoordinator()
        inc = coordinator.declare_incident(
            title="Şüpheli Giriş Denemesi",
            category=IncidentCategory.ACCOUNT_TAKEOVER,
            severity=IncidentSeverity.SEV_2_HIGH,
            lead_commander="CISO",
            affected_systems=["web_app"],
        )

        notice = coordinator.generate_user_advisory_notice(inc.incident_id)
        assert "GÜVENLİK BİLGİLENDİRMESİ VE KULLANICI UYARISI" in notice
        assert inc.incident_id in notice
        assert "Argon2id" in notice
        assert "İki Aşamalı Doğrulama (TOTP / MFA)" in notice
        assert "al/sat tavsiyesi" not in notice.lower()  # Complies with finance/legal rules

    def test_generate_post_mortem_report(self) -> None:
        coordinator = IncidentResponseCoordinator()
        detected_time = datetime(2026, 10, 25, 8, 0, 0, tzinfo=UTC)
        inc = coordinator.declare_incident(
            title="Kritik Veritabanı Kesintisi",
            category=IncidentCategory.INTEGRITY_COMPROMISE,
            severity=IncidentSeverity.SEV_1_CRITICAL,
            lead_commander="CISO",
            affected_systems=["db_primary"],
            detected_at=detected_time,
        )

        coordinator.transition_stage(
            inc.incident_id, IncidentStage.CONTAINMENT, "SecOps", "Tecrit sağlandı"
        )
        coordinator.transition_stage(
            inc.incident_id, IncidentStage.ERADICATION, "SRE", "Zararlı işlem iptal edildi"
        )
        coordinator.transition_stage(
            inc.incident_id, IncidentStage.RECOVERY, "DBA", "Geri yükleme tamamlandı"
        )
        coordinator.transition_stage(
            inc.incident_id, IncidentStage.LESSONS_LEARNED, "CISO", "Vaka kapandı"
        )

        post_mortem = coordinator.generate_post_mortem_report(
            inc.incident_id,
            root_cause=("Birincil düğüm üzerinde hafıza sızıntısı ve bağlantı havuzu tükenmesi."),
            remediation_actions=[
                "Bağlantı havuzu tavanını sınırla ve sızıntı alarmlarını sıkılaştır.",
                "Otomatik yeniden başlatma watchdog'u kur.",
                "Failover tatbikatını iki haftada bir tekrarla.",
            ],
        )

        assert f"(Post-Mortem): {inc.incident_id}" in post_mortem
        assert "Kök Neden Analizi" in post_mortem
        assert "Olay Kronolojisi" in post_mortem
        assert "İyileştirme ve Önleyici Aksiyonlar" in post_mortem
        assert "Bağlantı havuzu tavanını sınırla" in post_mortem
        assert "ONAYLANDI VE ARŞİVLENDİ" in post_mortem


class TestSlaAndConstants:
    """Tests for SLAs and workflow transition constants."""

    def test_severity_slas(self) -> None:
        assert SEVERITY_SLAS[IncidentSeverity.SEV_1_CRITICAL]["response_minutes"] == 15
        assert SEVERITY_SLAS[IncidentSeverity.SEV_2_HIGH]["response_minutes"] == 30
        assert SEVERITY_SLAS[IncidentSeverity.SEV_3_MEDIUM]["response_minutes"] == 120
        assert SEVERITY_SLAS[IncidentSeverity.SEV_4_LOW]["response_minutes"] == 1440

    def test_valid_stage_transitions_mapping(self) -> None:
        assert IncidentStage.PREPARATION in VALID_STAGE_TRANSITIONS
        assert IncidentStage.IDENTIFICATION in VALID_STAGE_TRANSITIONS
        assert IncidentStage.CONTAINMENT in VALID_STAGE_TRANSITIONS
        assert IncidentStage.ERADICATION in VALID_STAGE_TRANSITIONS
        assert IncidentStage.RECOVERY in VALID_STAGE_TRANSITIONS
        assert IncidentStage.LESSONS_LEARNED in VALID_STAGE_TRANSITIONS
