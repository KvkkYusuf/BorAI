"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 9 test paketi.

Kaggle veri incelemesi ve veri hazırlama kabul paketi.
Test kapsamı:
- RoadmapStepNineGatekeeper 6/6 tam uyum doğrulaması.
- Kaggle veri kartı, lisans künyesi (CC-BY-4.0), KAP/BIST kaynak zinciri ve v2 politikası.
- Kronolojik zaman serisi bölmesi (TimeSeriesSplitter, SplitRegistry) sızıntısızlık engeli.
- Gelecek verisi sızıntısı tespiti (LookaheadBiasDetector, center=True, shift(-k), korelasyon).
- Fiyat ölçekleme (PriceScaleManager) ve kurumsal işlem motoru (CorporateActionAdjuster).
- Hayatta kalma yanlılığı (survivorship bias), delist olmuş hisseler ve seçilmiş evren analizi.
- DVC nesne depolama, Git ham veri koruması (.gitignore) ve canlı piyasa şema uyumlaştırma köprüsü.
- SPK yasal uyarı feragatnamesi ve al/sat tavsiyesi olmama güvencesi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from stock_analysis.pipeline.corporate_actions import (
    CorporateAction,
    CorporateActionAdjuster,
    CorporateActionType,
)
from stock_analysis.pipeline.lookahead_bias import (
    DataLeakageError,
    LookaheadBiasDetector,
)
from stock_analysis.pipeline.price_scaling import (
    PriceScaleManager,
    ScalingMethod,
)
from stock_analysis.pipeline.time_series_splitter import (
    TimeSeriesSplitter,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP9_DISCLAIMER,
    ROADMAP_STEP9_STATUS,
    RoadmapStepNineGatekeeper,
    RoadmapStepNineStatus,
    generate_roadmap_step9_report,
)


