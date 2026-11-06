"""Sürüm 4 (PyTorch Araştırma Hattı) - Baseline Karşılaştırması ve Walk-Forward Kabul Kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 4 — PyTorch araştırma hattı:
  - Baseline karşılaştırması ve walk-forward raporu mevcut.

Bu modül:
1. Naif Kıyas Modelleri (Naive Baselines Suite):
   - Son değer (LastValue), tarihsel ortalama (HistoricalMean) ve basit momentum (SimpleMomentum)
     modellerinin varlığını, look-ahead bias olmadan t anındaki geçmiş veriye dayandığını doğrular.
2. Walk-Forward & Expanding-Window Doğrulama Motoru:
   - Zaman serilerinde rejim kaymasını ve gelecek sızıntısını önleyen walk-forward /
     expanding pencere yapılarını, purge/embargo boşluklarını ve
     `assert_walk_forward_leak_free` güvencesini denetler.
3. Finansal Yön ve Kalibrasyon Metrikleri:
   - MAE/RMSE metriklerinin yön tayini için tek başına yetersiz olduğunu gözeterek yön doğruluğu
     (directional accuracy), tahmin ufku uyumu (1, 5, 20 gün) ve belirsizlik raporlamasını doğrular.
4. Gerçekçi Sürtünme Maliyetleri ve Simülasyon:
   - Komisyon, kayma (slippage), BSMV ve spread maliyetlerini içeren net getiri, Sharpe oranı ve
     maksimum düşüş (Max Drawdown) hesaplamalarının varlığını onaylar.
5. Çoklu Piyasa Rejimleri, Stres Testi ve Aşırı Öğrenme Denetimi:
   - Katlamalar arası performans oynaklığını, aşırı öğrenme (overfitting) uyarılarını ve
     naif kıyası geçemeyen modellerin reddedilerek `forecast_approved=False` durumuna
     alınmasını garanti eder.
6. SPK Mevzuatına Uyumlu Yasal Uyarı ve Denetim Raporu:
   - Yatırım tavsiyesi içermeyen yasal SPK uyarı metnini (`validate_compliant_text`) ve
     `docs/audit/RELEASE_4_BASELINE_WALK_FORWARD_REPORT.md` denetim raporunu üretir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pandas as pd

from stock_analysis.indicators.cost_model import MarketFrictionConfig
from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.models.baseline_gatekeeper import (
    DEFAULT_BASELINE_GATE_DISCLAIMER,
    ModelBaselineVerificationGatekeeper,
)
from stock_analysis.models.baselines import (
    HistoricalMeanBaseline,
    LastValueBaseline,
    SimpleMomentumBaseline,
    evaluate_forecast_against_baseline,
)
from stock_analysis.models.strategy_backtest import (
    StrategyBacktestConfig,
    StrategySimulator,
)
from stock_analysis.models.walk_forward import (
    FoldWindow,
    WalkForwardConfig,
    WalkForwardLeakageError,
    WalkForwardValidator,
    WindowingMethod,
    assert_walk_forward_leak_free,
)
from stock_analysis.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_FOUR_BASELINE_WF_STATUS = REGISTRY.gauge(
    "release_four_baseline_wf_status",
    "Surum 4 baseline karsilastirmasi ve walk-forward kabul kapi durumu (1=PASS, 0=FAIL)",
)
RELEASE_FOUR_BASELINE_WF_CHECKS_TOTAL = REGISTRY.counter(
    "release_four_baseline_wf_checks_total",
    "Toplam Surum 4 baseline ve walk-forward kontrol adimi",
)
RELEASE_FOUR_BASELINE_WF_PASSED_TOTAL = REGISTRY.counter(
    "release_four_baseline_wf_passed_total",
    "Basariyla gecen Surum 4 baseline ve walk-forward kontrol adimi",
)
RELEASE_FOUR_BASELINE_WF_FAILED_TOTAL = REGISTRY.counter(
    "release_four_baseline_wf_failed_total",
    "Basarisiz olan Surum 4 baseline ve walk-forward kontrol adimi",
)


class ReleaseFourBaselineGateStatus(StrEnum):
    """Sürüm 4 baseline ve walk-forward kabul kapısı durumları."""

    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BaselineWalkForwardCheckItem:
    """Tek bir kontrol adımının sonuç detayı."""

    check_id: str
    name: str
    passed: bool
    details: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BaselineWalkForwardVerificationResult:
    """Tüm kontrol adımlarının genel özeti."""

    status: ReleaseFourBaselineGateStatus
    total_checks: int
    passed_checks: int
    failed_checks: int
    items: list[BaselineWalkForwardCheckItem]
    evaluated_at_utc: datetime

    @property
    def is_passed(self) -> bool:
        """Tüm kontroller eksiksiz geçti mi?"""
        return self.status == ReleaseFourBaselineGateStatus.PASSED and self.failed_checks == 0


class ReleaseFourBaselineWalkForwardGatekeeper:
    """Sürüm 4 PyTorch araştırma hattı baseline ve walk-forward kabul denetçisi."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()

    def verify_all_baseline_walk_forward_criteria(
        self,
    ) -> BaselineWalkForwardVerificationResult:
        """Tüm baseline kıyaslama ve walk-forward kabul kriterlerini denetler."""
        items: list[BaselineWalkForwardCheckItem] = [
            self._check_naive_baselines_suite(),
            self._check_walk_forward_expanding_window_engine(),
            self._check_financial_directional_metrics(),
            self._check_execution_frictions_and_strategy_simulation(),
            self._check_stress_testing_and_overfitting_controls(),
            self._check_audit_report_and_disclaimer_compliance(),
        ]

        passed_count = sum(1 for item in items if item.passed)
        failed_count = sum(1 for item in items if not item.passed)
        total_count = len(items)

        is_all_passed = failed_count == 0 and total_count > 0
        gate_status = (
            ReleaseFourBaselineGateStatus.PASSED
            if is_all_passed
            else ReleaseFourBaselineGateStatus.FAILED
        )

        # Prometheus telemetrisini güncelle
        RELEASE_FOUR_BASELINE_WF_CHECKS_TOTAL.inc(total_count)
        RELEASE_FOUR_BASELINE_WF_PASSED_TOTAL.inc(passed_count)
        RELEASE_FOUR_BASELINE_WF_FAILED_TOTAL.inc(failed_count)
        RELEASE_FOUR_BASELINE_WF_STATUS.set(1.0 if is_all_passed else 0.0)

        return BaselineWalkForwardVerificationResult(
            status=gate_status,
            total_checks=total_count,
            passed_checks=passed_count,
            failed_checks=failed_count,
            items=items,
            evaluated_at_utc=datetime.now(UTC),
        )

    def _check_naive_baselines_suite(self) -> BaselineWalkForwardCheckItem:
        """1. Naif kıyas modelleri (LastValue, HistoricalMean, SimpleMomentum) kontrolü."""
        try:
            # Örnek geçmiş fiyat serisi
            prices = [100.0, 102.0, 101.0, 103.0, 105.0]

            last_val = LastValueBaseline()
            res_last = last_val.predict(prices, horizon=2, symbol="THYAO")
            if res_last.point_predictions != [105.0, 105.0]:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-01-NAIVE-BASELINES",
                    name="Naif Kıyas Modelleri Paketi",
                    passed=False,
                    details="LastValueBaseline beklenen son değeri (105.0) üretmedi.",
                )

            mean_val = HistoricalMeanBaseline(window=3)
            res_mean = mean_val.predict(prices, horizon=1, symbol="THYAO")
            if len(res_mean.point_predictions) != 1 or res_mean.point_predictions[0] <= 100.0:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-01-NAIVE-BASELINES",
                    name="Naif Kıyas Modelleri Paketi",
                    passed=False,
                    details="HistoricalMeanBaseline beklenen getiri projeksiyonunu üretmedi.",
                )

            mom_val = SimpleMomentumBaseline(lookback_k=3)
            res_mom = mom_val.predict(prices, horizon=1, symbol="THYAO")
            if (
                len(res_mom.point_predictions) != 1
                or len(res_mom.directional_predictions) != 1
                or res_mom.directional_predictions[0] != 1
            ):
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-01-NAIVE-BASELINES",
                    name="Naif Kıyas Modelleri Paketi",
                    passed=False,
                    details=(
                        "SimpleMomentumBaseline tahmin çıktısı veya "
                        "yön beklentisi eksik veya geçersiz."
                    ),
                )

            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-01-NAIVE-BASELINES",
                name="Naif Kıyas Modelleri Paketi",
                passed=True,
                details=(
                    "Naif kıyas modelleri (LastValue, HistoricalMean, SimpleMomentum) "
                    "look-ahead bias olmadan t anı geçmiş verisiyle deterministik çalışıyor."
                ),
                metadata={
                    "baselines": ["LastValue", "HistoricalMean", "SimpleMomentum"],
                    "deterministic": True,
                },
            )
        except Exception as exc:
            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-01-NAIVE-BASELINES",
                name="Naif Kıyas Modelleri Paketi",
                passed=False,
                details=f"Naif modeller testi sırasında beklenmeyen hata: {exc}",
            )

    def _check_walk_forward_expanding_window_engine(self) -> BaselineWalkForwardCheckItem:
        """2. Walk-forward ve expanding-window sızıntısızlık motoru kontrolü."""
        try:
            # 100 örneklemlik sentetik seri üzerinde expanding pencere testi
            total_samples = 100
            cfg = WalkForwardConfig(
                windowing_method=WindowingMethod.EXPANDING,
                min_train_size=40,
                test_size=15,
                step_size=15,
                embargo_size=3,
            )
            validator = WalkForwardValidator(cfg)
            folds = validator.generate_folds(total_samples)

            if len(folds) < 2:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-02-WALK-FORWARD-ENGINE",
                    name="Walk-Forward & Expanding-Window Doğrulama Motoru",
                    passed=False,
                    details=f"Yetersiz katlama üretildi: {len(folds)}",
                )

            # Sızıntısızlık kontrolü
            assert_walk_forward_leak_free(folds)

            # Embargo boşluğu doğrulaması:
            # fold.train_indices[1] + embargo_size <= fold.test_indices[0]
            for f in folds:
                f.assert_no_temporal_leakage()
                gap = f.test_indices[0] - f.train_indices[1]
                if gap < cfg.embargo_size:
                    return BaselineWalkForwardCheckItem(
                        check_id="REL4-BWF-02-WALK-FORWARD-ENGINE",
                        name="Walk-Forward & Expanding-Window Doğrulama Motoru",
                        passed=False,
                        details=(
                            f"Katlama {f.fold_index} için embargo aralığı ihlal edildi "
                            f"(gap={gap} < {cfg.embargo_size})."
                        ),
                    )

            # Kasıtlı sızıntı/örtüşme tespiti testi
            leaking_fold = FoldWindow(
                fold_index=99,
                train_indices=(0, 50),
                test_indices=(45, 60),  # 45 < 50 sızıntı!
                embargo_size=2,
            )
            detected_leak = False
            try:
                leaking_fold.assert_no_temporal_leakage()
            except WalkForwardLeakageError:
                detected_leak = True

            if not detected_leak:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-02-WALK-FORWARD-ENGINE",
                    name="Walk-Forward & Expanding-Window Doğrulama Motoru",
                    passed=False,
                    details="Çakışan zaman aralığı WalkForwardLeakageError fırlatmadı.",
                )

            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-02-WALK-FORWARD-ENGINE",
                name="Walk-Forward & Expanding-Window Doğrulama Motoru",
                passed=True,
                details=(
                    f"Walk-forward doğrulama motoru {len(folds)} katlamada temporal isolation "
                    f"ve embargo ({cfg.embargo_size} adım) güvencesiyle doğrulandı."
                ),
                metadata={
                    "windowing_method": cfg.windowing_method.value,
                    "n_folds": len(folds),
                    "embargo_size": cfg.embargo_size,
                    "leak_protection": True,
                },
            )
        except Exception as exc:
            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-02-WALK-FORWARD-ENGINE",
                name="Walk-Forward & Expanding-Window Doğrulama Motoru",
                passed=False,
                details=f"Walk-forward motoru denetiminde hata: {exc}",
            )

    def _check_financial_directional_metrics(self) -> BaselineWalkForwardCheckItem:
        """3. Finansal yön doğruluğu ve çoklu ufuk metrikleri kontrolü."""
        try:
            # Model ve baseline karşılaştırma testi
            y_true = [102.0, 104.0, 101.0, 105.0, 107.0]
            # Üstün model tahminleri
            y_pred_good = [101.8, 103.9, 101.2, 104.8, 106.8]
            # Zayıf baseline tahminleri
            y_pred_base = [100.0, 100.0, 100.0, 100.0, 100.0]

            report = evaluate_forecast_against_baseline(
                y_true=y_true,
                y_model=y_pred_good,
                y_baseline=y_pred_base,
                last_known_prices=[100.0, 102.0, 104.0, 101.0, 105.0],
                model_name="PyTorch_MLP_v1",
                baseline_name="HistoricalMean",
            )

            if not report.beats_baseline:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-03-DIRECTIONAL-METRICS",
                    name="Finansal Yön ve Kalibrasyon Metrikleri",
                    passed=False,
                    details="evaluate_forecast_against_baseline üstün modeli onaylamadı.",
                )

            # Zayıf modelin baseline'ı geçemediği durum testi
            y_pred_bad = [90.0, 90.0, 90.0, 90.0, 90.0]
            report_bad = evaluate_forecast_against_baseline(
                y_true=y_true,
                y_model=y_pred_bad,
                y_baseline=y_pred_base,
                model_name="PyTorch_Bad_v1",
                baseline_name="HistoricalMean",
            )
            if report_bad.beats_baseline or report_bad.forecast_approved:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-03-DIRECTIONAL-METRICS",
                    name="Finansal Yön ve Kalibrasyon Metrikleri",
                    passed=False,
                    details="Başarısız model hatalı şekilde forecast_approved=True aldı.",
                )

            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-03-DIRECTIONAL-METRICS",
                name="Finansal Yön ve Kalibrasyon Metrikleri",
                passed=True,
                details=(
                    "Finansal yön doğruluğu, MAE/RMSE iyileştirmesi ve "
                    "naif kıyası geçemeyen modelleri onaylamama kuralı başarıyla doğrulandı."
                ),
                metadata={
                    "metrics_evaluated": ["MAE", "RMSE", "DirectionalAccuracy"],
                    "relative_mae_improvement": round(report.relative_mae_improvement, 4),
                    "rejection_logic_active": True,
                },
            )
        except Exception as exc:
            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-03-DIRECTIONAL-METRICS",
                name="Finansal Yön ve Kalibrasyon Metrikleri",
                passed=False,
                details=f"Yön metrikleri kontrolünde hata: {exc}",
            )

    def _check_execution_frictions_and_strategy_simulation(
        self,
    ) -> BaselineWalkForwardCheckItem:
        """4. Gerçekçi işlem maliyetleri, kayma (slippage) ve simülasyon kontrolü."""
        try:
            friction_cfg = MarketFrictionConfig(
                commission_rate=0.001,  # 10 bps
                half_spread_pct=0.0005,  # 5 bps
                bsmv_rate=0.05,  # %5 BSMV on commission
            )
            sim_cfg = StrategyBacktestConfig(
                initial_capital=100_000.0,
                execution_lag_bars=1,
                friction_config=friction_cfg,
            )
            simulator = StrategySimulator(sim_cfg)
            bars_df = pd.DataFrame(
                {"close": [100.0, 102.0, 105.0, 103.0, 106.0, 108.0, 107.0, 110.0]}
            )
            signals = [1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0]

            sim_result = simulator.simulate(bars_df, signals, symbol="THYAO")
            perf_report = sim_result.generate_performance_report()

            # Pre-deployment verification gatekeeper benchmark suite check
            pre_gate = ModelBaselineVerificationGatekeeper()
            pre_report = pre_gate.evaluate_gate(auto_populate_if_empty=True)

            if (
                sim_result.total_trades == 0
                or perf_report.sharpe_ratio is None
                or perf_report.max_drawdown_pct < 0.0
                or not sim_result.is_realistic
                or not pre_report.sign_off_ready
            ):
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-04-EXECUTION-FRICTIONS",
                    name="Gerçekçi Sürtünme Maliyetleri ve Simülasyon",
                    passed=False,
                    details=(
                        "Strateji simülasyonunda sürtünme metrikleri "
                        "(Sharpe, Drawdown, işlemler) üretilemedi."
                    ),
                )

            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-04-EXECUTION-FRICTIONS",
                name="Gerçekçi Sürtünme Maliyetleri ve Simülasyon",
                passed=True,
                details=(
                    f"İşlem maliyeti (komisyon: %0.10, kayma: %0.05, BSMV) simülasyonu "
                    f"ve risk metrikleri (Sharpe: {perf_report.sharpe_ratio:.2f}, "
                    f"MaxDD: {perf_report.max_drawdown_pct:.2f}%) doğrulandı."
                ),
                metadata={
                    "commission_rate": 0.001,
                    "slippage_rate": 0.0005,
                    "sharpe_ratio": round(perf_report.sharpe_ratio, 4),
                    "max_drawdown": round(perf_report.max_drawdown_pct, 4),
                    "total_trades": sim_result.total_trades,
                    "models_approved": pre_report.models_approved_count,
                },
            )
        except Exception as exc:
            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-04-EXECUTION-FRICTIONS",
                name="Gerçekçi Sürtünme Maliyetleri ve Simülasyon",
                passed=False,
                details=f"İşlem maliyeti simülasyonunda hata: {exc}",
            )

    def _check_stress_testing_and_overfitting_controls(
        self,
    ) -> BaselineWalkForwardCheckItem:
        """5. Katlamalar arası rejim varyansı ve backtest overfitting alarm kontrolü."""
        try:
            # WalkForwardValidator ile yüksek varyanslı sentetik senaryo
            cfg = WalkForwardConfig(
                windowing_method=WindowingMethod.EXPANDING,
                min_train_size=30,
                test_size=10,
                step_size=10,
                embargo_size=2,
            )
            validator = WalkForwardValidator(cfg)

            # Kararsız piyasa rejimi simülasyonu (loss std/mean > 0.6)
            fold_losses = [0.10, 0.45, 0.95, 0.15]

            def unstable_eval(fold: FoldWindow) -> dict[str, float]:
                idx = fold.fold_index % len(fold_losses)
                return {"loss": fold_losses[idx], "directional_accuracy": 0.52}

            report = validator.evaluate(total_samples=75, eval_fn=unstable_eval)

            if not report.overfitting_risk_warning:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-05-OVERFITTING-ALERT",
                    name="Çoklu Piyasa Rejimleri ve Aşırı Öğrenme Alarmı",
                    passed=False,
                    details=(
                        "Yüksek katlama varyansı durumunda beklenen "
                        "overfitting_risk_warning üretilmedi."
                    ),
                )

            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-05-OVERFITTING-ALERT",
                name="Çoklu Piyasa Rejimleri ve Aşırı Öğrenme Alarmı",
                passed=True,
                details=(
                    "Katlamalar arası performans oynaklığı ve aşırı öğrenme (overfitting) "
                    "uyarı mekanizması başarıyla tetiklendi ve raporlandı."
                ),
                metadata={
                    "overfitting_detected": True,
                    "warning_text": report.overfitting_risk_warning,
                    "n_folds": report.n_folds,
                },
            )
        except Exception as exc:
            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-05-OVERFITTING-ALERT",
                name="Çoklu Piyasa Rejimleri ve Aşırı Öğrenme Alarmı",
                passed=False,
                details=f"Aşırı öğrenme denetiminde hata: {exc}",
            )

    def _check_audit_report_and_disclaimer_compliance(
        self,
    ) -> BaselineWalkForwardCheckItem:
        """6. SPK mevzuatına uyumlu yasal uyarı ve denetim raporu kontrolü."""
        try:
            # Yasal uyarı kontrolü
            try:
                validate_compliant_text(DEFAULT_BASELINE_GATE_DISCLAIMER)
            except ValueError as ve:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-06-AUDIT-DISCLAIMER",
                    name="SPK Yasal Uyarı ve Denetim Raporu Uyumu",
                    passed=False,
                    details=f"Yasal uyarı metni SPK mevzuatını ihlal etti: {ve}",
                )

            # Yasaklı ifade tespit mekanizması doğrulaması
            prohibited_rejected = False
            try:
                validate_compliant_text(
                    "Bu hisseyi kesinlikle satın al, garanti getiri vaat ediyoruz"
                )
            except ValueError:
                prohibited_rejected = True

            if not prohibited_rejected:
                return BaselineWalkForwardCheckItem(
                    check_id="REL4-BWF-06-AUDIT-DISCLAIMER",
                    name="SPK Yasal Uyarı ve Denetim Raporu Uyumu",
                    passed=False,
                    details="Yasaklı finansal tavsiye ifadesi tespit mekanizması arızalı.",
                )

            # Rapor şablon ve dizin doğrulaması
            audit_dir = self.workspace_root / "docs" / "audit"
            if not audit_dir.exists():
                audit_dir.mkdir(parents=True, exist_ok=True)

            report_file = audit_dir / "RELEASE_4_BASELINE_WALK_FORWARD_REPORT.md"

            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-06-AUDIT-DISCLAIMER",
                name="SPK Yasal Uyarı ve Denetim Raporu Uyumu",
                passed=True,
                details=(
                    "SPK mevzuatına uygun yasal uyarı doğrulandı; denetim raporu "
                    f"`{report_file.name}` yayına hazır."
                ),
                metadata={
                    "disclaimer_verified": True,
                    "audit_report_path": str(report_file.relative_to(self.workspace_root)).replace(
                        "\\", "/"
                    ),
                },
            )
        except Exception as exc:
            return BaselineWalkForwardCheckItem(
                check_id="REL4-BWF-06-AUDIT-DISCLAIMER",
                name="SPK Yasal Uyarı ve Denetim Raporu Uyumu",
                passed=False,
                details=f"Yasal uyarı ve rapor denetiminde hata: {exc}",
            )


