"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 12 test paketi.

Güvenlik testi, kapalı beta ve kademeli yayın (Roadmap Final Adımı) kabul paketi.
Test kapsamı:
- RoadmapStepTwelveGatekeeper 6/6 tam uyum doğrulaması.
- Çok vektörlü güvenlik denetimi (RepoSecretAuditor, OWASP Top 10, CSP/HSTS, STRIDE).
- Kapalı beta erişim kapısı (ClosedBetaRolloutManager, allowlist, invite codes, KVKK/SPK onayı).
- Kademeli canary basamakları (%5->%100 GA) ve deterministik tuzlu kohort ataması.
- Otomatik devre kesici ve metrik tabanlı rollback tetikleyicileri (RollbackTriggerCoordinator).
- Kriptografik veritabanı yedeği (ChaCha20-Poly1305, DatabaseBackupEngine) ve olay tatbikatı.
- SPK zorunlu feragatnamesi ve tavsiye yasağı güvencesi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from stock_analysis.observability.deployment_flags import (
    HealthSnapshot,
    RollbackReason,
    RollbackThresholds,
    RollbackTriggerCoordinator,
)
from stock_analysis.release.release_five_canary_rollback import (
    compute_deterministic_user_bucket,
)
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP12_DISCLAIMER,
    ROADMAP_STEP12_STATUS,
    RoadmapStepTwelveGatekeeper,
    RoadmapStepTwelveStatus,
    generate_roadmap_step12_report,
)
from stock_analysis.security.closed_beta_gatekeeper import (
    ClosedBetaRolloutManager,
    RolloutStage,
)
from stock_analysis.security.database_backup import DatabaseBackupEngine
from stock_analysis.security.headers import (
    SecurityHeadersConfig,
    build_content_security_policy,
    build_hsts_header,
)
from stock_analysis.security.incident_response import (
    IncidentCategory,
    IncidentResponseCoordinator,
    IncidentSeverity,
)
from stock_analysis.security.secret_auditor import RepoSecretAuditor


