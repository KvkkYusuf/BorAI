"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 11 test paketi.

Model izleme ve gölge mod (Shadow Mode) kabul paketi.
Test kapsamı:
- RoadmapStepElevenGatekeeper 6/6 tam uyum doğrulaması.
- Gölge mod takip altyapısı (ShadowModeTracker, ShadowObservation, canlı kullanıcı izolasyonu).
- Model çıkarım gecikmesi (latency SLA, p50/p95/p99) ve sıfır hata toleransı.
- Çevrimdışı doğrulama vs canlı sonuç ayrımı ve dağılım sapması takibi.
- Naif baseline karşılaştırması ve terfi/karantina kararları (ShadowPromotionDecision).
- Epistemik belirsizlik ve tahmin aralığı gerçekleşme kapsaması (coverage ratio).
- SPK yasal uyarı feragatnamesi ve al/sat tavsiyesi olmama güvencesi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_analysis.models.shadow_mode import (
    ShadowModeConfig,
    ShadowModeTracker,
    ShadowPromotionDecision,
)
from stock_analysis.observability.model_performance import (
    LivePredictionRecord,
    ModelHealthStatus,
    ModelPerformanceTracker,
    OfflineValidationMetrics,
)
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP11_DISCLAIMER,
    ROADMAP_STEP11_STATUS,
    RoadmapStepElevenGatekeeper,
    RoadmapStepElevenStatus,
    generate_roadmap_step11_report,
)