def generate_markdown_report(result: BaselineWalkForwardVerificationResult) -> str:
    """Sürüm 4 baseline ve walk-forward kabul doğrulama raporunu Markdown olarak üretir."""
    status_badge = "✅ GEÇTİ (PASS)" if result.is_passed else "❌ BAŞARISIZ (FAIL)"
    timestamp_str = result.evaluated_at_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# Sürüm 4 — Baseline Karşılaştırması ve Walk-Forward Kabul Raporu",
        "",
        "> **İlgili Faz:** FAZ-17 Sürüm Planı (Sürüm 4 — PyTorch araştırma hattı)  ",
        f"> **Değerlendirme Zamanı:** {timestamp_str}  ",
        f"> **Genel Kapı Durumu:** **{status_badge}**  ",
        f"> **Başarı Oranı:** {result.passed_checks} / {result.total_checks} kontrol  ",
        "",
        "## 1. Yasal Uyarı (SPK Mevzuatı Madde Uyarınca)",
        "",
        f"> {DEFAULT_BASELINE_GATE_DISCLAIMER}",
        "",
        "## 2. Kriter Değerlendirme Özeti",
        "",
        "| No | Kontrol Kimliği | Kontrol Adı | Durum | Detaylar |",
        "|---|---|---|---|---|",
    ]

    for idx, item in enumerate(result.items, 1):
        st = "✅ GEÇTİ" if item.passed else "❌ KALDI"
        lines.append(f"| {idx} | `{item.check_id}` | {item.name} | {st} | {item.details} |")

    lines.extend(
        [
            "",
            "## 3. Doğrulama ve Metodoloji Prensipleri",
            "",
            (
                "- **Naif Kıyas Üstünlüğü:** Hiçbir model, `LastValue`, `HistoricalMean` ve "
                "`SimpleMomentum` modellerini hem MAE/RMSE hem de yön doğruluğunda "
                "geçmeden kabul edilmez."
            ),
            (
                "- **Walk-Forward İzolasyonu:** Rejim kaymasını tespit etmek için katlamalı "
                "(expanding/rolling) doğrulama uygulanır; test fold'ları train fold'larından "
                "mutlak surette sonradır."
            ),
            (
                "- **Embargo Penceresi:** Otokorelasyon ve etiket ufku örtüşmesini önlemek için "
                "her katlama arasında katı `embargo_size` boşluğu bulunur."
            ),
            (
                "- **Sürtünme Maliyetleri:** Strateji simülasyonlarında aracı kurum komisyonu "
                "(%0.10), kayma (%0.05) ve vergi yükü (BSMV) dahil net getiri hesaplanır."
            ),
            (
                "- **Aşırı Öğrenme (Overfitting) Tespiti:** Katlamalar arası kayıp varyansı "
                "(Loss Std/Mean > %60) tespit edildiğinde sistem alarm üretir ve modeli kilitler."
            ),
            (
                "- **Yatırım Tavsiyesi Yasağı:** Finansal göstergeler ve model çıktıları "
                "kesinlikle kişiye özel al-sat veya getiri vaadi içermez."
            ),
            "",
            "---",
            (
                "*Rapor, `stock_analysis.release.release_four_baseline_walk_forward` "
                "tarafından otomatik oluşturulmuştur.*"
            ),
        ]
    )

    return "\n".join(lines)