class TestRoadmapOrderStepTwelveSuite:
    """FAZ-18 Yol Haritası Adım 12 kabul kapısı testleri."""

    def test_gatekeeper_full_evaluation_pass(self) -> None:
        """RoadmapStepTwelveGatekeeper tüm 6 kontrolü başarıyla geçer (6/6 PASS)."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        result = gatekeeper.evaluate()

        assert result.status == RoadmapStepTwelveStatus.PASS
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert len(result.checks) == 6
        assert ROADMAP_STEP12_STATUS.get() == 1.0

        check_ids = [c.check_id for c in result.checks]
        assert "ROADMAP-S12-01-SECURITY-AUDIT-AND-THREAT-MODEL" in check_ids
        assert "ROADMAP-S12-02-CLOSED-BETA-GATING-AND-ALLOWLIST" in check_ids
        assert "ROADMAP-S12-03-CANARY-LADDER-AND-DETERMINISTIC-COHORT" in check_ids
        assert "ROADMAP-S12-04-AUTOMATED-CIRCUIT-BREAKER-AND-ROLLBACK" in check_ids
        assert "ROADMAP-S12-05-CRYPTOGRAPHIC-BACKUP-AND-SANDBOX-RESTORE-DRILL" in check_ids
        assert "ROADMAP-S12-06-MANDATORY-SPK-DISCLAIMER-AND-NO-ADVICE-SAFEGUARDS" in check_ids

    def test_default_disclaimer_and_compliance(self) -> None:
        """Adım 12 feragatnamesi SPK mevzuatına uygun olmalı ve yasaklı terim içermemelidir."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        assert "SPK Mevzuatı Uyarınca" in gatekeeper.disclaimer
        assert "yatırım danışmanlığı" in gatekeeper.disclaimer
        assert "kapalı beta" in gatekeeper.disclaimer
        assert "canary" in gatekeeper.disclaimer

        result = gatekeeper.evaluate()
        assert result.disclaimer == DEFAULT_ROADMAP_STEP12_DISCLAIMER

    def test_check_security_audit_and_threat_model(self) -> None:
        """1. Kontrol: Çok vektörlü güvenlik taraması, OWASP/secret denetimi ve tehdit modeli."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        item = gatekeeper._check_security_audit_and_threat_model()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S12-01-SECURITY-AUDIT-AND-THREAT-MODEL"
        assert item.details["has_repo_auditor"] is True
        assert item.details["has_headers_config"] is True
        assert item.details["functional_pass"] is True

    def test_check_closed_beta_gating_and_allowlist(self) -> None:
        """2. Kontrol: Kapalı beta erişim kapısı, izin listesi ve davet kodu izolasyonu."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        item = gatekeeper._check_closed_beta_gating_and_allowlist()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S12-02-CLOSED-BETA-GATING-AND-ALLOWLIST"
        assert item.details["has_rollout_manager"] is True
        assert item.details["has_gatekeeper"] is True
        assert item.details["functional_pass"] is True

    def test_check_canary_ladder_and_deterministic_cohort(self) -> None:
        """3. Kontrol: Kademeli canary basamakları ve deterministik kohort dağılımı."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        item = gatekeeper._check_canary_ladder_and_deterministic_cohort()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S12-03-CANARY-LADDER-AND-DETERMINISTIC-COHORT"
        assert item.details["has_compute_bucket"] is True
        assert item.details["functional_pass"] is True

    def test_check_automated_circuit_breaker_and_rollback(self) -> None:
        """4. Kontrol: Otomatik devre kesici, hata/SLA eşik denetimi ve acil durum rollback."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        item = gatekeeper._check_automated_circuit_breaker_and_rollback()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S12-04-AUTOMATED-CIRCUIT-BREAKER-AND-ROLLBACK"
        assert item.details["has_coordinator"] is True
        assert item.details["functional_pass"] is True

    def test_check_cryptographic_backup_and_sandbox_restore_drill(self) -> None:
        """5. Kontrol: Kriptografik veritabanı yedeği, kum havuzu geri yükleme ve olay tatbikatı."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        item = gatekeeper._check_cryptographic_backup_and_sandbox_restore_drill()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S12-05-CRYPTOGRAPHIC-BACKUP-AND-SANDBOX-RESTORE-DRILL"
        assert item.details["has_engine"] is True
        assert item.details["has_incident_coord"] is True
        assert item.details["functional_pass"] is True

    def test_check_mandatory_spk_disclaimer_and_no_advice_safeguards(self) -> None:
        """6. Kontrol: SPK zorunlu yasal uyarı entegrasyonu ve tavsiye yasağı korumaları."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        item = gatekeeper._check_mandatory_spk_disclaimer_and_no_advice_safeguards()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S12-06-MANDATORY-SPK-DISCLAIMER-AND-NO-ADVICE-SAFEGUARDS"
        assert item.details["functional_pass"] is True

    def test_repo_secret_auditor_scan_behavior(self) -> None:
        """RepoSecretAuditor güvenli kodda temiz sonuç vermeli, sızıntıyı tespit etmelidir."""
        auditor = RepoSecretAuditor()
        safe_code = "def calculate_rsi(prices: list[float]) -> float:\n    return 55.4\n"
        findings_safe = auditor.scan_file_content(Path("src/stock_analysis/test.py"), safe_code)
        assert len(findings_safe) == 0

        # Private key sızıntısı tespiti
        leaked_key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0...\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        findings_leak = auditor.scan_file_content(Path("src/stock_analysis/bad.py"), leaked_key)
        assert len(findings_leak) > 0
        assert any("Private Key" in f.rule_name for f in findings_leak)

    def test_security_headers_and_csp(self) -> None:
        """SecurityHeadersConfig ve CSP yönergeleri beklenen güvenlik kalkanlarını sağlamalıdır."""
        config = SecurityHeadersConfig()
        assert config.enable_hsts is True
        assert config.csp_enabled is True

        csp = build_content_security_policy()
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

        hsts = build_hsts_header(max_age_seconds=31536000, include_subdomains=True)
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts

    def test_closed_beta_rollout_manager_lifecycle(self) -> None:
        """Kapalı beta yöneticisi allowlist, davet kodu ve etap ilerletme yaşam döngüsü."""
        mgr = ClosedBetaRolloutManager()
        assert mgr.current_stage == RolloutStage.CLOSED_BETA

        # 1. Allowlist'e ekleme
        user = "analyst@borsa-test.com"
        mgr.add_to_allowlist(user)
        eligible, reason = mgr.is_user_eligible(user)
        assert eligible is True
        assert "onaylandı" in reason.lower()

        # 2. Davet kodu üretimi ve doğrulama
        code = "BETA-TURKISH-2026"
        mgr.issue_invite_code(code=code, max_uses=2)
        valid, msg = mgr.validate_invite_code(code)
        assert valid is True

        # 3. Şartları onaylayarak davetiye kullanma
        redeemed, red_msg = mgr.redeem_invite_code(
            code=code,
            user_identifier="invitee@borsa-test.com",
            agreed_to_terms=True,
            agreed_to_spk_disclaimer=True,
        )
        assert redeemed is True
        assert "hoş geldiniz" in red_msg.lower()

        # 4. Etap ilerletme
        mgr.advance_stage(RolloutStage.CANARY_25)
        assert str(mgr.current_stage) == str(RolloutStage.CANARY_25)

    def test_canary_deterministic_bucket_and_sticky_cohort(self) -> None:
        """Kademeli canary dağıtımı SHA-256 tuzlu deterministik kohort ataması yapmalıdır."""
        u1 = "user_istanbul_001"
        u2 = "user_ankara_002"

        b1_run1 = compute_deterministic_user_bucket(u1, salt="bist_prod_salt")
        b1_run2 = compute_deterministic_user_bucket(u1, salt="bist_prod_salt")
        assert b1_run1 == b1_run2
        assert 0 <= b1_run1 <= 99

        b2 = compute_deterministic_user_bucket(u2, salt="bist_prod_salt")
        assert 0 <= b2 <= 99

    def test_rollback_trigger_coordinator_threshold_evaluation(self) -> None:
        """Otomatik devre kesici ve metrik tabanlı geri alma mantığı doğrulanmalıdır."""
        coordinator = RollbackTriggerCoordinator(
            default_thresholds=RollbackThresholds(
                max_error_rate_pct=1.0,
                max_p95_latency_ms=800.0,
            )
        )
        dep = coordinator.register_deployment(
            deployment_id="prod-dep-101",
            version="1.0.0",
            git_commit="git987654",
            canary_percentage=10.0,
        )

        # 1. Normal metrikler -> Rollback yok
        healthy = HealthSnapshot(
            error_rate_pct=0.1, p95_latency_ms=220.0, consecutive_health_failures=0
        )
        res_h = coordinator.assess_health(dep.deployment_id, healthy)
        assert res_h.should_rollback is False

        # 2. P95 Gecikme Aşımı (> 800ms) -> Rollback tetiklenmeli
        high_latency = HealthSnapshot(
            error_rate_pct=0.2, p95_latency_ms=950.0, consecutive_health_failures=0
        )
        res_l = coordinator.assess_health(dep.deployment_id, high_latency)
        assert res_l.should_rollback is True
        assert res_l.reason == RollbackReason.LATENCY_P95_BREACH

        # 3. Hata Oranı Aşımı (> %1.0) -> Rollback tetiklenmeli
        high_error = HealthSnapshot(
            error_rate_pct=3.2, p95_latency_ms=300.0, consecutive_health_failures=0
        )
        res_e = coordinator.assess_health(dep.deployment_id, high_error)
        assert res_e.should_rollback is True
        assert res_e.reason == RollbackReason.ERROR_RATE_BREACH

    def test_database_backup_engine_and_tamper_evident_hmac(self) -> None:
        """Kriptografik veritabanı yedeği ve HMAC kurcalama tespiti doğrulanmalıdır."""
        engine = DatabaseBackupEngine(pbkdf2_iterations=1000)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = Path(f.name)

        try:
            conn = sqlite3.connect(str(temp_db))
            conn.execute(
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, symbol TEXT, quantity INTEGER)"
            )
            conn.execute("INSERT INTO orders VALUES (1, 'THYAO', 100)")
            conn.execute("INSERT INTO orders VALUES (2, 'ASELS', 250)")
            conn.commit()
            conn.close()

            pkg = engine.create_backup(temp_db, backup_key="SuperSecretMasterKey32BytesLong!")
            assert pkg.manifest.auth_tag_hex is not None
            assert len(pkg.manifest.auth_tag_hex) == 64
            assert pkg.manifest.table_stats.get("orders") == 2
            assert len(pkg.ciphertext) > 0
        finally:
            if temp_db.exists():
                temp_db.unlink()

    def test_incident_response_coordinator_lifecycle(self) -> None:
        """NIST PICERL olay koordinatörü olay deklarasyonu ve yasal takvim doğrulaması."""
        coord = IncidentResponseCoordinator()
        inc = coord.declare_incident(
            title="Siber Güvenlik Tatbikatı — Adım 12",
            category=IncidentCategory.DATA_BREACH,
            severity=IncidentSeverity.SEV_2_HIGH,
            lead_commander="Baş Mühendis",
            affected_systems=["db_replica", "auth_proxy"],
            is_pii_affected=True,
        )

        assert inc.incident_id.startswith("INC-")
        assert inc.severity == IncidentSeverity.SEV_2_HIGH
        # KVKK (72 saat) ve SPK (24 saat) bildirim takvimleri eklenmeli
        regulatory_bodies = [d.regulatory_body for d in inc.legal_deadlines]
        assert "KVKK" in regulatory_bodies
        assert "SPK" in regulatory_bodies

    def test_generate_roadmap_step12_report(self) -> None:
        """generate_roadmap_step12_report geçerli Markdown formatında rapor üretmelidir."""
        gatekeeper = RoadmapStepTwelveGatekeeper()
        result = gatekeeper.evaluate()
        report = generate_roadmap_step12_report(result)

        assert "# Yol Haritası Uygulama Sırası — Adım 12 Denetim Raporu" in report
        assert "✅ PASS" in report
        assert "SPK Mevzuatı Uyarınca" in report
        assert "ROADMAP-S12-01-SECURITY-AUDIT-AND-THREAT-MODEL" in report
        assert "ROADMAP-S12-02-CLOSED-BETA-GATING-AND-ALLOWLIST" in report
        assert "ROADMAP-S12-03-CANARY-LADDER-AND-DETERMINISTIC-COHORT" in report
        assert "ROADMAP-S12-04-AUTOMATED-CIRCUIT-BREAKER-AND-ROLLBACK" in report
        assert "ROADMAP-S12-05-CRYPTOGRAPHIC-BACKUP-AND-SANDBOX-RESTORE-DRILL" in report
        assert "ROADMAP-S12-06-MANDATORY-SPK-DISCLAIMER-AND-NO-ADVICE-SAFEGUARDS" in report

    def test_gatekeeper_failure_on_missing_file(self, tmp_path: Path) -> None:
        """Eksik dosya durumunda gatekeeper FAIL durumuna geçmelidir."""
        empty_root = tmp_path / "empty_workspace"
        empty_root.mkdir()

        gatekeeper = RoadmapStepTwelveGatekeeper(workspace_root=empty_root)
        result = gatekeeper.evaluate()

        assert result.status == RoadmapStepTwelveStatus.FAIL
        assert result.passed_checks < 6
        assert result.failed_checks > 0