class TestRoadmapOrderStepElevenSuite:
    """FAZ-18 Yol Haritası Adım 11 kabul kapısı testleri."""

    def test_gatekeeper_full_evaluation_pass(self) -> None:
        """RoadmapStepElevenGatekeeper tüm 6 kontrolü başarıyla geçer (6/6 PASS)."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        result = gatekeeper.evaluate()

        assert result.status == RoadmapStepElevenStatus.PASS
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert len(result.checks) == 6
        assert ROADMAP_STEP11_STATUS.get() == 1.0

        check_ids = [c.check_id for c in result.checks]
        assert "ROADMAP-S11-01-SHADOW-MODE-ISOLATION-AND-REGISTRY" in check_ids
        assert "ROADMAP-S11-02-INFERENCE-LATENCY-AND-SLA-TRACKING" in check_ids
        assert "ROADMAP-S11-03-OFFLINE-VS-LIVE-DRIFT-AND-HEALTH-STATUS" in check_ids
        assert "ROADMAP-S11-04-NAIVE-BASELINE-COMPARISON-AND-PROMOTION-DECISIONS" in check_ids
        assert "ROADMAP-S11-05-EPISTEMIC-UNCERTAINTY-AND-INTERVAL-COVERAGE" in check_ids
        assert "ROADMAP-S11-06-MANDATORY-SPK-DISCLAIMER-AND-NO-ADVICE-SAFEGUARDS" in check_ids

    def test_default_disclaimer_and_compliance(self) -> None:
        """Adım 11 feragatnamesi SPK mevzuatına uygun olmalı ve yasaklı terim içermemelidir."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        assert "SPK Mevzuatı Uyarınca" in gatekeeper.disclaimer
        assert "yatırım danışmanlığı" in gatekeeper.disclaimer
        assert "gölge mod" in gatekeeper.disclaimer

        result = gatekeeper.evaluate()
        assert result.disclaimer == DEFAULT_ROADMAP_STEP11_DISCLAIMER

    def test_check_shadow_mode_isolation_and_registry(self) -> None:
        """1. Kontrol: Gölge modu takibi, veri modelleri ve canlı izolasyonu denetimi."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        item = gatekeeper._check_shadow_mode_isolation_and_registry()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S11-01-SHADOW-MODE-ISOLATION-AND-REGISTRY"
        assert item.details["has_tracker"] is True
        assert item.details["has_observation"] is True
        assert item.details["functional_pass"] is True

    def test_check_inference_latency_and_sla_tracking(self) -> None:
        """2. Kontrol: Model çıkarım gecikmesi (latency SLA) ve performans takibi."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        item = gatekeeper._check_inference_latency_and_sla_tracking()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S11-02-INFERENCE-LATENCY-AND-SLA-TRACKING"
        assert item.details["has_latency_p95"] is True
        assert item.details["functional_pass"] is True

    def test_check_offline_vs_live_drift_and_health_status(self) -> None:
        """3. Kontrol: Çevrimdışı doğrulama vs canlı sonuç ayrımı ve dağılım sapması takibi."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        item = gatekeeper._check_offline_vs_live_drift_and_health_status()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S11-03-OFFLINE-VS-LIVE-DRIFT-AND-HEALTH-STATUS"
        assert item.details["has_tracker"] is True
        assert item.details["has_health_status"] is True
        assert item.details["functional_pass"] is True

    def test_check_naive_baseline_comparison_and_promotion_decisions(self) -> None:
        """4. Kontrol: Naif baseline karşılaştırması ve gölge terfi / karantina kararları."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        item = gatekeeper._check_naive_baseline_comparison_and_promotion_decisions()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S11-04-NAIVE-BASELINE-COMPARISON-AND-PROMOTION-DECISIONS"
        assert item.details["has_beat_baseline"] is True
        assert item.details["functional_pass"] is True

    def test_check_epistemic_uncertainty_and_interval_coverage(self) -> None:
        """5. Kontrol: Epistemik belirsizlik ve tahmin aralıkları gerçekleşme kapsaması."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        item = gatekeeper._check_epistemic_uncertainty_and_interval_coverage()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S11-05-EPISTEMIC-UNCERTAINTY-AND-INTERVAL-COVERAGE"
        assert item.details["has_coverage_field"] is True
        assert item.details["functional_pass"] is True

    def test_check_mandatory_spk_disclaimer_and_no_advice_safeguards(self) -> None:
        """6. Kontrol: SPK zorunlu yasal uyarı entegrasyonu ve tavsiye yasağı korumaları."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        item = gatekeeper._check_mandatory_spk_disclaimer_and_no_advice_safeguards()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S11-06-MANDATORY-SPK-DISCLAIMER-AND-NO-ADVICE-SAFEGUARDS"
        assert item.details["functional_pass"] is True

    def test_shadow_mode_tracker_lifecycle(self) -> None:
        """Gölge mod takipçisi gözlem kaydı, gerçekleşme eşleme ve özetleme yaşam döngüsü."""
        cfg = ShadowModeConfig(min_observations=5, min_shadow_duration_days=1)
        tracker = ShadowModeTracker(model_version="ML-v1.0", config=cfg)

        now = datetime.now(UTC)
        obs = tracker.record_prediction(
            symbol="KCHOL",
            timestamp=now,
            horizon="1d",
            shadow_prediction=185.0,
            baseline_prediction=182.0,
            latency_ms=42.5,
            interval_lower=178.0,
            interval_upper=192.0,
            epistemic_uncertainty=0.03,
        )

        assert obs.symbol == "KCHOL"
        assert obs.shadow_prediction == 185.0
        assert obs.baseline_prediction == 182.0
        assert obs.latency_ms == 42.5
        assert obs.is_realized() is False

        # Gerçekleşen sonucu kaydet
        tracker.resolve_observation(
            observation_id=obs.observation_id,
            actual_value=186.0,
            realized_at=now + timedelta(days=1),
        )

        updated_obs = tracker.get_observation(obs.observation_id)
        assert updated_obs is not None
        assert updated_obs.is_realized() is True
        assert updated_obs.actual_value == 186.0

    def test_shadow_mode_latency_sla_and_percentiles(self) -> None:
        """Gölge mod p50, p95 ve p99 çıkarım gecikmelerini ve hata oranını doğru hesaplar."""
        cfg = ShadowModeConfig(min_observations=4, max_allowed_latency_p95_ms=200.0)
        tracker = ShadowModeTracker(model_version="ML-SLA-Test", config=cfg)
        now = datetime.now(UTC)

        latencies = [30.0, 45.0, 60.0, 150.0]
        for idx, lat in enumerate(latencies):
            tracker.record_prediction(
                symbol="BIMAS",
                timestamp=now + timedelta(hours=idx),
                horizon="1d",
                shadow_prediction=450.0 + idx,
                baseline_prediction=448.0 + idx,
                latency_ms=lat,
            )

        report = tracker.evaluate()
        assert len(tracker.list_observations()) == 4
        assert report.latency_p50_ms > 0.0
        assert report.latency_p95_ms <= 200.0
        assert report.error_rate == 0.0

    def test_shadow_mode_promotion_decision_logic(self) -> None:
        """Terfi kararları: Yetersiz gözlemde EXTEND, SLA aşımında REJECT, başarıda PROCEED."""
        # 1. Yetersiz gözlem -> EXTEND_SHADOW
        cfg_pending = ShadowModeConfig(min_observations=20)
        tracker_pending = ShadowModeTracker("ML-Pending", config=cfg_pending)
        rep_pending = tracker_pending.evaluate()
        assert rep_pending.decision == ShadowPromotionDecision.EXTEND_SHADOW
        assert not rep_pending.is_approved_for_live()
        assert len(rep_pending.pending_reasons) > 0

        # 2. SLA gecikme aşımı -> REJECT
        cfg_reject = ShadowModeConfig(min_observations=3, max_allowed_latency_p95_ms=100.0)
        tracker_reject = ShadowModeTracker("ML-Reject", config=cfg_reject)
        now = datetime.now(UTC)
        for _ in range(3):
            tracker_reject.record_prediction(
                symbol="AKBNK",
                timestamp=now,
                horizon="1d",
                shadow_prediction=55.0,
                baseline_prediction=54.5,
                latency_ms=250.0,  # 100ms tavanı aşıldı
            )
        rep_reject = tracker_reject.evaluate()
        assert rep_reject.decision == ShadowPromotionDecision.REJECT
        assert any(
            "gecikme" in r.lower() or "sla" in r.lower() for r in rep_reject.rejection_reasons
        )

        # 3. Tüm kriterler karşılandığında -> PROCEED_TO_LIVE
        cfg_pass = ShadowModeConfig(
            min_observations=3,
            min_shadow_duration_days=0,
            max_allowed_latency_p95_ms=200.0,
            require_beat_baseline_mae=True,
            min_directional_accuracy=0.50,
            min_interval_coverage=0.60,
        )
        tracker_pass = ShadowModeTracker("ML-Pass", config=cfg_pass)
        now = datetime.now(UTC)
        for i in range(3):
            o = tracker_pass.record_prediction(
                symbol="ISCTR",
                timestamp=now + timedelta(hours=i),
                horizon="1d",
                shadow_prediction=12.0 + (i * 0.1),
                baseline_prediction=11.0,
                latency_ms=50.0,
                interval_lower=11.5 + (i * 0.1),
                interval_upper=12.5 + (i * 0.1),
            )
            # Model tam doğru tahmin yapsın (actual = shadow_prediction)
            tracker_pass.resolve_observation(o.observation_id, actual_value=12.0 + (i * 0.1))

        rep_pass = tracker_pass.evaluate()
        assert rep_pass.decision == ShadowPromotionDecision.PROCEED_TO_LIVE
        assert rep_pass.is_approved_for_live()
        assert rep_pass.model_mae == 0.0
        assert rep_pass.interval_coverage == 1.0

    def test_model_performance_drift_and_health(self) -> None:
        """ModelPerformanceTracker çevrimdışı/canlı sapma ve operasyonel sağlık durumunu yönetir."""
        tracker = ModelPerformanceTracker()

        offline = OfflineValidationMetrics(
            model_id="ML-v2.0",
            version="2.0.0",
            directional_accuracy=0.65,
            brier_score=0.15,
            mae=0.012,
            rmse=0.018,
            sample_count=150,
        )
        tracker.register_offline_validation(offline)
        assert tracker.get_offline_validation("ML-v2.0", "2.0.0") == offline

        # Canlı tahmin ve gerçekleşme kayıtları ekle (12 adet >= min_live_samples_for_evaluation=10)
        now = datetime.now(UTC)
        for i in range(12):
            pred = LivePredictionRecord(
                prediction_id=f"pred_{i}",
                model_id="ML-v2.0",
                version="2.0.0",
                symbol="THYAO",
                prediction_timestamp=now - timedelta(hours=i),
                horizon_hours=24,
                predicted_value=10.0 + (i * 0.1),
                predicted_direction=1,
                predicted_probability=0.75,
            )
            tracker.record_live_prediction(pred)
            tracker.record_realized_outcome(
                prediction_id=f"pred_{i}",
                actual_value=10.01 + (i * 0.1),
                actual_direction=1,
            )

        comparison = tracker.evaluate_model(model_id="ML-v2.0", version="2.0.0")
        assert comparison.status == ModelHealthStatus.HEALTHY
        assert comparison.live_metrics.realized_predictions == 12
        assert comparison.accuracy_gap is not None
        assert comparison.accuracy_gap <= 0.05

    def test_uncertainty_interval_coverage_calculation(self) -> None:
        """Gölge mod tahmin aralıkları gerçekleşme kapsaması (coverage) doğru hesaplanır."""
        cfg = ShadowModeConfig(min_observations=4, min_shadow_duration_days=0)
        tracker = ShadowModeTracker("ML-Cov-Test", config=cfg)
        now = datetime.now(UTC)

        # 4 gözlem: 3 tanesi aralık içinde, 1 tanesi aralık dışında
        obs_defs = [
            (100.0, 95.0, 105.0, 101.0),  # içinde
            (100.0, 95.0, 105.0, 98.0),  # içinde
            (100.0, 95.0, 105.0, 104.0),  # içinde
            (100.0, 95.0, 105.0, 115.0),  # DIŞINDA
        ]
        for idx, (pred, low, high, actual) in enumerate(obs_defs):
            o = tracker.record_prediction(
                symbol="TUPRS",
                timestamp=now + timedelta(hours=idx),
                horizon="1d",
                shadow_prediction=pred,
                baseline_prediction=99.0,
                interval_lower=low,
                interval_upper=high,
            )
            tracker.resolve_observation(o.observation_id, actual_value=actual)

        rep = tracker.evaluate()
        assert rep.interval_coverage == 0.75  # 3/4 = %75


    def test_generate_roadmap_step11_report(self) -> None:
        """generate_roadmap_step11_report Markdown denetim raporunu standartlara uygun üretir."""
        gatekeeper = RoadmapStepElevenGatekeeper()
        result = gatekeeper.evaluate()
        report_md = generate_roadmap_step11_report(result)

        assert "# Yol Haritası Uygulama Sırası — Adım 11 Denetim Raporu" in report_md
        assert "✅ PASS" in report_md
        assert "ROADMAP-S11-01-SHADOW-MODE-ISOLATION-AND-REGISTRY" in report_md
        assert "ROADMAP-S11-02-INFERENCE-LATENCY-AND-SLA-TRACKING" in report_md
        assert "ROADMAP-S11-03-OFFLINE-VS-LIVE-DRIFT-AND-HEALTH-STATUS" in report_md
        assert "ROADMAP-S11-04-NAIVE-BASELINE-COMPARISON-AND-PROMOTION-DECISIONS" in report_md
        assert "ROADMAP-S11-05-EPISTEMIC-UNCERTAINTY-AND-INTERVAL-COVERAGE" in report_md
        assert "ROADMAP-S11-06-MANDATORY-SPK-DISCLAIMER-AND-NO-ADVICE-SAFEGUARDS" in report_md
        assert result.disclaimer in report_md

    def test_gatekeeper_failure_on_missing_file(self, tmp_path: Path) -> None:
        """Eksik dosya durumunda kapı FAIL durumuna geçmeli ve metrikleri güncellemelidir."""
        empty_root = tmp_path / "empty_workspace"
        empty_root.mkdir()

        gatekeeper = RoadmapStepElevenGatekeeper(workspace_root=empty_root)
        result = gatekeeper.evaluate()

        assert result.status == RoadmapStepElevenStatus.FAIL
        assert result.failed_checks > 0
        assert result.passed_checks < 6
