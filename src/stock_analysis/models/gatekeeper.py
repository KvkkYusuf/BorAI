"""Model yayınlama kapısı (Model Publishing Gate).

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Naif baseline'ı (son değer, tarihsel ortalama, basit momentum) ve işlem maliyetli
  gerçekçi kıyası geçemeyen hiçbir model kullanıcıya tahmin üretmek için yayınlanamaz.
- Baseline ve friksiyon şartını sağlamayan model için forecast_enabled=False yapılır.
- Sessiz fallback (hatalı/eski modeli sessizce çalıştırma veya varsayılan tahmin uydurma)
  kesinlikle yasaktır; kullanıcıya açıkça "tahmin şu an kapalı" durumu gösterilir.
- Tüm rapor ve kararlar SPK mevzuatına uygun yasal uyarı içerir.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.models.baselines import (
    ModelBenchmarkReport,
    evaluate_forecast_against_baseline,
)
from stock_analysis.models.strategy_backtest import (
    BacktestPerformanceReport,
    StrategyBacktestConfig,
    StrategySimulationResult,
    StrategySimulator,
    calculate_backtest_performance,
)


class ModelPublishingError(Exception):
    """Model yayınlama ve kapı kontrolü genel hatası."""


class ModelNotApprovedForPublishingError(ModelPublishingError):
    """Model naif baseline veya işlem maliyetli kıyası geçemediği için yayına alınamadı."""


DEFAULT_GATEKEEPER_DISCLAIMER = (
    "Bu model yayın onay değerlendirmesi, geriye dönük naif kıyas ve işlem maliyetli "
    "strateji simülasyonu sonuçlarına dayanır. Geçmiş verideki üstünlük veya kapı "
    "onayı gelecekteki sonuçların aynen gerçekleşeceğini ifade etmez veya bir vaat "
    "oluşturmaz. SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında olmayıp "
    "yalnızca model yayınlama kalite güvencesi amacıyla sunulmuştur."
)


@dataclass(frozen=True)
class GatekeeperConfig:
    """Model yayın onay kapısı yapılandırması."""

    require_baseline_beat: bool = True  # Model MAE/RMSE/Yön naif baseline'ı geçmeli
    require_positive_net_return: bool = True  # Friksiyon sonrası net getiri > 0 olmalı
    require_positive_sharpe: bool = True  # Net Sharpe oranı > 0 olmalı
    require_beat_buy_and_hold: bool = False  # İsteğe bağlı: Buy & hold kıyasını geçme şartı
    max_allowed_drawdown_pct: float = 35.0  # Azami kabul edilebilir düşüş (%35)
    min_trade_count: int = 2  # Asgari işlem adedi (şans eseri tek işlemi engellemek için)
    max_friction_drag_pct: float = 85.0  # Brüt getirinin friksiyona giden azami payı
    backtest_config: StrategyBacktestConfig = field(default_factory=StrategyBacktestConfig)

    def __post_init__(self) -> None:
        if self.max_allowed_drawdown_pct <= 0 or self.max_allowed_drawdown_pct > 100:
            raise ValueError(
                f"Geçersiz azami düşüş oranı: %{self.max_allowed_drawdown_pct}. "
                "(0, 100] aralığında olmalıdır."
            )
        if self.min_trade_count < 0:
            raise ValueError(f"Asgari işlem sayısı negatif olamaz: {self.min_trade_count}")


@dataclass(frozen=True)
class PublishingDecision:
    """Model yayınlama değerlendirme kararı ve karnesi."""

    model_name: str
    symbol: str
    is_approved: bool
    beats_baseline: bool
    beats_friction_cost: bool
    rejection_reasons: list[str]
    baseline_report: ModelBenchmarkReport | None
    backtest_report: BacktestPerformanceReport | None
    simulation_result: StrategySimulationResult | None
    recommended_forecast_enabled: bool
    user_facing_status: str
    user_facing_message: str
    disclaimer: str = DEFAULT_GATEKEEPER_DISCLAIMER

    def __post_init__(self) -> None:
        validate_compliant_text(self.disclaimer)

    def to_dict(self) -> dict[str, Any]:
        """Karar özetini JSON / API sözlüğü olarak döndürür."""
        return {
            "model_name": self.model_name,
            "symbol": self.symbol,
            "is_approved": self.is_approved,
            "beats_baseline": self.beats_baseline,
            "beats_friction_cost": self.beats_friction_cost,
            "rejection_reasons": list(self.rejection_reasons),
            "recommended_forecast_enabled": self.recommended_forecast_enabled,
            "user_facing_status": self.user_facing_status,
            "user_facing_message": self.user_facing_message,
            "baseline_report": (
                {
                    "baseline_name": self.baseline_report.baseline_name,
                    "model_mae": self.baseline_report.model_metrics.mae,
                    "baseline_mae": self.baseline_report.baseline_metrics.mae,
                    "model_rmse": self.baseline_report.model_metrics.rmse,
                    "baseline_rmse": self.baseline_report.baseline_metrics.rmse,
                    "model_directional_accuracy": (
                        self.baseline_report.model_metrics.directional_accuracy
                    ),
                    "baseline_directional_accuracy": (
                        self.baseline_report.baseline_metrics.directional_accuracy
                    ),
                    "relative_mae_improvement": self.baseline_report.relative_mae_improvement,
                    "relative_rmse_improvement": self.baseline_report.relative_rmse_improvement,
                    "beats_baseline": self.baseline_report.beats_baseline,
                }
                if self.baseline_report
                else None
            ),
            "backtest_report": (
                {
                    "net_return_pct": self.backtest_report.total_return_pct,
                    "annualized_return_pct": self.backtest_report.annualized_return_pct,
                    "sharpe_ratio": self.backtest_report.sharpe_ratio,
                    "max_drawdown_pct": self.backtest_report.max_drawdown_pct,
                    "daily_win_rate_pct": self.backtest_report.daily_win_rate_pct,
                }
                if self.backtest_report
                else None
            ),
            "simulation_result": (
                {
                    "gross_return_pct": self.simulation_result.gross_return_pct,
                    "net_return_pct": self.simulation_result.net_return_pct,
                    "friction_drag_pct": self.simulation_result.friction_drag_pct,
                    "total_trades": self.simulation_result.total_trades,
                    "total_friction_try": self.simulation_result.total_friction_try,
                }
                if self.simulation_result
                else None
            ),
            "disclaimer": self.disclaimer,
        }

    def format_summary(self) -> str:
        """Kullanıcı veya denetçi için biçimlendirilmiş metin raporu."""
        status_icon = "✅" if self.is_approved else "❌"
        lines = [
            f"# Model Yayınlama Kapısı Değerlendirmesi: {self.model_name} ({self.symbol})",
            "",
            f"**Durum:** {status_icon} {self.user_facing_status}",
            f"**FORECAST_ENABLED Önerisi:** `{self.recommended_forecast_enabled}`",
            "",
            "## Kapı Kontrolleri",
            f"- Naif Baseline Kapısı: {'Geçti ✅' if self.beats_baseline else 'Kaldı ❌'}",
            (
                "- İşlem Maliyetli Piyasa Kapısı: "
                f"{'Geçti ✅' if self.beats_friction_cost else 'Kaldı ❌'}"
            ),
        ]

        if self.baseline_report:
            b = self.baseline_report
            lines.extend(
                [
                    "",
                    "## 1. Naif Baseline Detayları",
                    f"- Kıyas Baseline: {b.baseline_name}",
                    (
                        f"- Model MAE: {b.model_metrics.mae:.4f} vs "
                        f"Baseline MAE: {b.baseline_metrics.mae:.4f}"
                    ),
                    (
                        f"- Model RMSE: {b.model_metrics.rmse:.4f} vs "
                        f"Baseline RMSE: {b.baseline_metrics.rmse:.4f}"
                    ),
                    (
                        f"- Model Yön Doğruluğu: "
                        f"%{b.model_metrics.directional_accuracy * 100:.1f} vs "
                        f"Baseline: %{b.baseline_metrics.directional_accuracy * 100:.1f}"
                    ),
                ]
            )

        if self.simulation_result and self.backtest_report:
            s = self.simulation_result
            rep = self.backtest_report
            lines.extend(
                [
                    "",
                    "## 2. Gerçekçi Friksiyon ve Strateji Detayları",
                    f"- Net Getiri: %{rep.total_return_pct:.2f}",
                    f"- Brüt Getiri: %{s.gross_return_pct:.2f}",
                    f"- Friksiyon Kaybı (Sürtünme): %{s.friction_drag_pct:.2f}",
                    f"- Net Sharpe Oranı: {rep.sharpe_ratio:.2f}",
                    f"- Maksimum Düşüş (MDD): %{rep.max_drawdown_pct:.2f}",
                    f"- Toplam İşlem Sayısı: {s.total_trades}",
                    f"- Toplam Friksiyon Maliyeti: {s.total_friction_try:.2f} TL",
                ]
            )

        if self.rejection_reasons:
            lines.extend(
                [
                    "",
                    "## Reddetme Nedenleri",
                ]
            )
            for reason in self.rejection_reasons:
                lines.append(f"- ⚠️ {reason}")

        lines.extend(
            [
                "",
                "## Yasal Uyarı",
                self.disclaimer,
            ]
        )
        return "\n".join(lines)


class ModelPublishingGate:
    """Naif baseline ve friksiyon kapısını denetleyen yayın onay motoru."""

    def __init__(self, config: GatekeeperConfig | None = None) -> None:
        self.config = config or GatekeeperConfig()

    def evaluate(
        self,
        y_true: Sequence[float],
        y_model: Sequence[float],
        y_baseline: Sequence[float],
        bars: pd.DataFrame,
        signals: Sequence[float],
        symbol: str = "BIST_SYM",
        model_name: str = "CandidateModel",
        baseline_name: str = "last_value",
        last_known_prices: Sequence[float] | None = None,
    ) -> PublishingDecision:
        """Modeli hem naif baseline hem de gerçekçi işlem maliyetli kıyasa karşı değerlendirir.

        Adımlar:
        1. Naif baseline karşılaştırması (evaluate_forecast_against_baseline).
        2. Gerçekçi piyasa sürtünmeleriyle strateji simülasyonu (StrategySimulator).
        3. Kapı kurallarının denetimi (net kârlılık, Sharpe, MDD, minimum işlem).
        4. Kesin ve şeffaf PublishingDecision üretimi.
        """
        rejection_reasons: list[str] = []

        # 1. Aşama: Naif Baseline Kapısı
        baseline_report = evaluate_forecast_against_baseline(
            y_true=y_true,
            y_model=y_model,
            y_baseline=y_baseline,
            last_known_prices=last_known_prices,
            model_name=model_name,
            baseline_name=baseline_name,
        )

        beats_baseline = bool(baseline_report.beats_baseline)
        if self.config.require_baseline_beat and not beats_baseline:
            reason = (
                f"Model naif baseline'ı ({baseline_name}) geçemedi: "
                f"{baseline_report.rejection_reason or 'Hata/yön metrikleri yetersiz'}."
            )
            rejection_reasons.append(reason)

        # 2. Aşama: Gerçekçi İşlem Maliyetli Piyasa Kapısı
        simulator = StrategySimulator(self.config.backtest_config)
        sim_result = simulator.simulate(bars=bars, signals=signals, symbol=symbol)
        backtest_report = calculate_backtest_performance(sim_result)

        beats_friction = True

        # 2a. Asgari işlem adedi kontrolü
        if sim_result.total_trades < self.config.min_trade_count:
            beats_friction = False
            rejection_reasons.append(
                f"Gerçekleşen işlem sayısı ({sim_result.total_trades}) "
                f"asgari sınırın ({self.config.min_trade_count}) altında."
            )

        # 2b. Net getiri pozitif olmalı
        if self.config.require_positive_net_return and sim_result.net_return_pct <= 0.0:
            beats_friction = False
            rejection_reasons.append(
                f"İşlem maliyeti (komisyon, makas, kayma) sonrası net getiri "
                f"(%{sim_result.net_return_pct:.2f}) pozitif değil."
            )

        # 2c. Net Sharpe oranı pozitif olmalı
        if self.config.require_positive_sharpe and backtest_report.sharpe_ratio <= 0.0:
            beats_friction = False
            rejection_reasons.append(
                f"İşlem maliyetleri sonrası Sharpe oranı ({backtest_report.sharpe_ratio:.2f}) "
                f"yetersiz (0 veya negatif)."
            )

        # 2d. Maksimum düşüş (Drawdown) kabul edilebilir sınırda olmalı
        if backtest_report.max_drawdown_pct > self.config.max_allowed_drawdown_pct:
            beats_friction = False
            rejection_reasons.append(
                f"Maksimum düşüş oranı (%{backtest_report.max_drawdown_pct:.2f}) "
                f"azami tolerans sınırını (%{self.config.max_allowed_drawdown_pct:.2f}) aştı."
            )

        # 2e. Aşırı friksiyon sürtünmesi (brüt kârın çoğunu komisyon/kayma yutuyor mu?)
        if (
            sim_result.gross_return_pct > 0.0
            and sim_result.friction_drag_pct > self.config.max_friction_drag_pct
        ):
            beats_friction = False
            rejection_reasons.append(
                f"Friksiyon sürtünme kaybı (%{sim_result.friction_drag_pct:.1f}), "
                f"azami izin verilen seviyeyi (%{self.config.max_friction_drag_pct:.1f}) aştı."
            )

        # 2f. Buy-and-Hold benchmark kıyası (isteğe bağlı bayrak)
        if self.config.require_beat_buy_and_hold:
            prices = bars["close"].to_numpy(dtype=float)
            bnh_return_pct = (
                ((prices[-1] - prices[0]) / prices[0]) * 100.0 if prices[0] > 0 else 0.0
            )
            if sim_result.net_return_pct <= bnh_return_pct:
                beats_friction = False
                rejection_reasons.append(
                    f"Net getiri (%{sim_result.net_return_pct:.2f}), "
                    f"Buy & Hold kıyas getirisinden (%{bnh_return_pct:.2f}) düşük/eşit."
                )

        # Genel Karar
        beats_friction = bool(beats_friction)
        is_approved = bool(
            (beats_baseline if self.config.require_baseline_beat else True) and beats_friction
        )

        recommended_forecast_enabled = bool(is_approved)

        if is_approved:
            user_status = "Yayına Uygun (Approved)"
            user_message = (
                "Model hem naif baseline kıyasını hem de işlem maliyetli "
                "gerçekçi simülasyon kriterlerini başarıyla geçmiştir. Yayına uygundur."
            )
        else:
            user_status = "Tahmin Kapalı (Model Reddedildi)"
            user_message = (
                "Tahmin şu an kapalı: Model naif baseline veya işlem maliyetli "
                "piyasa kıyasını geçemediği için kullanıcıya tahmin üretimi durdurulmuştur."
            )

        return PublishingDecision(
            model_name=model_name,
            symbol=symbol,
            is_approved=is_approved,
            beats_baseline=beats_baseline,
            beats_friction_cost=beats_friction,
            rejection_reasons=rejection_reasons,
            baseline_report=baseline_report,
            backtest_report=backtest_report,
            simulation_result=sim_result,
            recommended_forecast_enabled=recommended_forecast_enabled,
            user_facing_status=user_status,
            user_facing_message=user_message,
        )

    def enforce_approval(self, decision: PublishingDecision) -> None:
        """Model yayın onayını zorunlu kılar; onaylanmamışsa istisna fırlatır."""
        if not decision.is_approved:
            reasons_str = "; ".join(decision.rejection_reasons)
            raise ModelNotApprovedForPublishingError(
                f"Model '{decision.model_name}' yayına alınamaz veya tahmin üretemez! "
                f"Reddedilme Nedenleri: {reasons_str}"
            )

    @staticmethod
    def create_safe_serving_fallback(
        decision: PublishingDecision,
    ) -> dict[str, Any]:
        """Kullanıcıya veya API'ye şeffaf, sessizce uydurma tahmin üretmeyen yanıt üretir.

        Kural: Sessiz fallback yok. Kullanıcıya 'tahmin kapalı' gösterilir.
        """
        if decision.is_approved:
            return {
                "forecast_enabled": True,
                "status": "APPROVED",
                "symbol": decision.symbol,
                "model_name": decision.model_name,
                "message": decision.user_facing_message,
            }

        return {
            "forecast_enabled": False,
            "status": "FORECAST_DISABLED",
            "symbol": decision.symbol,
            "model_name": decision.model_name,
            "message": decision.user_facing_message,
            "reasons": list(decision.rejection_reasons),
            "disclaimer": decision.disclaimer,
        }