class TestRoadmapOrderStepNineSuite:
    """FAZ-18 Yol Haritası Adım 9 kabul kapısı testleri."""

    def test_gatekeeper_full_evaluation_pass(self) -> None:
        """RoadmapStepNineGatekeeper tüm 6 kontrolü başarıyla geçer (6/6 PASS)."""
        gatekeeper = RoadmapStepNineGatekeeper()
        result = gatekeeper.evaluate()

        assert result.status == RoadmapStepNineStatus.PASS
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert len(result.checks) == 6
        assert ROADMAP_STEP9_STATUS.get() == 1.0

        check_ids = [c.check_id for c in result.checks]
        assert "ROADMAP-S9-01-KAGGLE-METADATA-PROVENANCE-LICENSING" in check_ids
        assert "ROADMAP-S9-02-TIME-SERIES-SPLIT-AND-PURITY" in check_ids
        assert "ROADMAP-S9-03-LOOKAHEAD-BIAS-DETECTION" in check_ids
        assert "ROADMAP-S9-04-PRICE-SCALING-AND-CORPORATE-ACTIONS" in check_ids
        assert "ROADMAP-S9-05-SURVIVORSHIP-BIAS-AND-DELISTED-EVALUATION" in check_ids
        assert "ROADMAP-S9-06-DVC-GIT-RAW-ISOLATION-SCHEMA-TRANSITION" in check_ids

    def test_default_disclaimer_and_compliance(self) -> None:
        """Adım 9 feragatnamesi SPK mevzuatına uygun olmalı ve yasaklı terim içermemelidir."""
        gatekeeper = RoadmapStepNineGatekeeper()
        assert "SPK Mevzuatı Uyarınca" in gatekeeper.disclaimer
        assert "yatırım danışmanlığı" in gatekeeper.disclaimer
        assert "Kaggle veri setleri" in gatekeeper.disclaimer

        result = gatekeeper.evaluate()
        assert result.disclaimer == DEFAULT_ROADMAP_STEP9_DISCLAIMER

    def test_check_kaggle_metadata_and_provenance_licensing(self) -> None:
        """1. Kontrol: Kaggle veri kartı, lisanslama ve kaynak zinciri denetimi."""
        gatekeeper = RoadmapStepNineGatekeeper()
        item = gatekeeper._check_kaggle_metadata_and_provenance_licensing()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S9-01-KAGGLE-METADATA-PROVENANCE-LICENSING"
        assert item.details["has_card"] is True
        assert item.details["has_cc_by"] is True
        assert item.details["has_currency"] is True
        assert item.details["has_tz"] is True
        assert item.details["has_v2_policy"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_kaggle_metadata_and_provenance_licensing()
            assert fail_item.passed is False
            assert "Eksik Kaggle lisans bileşenleri" in fail_item.evidence

    def test_check_time_series_split_and_purity(self) -> None:
        """2. Kontrol: Zaman serisi bölmesi ve sızıntısızlık güvencesi denetimi."""
        gatekeeper = RoadmapStepNineGatekeeper()
        item = gatekeeper._check_time_series_split_and_purity()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S9-02-TIME-SERIES-SPLIT-AND-PURITY"
        assert item.details["has_splitter"] is True
        assert item.details["has_registry"] is True
        assert item.details["has_shuffle_guard"] is True
        assert item.details["has_embargo_support"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_time_series_split_and_purity()
            assert fail_item.passed is False
            assert "Eksik zaman serisi bölme bileşenleri" in fail_item.evidence

    def test_check_lookahead_bias_detection(self) -> None:
        """3. Kontrol: Gelecek verisi sızıntısı (look-ahead bias) tespiti denetimi."""
        gatekeeper = RoadmapStepNineGatekeeper()
        item = gatekeeper._check_lookahead_bias_detection()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S9-03-LOOKAHEAD-BIAS-DETECTION"
        assert item.details["has_file"] is True
        assert item.details["has_detector_cls"] is True
        assert item.details["has_center_window_check"] is True
        assert item.details["has_negative_shift_check"] is True
        assert item.details["has_correlation_check"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_lookahead_bias_detection()
            assert fail_item.passed is False
            assert "Eksik lookahead sızıntı bileşenleri" in fail_item.evidence

    def test_check_price_scaling_and_corporate_actions(self) -> None:
        """4. Kontrol: Fiyat ölçekleme, normalizasyon ve kurumsal işlem düzeltmeleri denetimi."""
        gatekeeper = RoadmapStepNineGatekeeper()
        item = gatekeeper._check_price_scaling_and_corporate_actions()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S9-04-PRICE-SCALING-AND-CORPORATE-ACTIONS"
        assert item.details["has_scaling"] is True
        assert item.details["has_corp"] is True
        assert item.details["has_price_scaler"] is True
        assert item.details["has_train_only_fit"] is True
        assert item.details["has_corp_adjuster"] is True
        assert item.details["has_dual_stream"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_price_scaling_and_corporate_actions()
            assert fail_item.passed is False
            assert "Eksik fiyat ölçekleme / kurumsal işlem bileşenleri" in fail_item.evidence

    def test_check_survivorship_bias_and_delisted_stocks(self) -> None:
        """5. Kontrol: Hayatta kalma yanlılığı ve kottan çıkan hisseler denetimi."""
        gatekeeper = RoadmapStepNineGatekeeper()
        item = gatekeeper._check_survivorship_bias_and_delisted_stocks()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S9-05-SURVIVORSHIP-BIAS-AND-DELISTED-EVALUATION"
        assert item.details["has_card"] is True
        assert item.details["has_verifier"] is True
        assert item.details["has_survivorship_section"] is True
        assert item.details["has_delisted_stocks_doc"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_survivorship_bias_and_delisted_stocks()
            assert fail_item.passed is False
            assert "Eksik hayatta kalma yanlılığı bileşenleri" in fail_item.evidence

    def test_check_dvc_git_raw_isolation_and_schema_transition(self) -> None:
        """6. Kontrol: DVC nesne depolama, Git ham veri izolasyonu ve şema uyumlaştırma."""
        gatekeeper = RoadmapStepNineGatekeeper()
        item = gatekeeper._check_dvc_git_raw_isolation_and_schema_transition()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S9-06-DVC-GIT-RAW-ISOLATION-SCHEMA-TRANSITION"
        assert item.details["has_gitignore"] is True
        assert item.details["has_dvc"] is True
        assert item.details["has_transition"] is True
        assert item.details["has_git_raw_ignore"] is True
        assert item.details["has_dvc_leak_guard"] is True
        assert item.details["has_schema_reconciler"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_dvc_git_raw_isolation_and_schema_transition()
            assert fail_item.passed is False
            assert "Eksik DVC / şema bileşenleri" in fail_item.evidence

    def test_report_generation_markdown(self) -> None:
        """generate_roadmap_step9_report fonksiyonu standartlara uygun Markdown üretir."""
        gatekeeper = RoadmapStepNineGatekeeper()
        result = gatekeeper.evaluate()
        report = generate_roadmap_step9_report(result)

        assert "# Yol Haritası Uygulama Sırası — Adım 9 Denetim Raporu" in report
        assert "**Genel Durum:** ✅ PASS" in report
        assert "ROADMAP-S9-01-KAGGLE-METADATA-PROVENANCE-LICENSING" in report
        assert "ROADMAP-S9-06-DVC-GIT-RAW-ISOLATION-SCHEMA-TRANSITION" in report
        assert "DATASET_CARD_KAGGLE.md" in report
        assert "TimeSeriesSplitter" in report
        assert "LookaheadBiasDetector" in report
        assert "PriceScaleManager" in report

    def test_to_dict_serialization(self) -> None:
        """VerificationResult sözlük formatına kayıpsız dönüştürülebilir."""
        gatekeeper = RoadmapStepNineGatekeeper()
        result = gatekeeper.evaluate()
        data = result.to_dict()

        assert data["status"] == "PASS"
        assert data["total_checks"] == 6
        assert data["passed_checks"] == 6
        assert data["failed_checks"] == 0
        assert len(data["checks"]) == 6
        assert data["disclaimer"] == DEFAULT_ROADMAP_STEP9_DISCLAIMER

    def test_time_series_splitter_functional_behavior(self) -> None:
        """TimeSeriesSplitter kronolojik sıralamayı ve çakışmasızlığı doğrular."""
        # Kronolojik kayıtlar
        records = [
            {"date": date(2020, 1, 1), "value": 10},
            {"date": date(2020, 1, 2), "value": 11},
            {"date": date(2020, 1, 3), "value": 12},
            {"date": date(2020, 1, 4), "value": 13},
        ]
        TimeSeriesSplitter.verify_chronological_order(records)

        # Karışık (shuffled) sıralamada DataLeakageError fırlatılmalıdır
        shuffled = [
            {"date": date(2020, 1, 3), "value": 12},
            {"date": date(2020, 1, 1), "value": 10},
        ]
        with pytest.raises(DataLeakageError):
            TimeSeriesSplitter.verify_chronological_order(shuffled)

        # Partition sınırları Train < Val < Test
        train_p = records[:2]
        val_p = records[2:3]
        test_p = records[3:]
        assert TimeSeriesSplitter.verify_no_leakage(train_p, val_p, test_p) is True

    def test_lookahead_bias_detector_functional_behavior(self) -> None:
        """LookaheadBiasDetector centered window veya forward shift sızıntılarını tespit eder."""
        # Güvenli pencere
        LookaheadBiasDetector.verify_window_configuration(
            window_size=20, is_centered=False, forward_shift=0
        )

        # Ortalanmış pencere (center=True) sızıntısı
        with pytest.raises(DataLeakageError):
            LookaheadBiasDetector.verify_window_configuration(window_size=20, is_centered=True)

        # Geleceğe kaydırma (shift < 0) sızıntısı
        with pytest.raises(DataLeakageError):
            LookaheadBiasDetector.verify_window_configuration(window_size=20, forward_shift=2)

    def test_corporate_actions_and_price_scaling_integration(self) -> None:
        """CorporateActionAdjuster ve PriceScaleManager fonksiyonel entegrasyonu."""
        bars = [
            MarketBarRecord(
                symbol="THYAO",
                trade_date=date(2024, 1, 2),
                raw_open=Decimal("100.0"),
                raw_high=Decimal("105.0"),
                raw_low=Decimal("99.0"),
                raw_close=Decimal("102.0"),
                adj_open=Decimal("100.0"),
                adj_high=Decimal("105.0"),
                adj_low=Decimal("99.0"),
                adj_close=Decimal("102.0"),
                volume=1000,
                turnover_try=Decimal("102000.0"),
                data_status=DataStatus.VERIFIED,
                source_name="bist-eod",
            ),
            MarketBarRecord(
                symbol="THYAO",
                trade_date=date(2024, 1, 3),
                raw_open=Decimal("52.0"),
                raw_high=Decimal("55.0"),
                raw_low=Decimal("50.0"),
                raw_close=Decimal("54.0"),
                adj_open=Decimal("52.0"),
                adj_high=Decimal("55.0"),
                adj_low=Decimal("50.0"),
                adj_close=Decimal("54.0"),
                volume=2000,
                turnover_try=Decimal("108000.0"),
                data_status=DataStatus.VERIFIED,
                source_name="bist-eod",
            ),
        ]
        actions = [
            CorporateAction(
                symbol="THYAO",
                ex_date=date(2024, 1, 3),
                action_type=CorporateActionType.SPLIT,
                ratio=Decimal("2.0"),
                description="2:1 Hisse Bolunmesi",
            )
        ]

        adjuster = CorporateActionAdjuster()
        adjusted = adjuster.adjust(bars, actions)

        assert len(adjusted) == 2
        # Ex-date öncesindeki bar 2:1 oranında geriye dönük yarıya bölünür (factor = 0.5)
        assert adjusted[0].adj_close == Decimal("51.0000")
        assert adjusted[1].adj_close == Decimal("54.0000")

        # Fiyat ölçekleme testi
        scaler = PriceScaleManager(method=ScalingMethod.MINMAX, feature_range=(0.0, 1.0))
        scaler.fit(adjusted[:1])
        scaled_features = scaler.transform(adjusted)

        assert len(scaled_features) == 2
        assert scaled_features[0].symbol == "THYAO"
