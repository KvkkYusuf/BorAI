"""Birden çok deneme nedeniyle oluşabilecek backtest overfitting (aşırı uyum) analizi.

Bu modül, makine öğrenmesi ve nicel strateji geliştirmede birden çok deneme,
hiperparametre taraması ve özellik arayışı sonucu ortaya çıkan seçim yanlılığını
(selection bias / multiple testing problem) kayda alır ve aşağıdaki metrikleri hesaplar:
1. Deneme Defteri (Trial Registry): Yapılan tüm backtest ve strateji varyantlarının kaydı.
2. Beklenen Azami Sharpe (Expected Max Sharpe): N adet bağımsız deneme yapıldığında şans eseri
   oluşabilecek en yüksek Sharpe oranı eşiği.
3. Deflated Sharpe Ratio (DSR): Seçilen stratejinin Sharpe oranının, yapılan deneme sayısı
   ve getiri dağılımının çarpıklık/basıklığı (skewness/kurtosis) göz önünde bulundurularak
   şans eseri elde edilmemiş olma olasılığı (López de Prado & Bailey yaklaşımı).
4. Tıraşlanmış Sharpe (Haircut Sharpe): Çoklu test cezası uygulanmış gerçekçi Sharpe kestirimi.
5. Asgari Veri Uzunluğu (Minimum Track Record Length - MinTRL): İstatistiksel güven için
   gereken asgari işlem günü / bar sayısı.
6. Hukuki Uyum: SPK mevzuatına uygun standart uyarı ve risk bildirimleri.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import numpy as np

from stock_analysis.indicators.trend import validate_compliant_text


class OverfittingRiskLevel(StrEnum):
    """Backtest aşırı uyum risk seviyeleri."""

    LOW = "low"  # DSR >= 0.95: İstatistiksel olarak anlamlı, aşırı uyum riski düşük
    MODERATE = "moderate"  # 0.80 <= DSR < 0.95: Kabul edilebilir ancak dikkatli izlenmeli
    HIGH = "high"  # 0.50 <= DSR < 0.80: Yüksek aşırı uyum riski, seçim yanlılığı baskın
    CRITICAL = "critical"  # DSR < 0.50: Şans eseri oluşma olasılığı yüksek, kullanıma uygun değil


def standard_normal_cdf(x: float) -> float:
    """Standart normal dağılım kümülatif yoğunluk fonksiyonu (CDF)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def inverse_normal_cdf(p: float) -> float:
    """Standart normal dağılım ters kümülatif fonksiyonu (Acklam probit yaklaşımı)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError(f"Olasılık değeri (0, 1) açık aralığında olmalıdır: {p}")

    # Katsayılar (Peter J. Acklam algoritması)
    a1 = -3.969683028665376e01
    a2 = 2.209460984245205e02
    a3 = -2.759285104469687e02
    a4 = 1.383577518672690e02
    a5 = -3.066479806614716e01
    a6 = 2.506628277459239e00

    b1 = -5.447609879822406e01
    b2 = 1.615858368580409e02
    b3 = -1.556989798598866e02
    b4 = 6.680131188771972e01
    b5 = -1.328068155288572e01

    c1 = -7.784894002430293e-03
    c2 = -3.223964580411365e-01
    c3 = -2.400758277161838e00
    c4 = -2.549732539343734e00
    c5 = 4.374664141464968e00
    c6 = 2.938163982698783e00

    d1 = 7.784695709041462e-03
    d2 = 3.224671290700398e-01
    d3 = 2.445134137142996e00
    d4 = 3.754408661907416e00

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) / (
            (((d1 * q + d2) * q + d3) * q + d4) * q + 1.0
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        return ((((((a1 * r + a2) * r + a3) * r + a4) * r + a5) * r + a6) * q) / (
            ((((b1 * r + b2) * r + b3) * r + b4) * r + b5) * r + 1.0
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) / (
            (((d1 * q + d2) * q + d3) * q + d4) * q + 1.0
        )


def calculate_moments(returns: Sequence[float] | np.ndarray) -> tuple[float, float, float, float]:
    """Getiri dizisinden ortalama, standart sapma, çarpıklık ve basıklık hesaplar."""
    arr = np.asarray(returns, dtype=float)
    n = len(arr)
    if n < 3:
        raise ValueError(f"Moment hesaplaması için en az 3 getiri değeri gereklidir (mevcut: {n}).")

    mean = float(np.mean(arr))
    var = float(np.var(arr, ddof=1))
    std = float(np.sqrt(var)) if var > 1e-12 else 1e-6

    # Çarpıklık (Fisher-Pearson)
    diff = arr - mean
    m3 = float(np.mean(diff**3))
    skewness = m3 / (std**3) if std > 1e-12 else 0.0

    # Basıklık (Pearson: normal dağılım = 3.0)
    m4 = float(np.mean(diff**4))
    kurtosis = m4 / (std**4) if std > 1e-12 else 3.0

    return mean, std, skewness, kurtosis


@dataclass(frozen=True)
class BacktestTrialRecord:
    """Tek bir model / strateji backtest denemesi kaydı."""

    trial_id: str
    strategy_name: str
    symbol: str
    sharpe_ratio: float
    sample_bars: int
    skewness: float = 0.0
    kurtosis: float = 3.0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    parameters: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Deneme parametrelerini doğrular."""
        if self.sample_bars < 2:
            raise ValueError(f"sample_bars en az 2 olmalıdır: {self.sample_bars}")


class BacktestTrialRegistry:
    """Birden çok backtest denemesini toplayan deneme defteri."""

    def __init__(self) -> None:
        self._trials: list[BacktestTrialRecord] = []

    def register_trial(self, record: BacktestTrialRecord) -> None:
        """Yeni bir strateji denemesini kayda alır."""
        self._trials.append(record)

    @property
    def total_trials(self) -> int:
        """Kayıtlı toplam deneme sayısı."""
        return len(self._trials)

    def get_trials(self) -> tuple[BacktestTrialRecord, ...]:
        """Tüm deneme kayıtlarını döndürür."""
        return tuple(self._trials)

    def get_trial(self, trial_id: str) -> BacktestTrialRecord | None:
        """Belirtilen kimliğe sahip deneme kaydını döndürür."""
        for t in self._trials:
            if t.trial_id == trial_id:
                return t
        return None

    def get_sharpe_distribution(self) -> np.ndarray:
        """Kayıtlı tüm denemelerin Sharpe oranlarını dizi olarak döndürür."""
        if not self._trials:
            return np.array([], dtype=float)
        return np.array([t.sharpe_ratio for t in self._trials], dtype=float)


def calculate_expected_max_sharpe(n_trials: int, sharpe_std: float) -> float:
    """N bağımsız deneme yapıldığında beklenen maksimum Sharpe oranını hesaplar.

    López de Prado & Bailey (2014) asimptotik uç değer yaklaşımı.
    """
    if n_trials <= 1:
        return 0.0
    if sharpe_std <= 0.0:
        return 0.0

    euler_mascheroni = 0.57721566490153286
    z1 = inverse_normal_cdf(1.0 - (1.0 / n_trials))
    z2 = inverse_normal_cdf(1.0 - (1.0 / (n_trials * math.e)))

    exp_max_z = (1.0 - euler_mascheroni) * z1 + euler_mascheroni * z2
    return float(sharpe_std * exp_max_z)


def calculate_deflated_sharpe_ratio(
    selected_sharpe: float,
    sample_bars: int,
    n_trials: int,
    sharpe_std: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (DSR) hesaplar.

    DSR, çoklu test ortamında seçilen stratejinin beklenen maksimum Sharpe oranını
    (selection bias) geride bırakma olasılığını (0.0 ile 1.0 arası) ifade eder.
    """
    if sample_bars <= 2:
        return 0.0

    exp_max_sr = calculate_expected_max_sharpe(n_trials, sharpe_std)

    # Normal olmayan getiri düzeltmesi (Mertens 2002 / Lo 2002 varyansı)
    # var(SR) = (1 - skew * SR + (kurt - 1)/4 * SR^2) / (T - 1)
    kurt_term = ((kurtosis - 1.0) / 4.0) * (selected_sharpe**2)
    variance_term = 1.0 - (skewness * selected_sharpe) + kurt_term
    variance_term = max(1e-8, variance_term)
    sr_std_error = math.sqrt(variance_term / (sample_bars - 1))

    z_score = (selected_sharpe - exp_max_sr) / sr_std_error
    return float(standard_normal_cdf(z_score))


def calculate_haircut_sharpe_ratio(
    selected_sharpe: float,
    n_trials: int,
    sample_bars: int,
) -> float:
    """Çoklu test cezası uygulanmış tıraşlanmış (haircut) Sharpe oranını hesaplar.

    Harvey & Liu (2015) sezgisel haircut yaklaşımı.
    """
    if selected_sharpe <= 0:
        return selected_sharpe
    if n_trials <= 1:
        return selected_sharpe

    # Deneme sayısı ve örneklem büyüklüğüne göre haircut faktörü
    # log2(N) ile artan, örneklem uzadıkça hafifleyen ceza
    discount_factor = math.sqrt(math.log(n_trials + 1) / math.log(2.0)) * 0.15
    discount_factor = min(0.85, max(0.05, discount_factor))

    return float(selected_sharpe * (1.0 - discount_factor))


def calculate_minimum_track_record_length(
    sharpe: float,
    n_trials: int,
    sharpe_std: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    alpha: float = 0.05,
) -> int:
    """Stratejinin anlamlı olması için gereken asgari bar sayısını (MinTRL) hesaplar."""
    if sharpe <= 0:
        return 999_999

    exp_max_sr = calculate_expected_max_sharpe(n_trials, sharpe_std)
    excess_sr = sharpe - exp_max_sr
    if excess_sr <= 0:
        return 999_999

    z_alpha = inverse_normal_cdf(1.0 - alpha)
    variance_term = 1.0 - (skewness * sharpe) + (((kurtosis - 1.0) / 4.0) * (sharpe**2))
    variance_term = max(1e-8, variance_term)

    min_t = 1.0 + variance_term * ((z_alpha / excess_sr) ** 2)
    return int(math.ceil(min_t))


DEFAULT_OVERFITTING_DISCLAIMER = (
    "Bu analiz, birden çok model ve strateji denemesinden kaynaklanan seçim yanlılığı "
    "(selection bias) ve aşırı uyum (overfitting) riskini istatistiksel yöntemlerle "
    "değerlendirmektedir. Yatırım danışmanlığı, getiri taahhüdü veya kâr vaadi içermez. "
    "Geçmiş deneme sonuçları gelecekteki performansı temsil etmez."
)


@dataclass(frozen=True)
class BacktestOverfittingReport:
    """Çoklu test ve backtest aşırı uyum değerlendirme raporu."""

    strategy_name: str
    selected_trial_id: str
    symbol: str
    total_trials_tested: int
    nominal_sharpe: float
    expected_max_sharpe: float
    deflated_sharpe_ratio: float
    haircut_sharpe: float
    min_track_record_length_bars: int
    actual_bars: int
    risk_level: OverfittingRiskLevel
    is_statistically_significant: bool
    warning_message: str
    disclaimer: str

    def __post_init__(self) -> None:
        """Yasal metin uyumunu denetler."""
        validate_compliant_text(self.disclaimer)

    def to_dict(self) -> dict[str, object]:
        """Rapor verilerini sözlük formatında döndürür."""
        return {
            "strategy_name": self.strategy_name,
            "selected_trial_id": self.selected_trial_id,
            "symbol": self.symbol,
            "total_trials_tested": self.total_trials_tested,
            "nominal_sharpe": round(self.nominal_sharpe, 4),
            "expected_max_sharpe": round(self.expected_max_sharpe, 4),
            "deflated_sharpe_ratio": round(self.deflated_sharpe_ratio, 4),
            "haircut_sharpe": round(self.haircut_sharpe, 4),
            "min_track_record_length_bars": self.min_track_record_length_bars,
            "actual_bars": self.actual_bars,
            "risk_level": self.risk_level.value,
            "is_statistically_significant": self.is_statistically_significant,
            "warning_message": self.warning_message,
            "disclaimer": self.disclaimer,
        }

    def format_summary(self) -> str:
        """Kullanıcı dostu metin özeti oluşturur."""
        lines = [
            f"# Backtest Aşırı Uyum (Overfitting) Raporu: {self.strategy_name}",
            f"Sembol: {self.symbol} (Deneme ID: {self.selected_trial_id})",
            "",
            "## Çoklu Test ve İstatistiksel Anlamlılık",
            f"- Toplam Yapılan Deneme Sayısı: {self.total_trials_tested}",
            f"- Nominal Sharpe Oranı: {self.nominal_sharpe:.2f}",
            f"- Şans Eseri Beklenen Azami Sharpe (Hurdle): {self.expected_max_sharpe:.2f}",
            f"- Düzeltilmiş Sharpe (DSR): %{self.deflated_sharpe_ratio * 100.0:.1f}",
            f"- Tıraşlanmış Gerçekçi Sharpe (Haircut Sharpe): {self.haircut_sharpe:.2f}",
            f"- Gerekli Asgari Veri Uzunluğu (MinTRL): {self.min_track_record_length_bars} bar "
            f"(Mevcut: {self.actual_bars} bar)",
            "",
            "## Risk Değerlendirmesi",
            f"- Risk Seviyesi: {self.risk_level.value.upper()}",
            (
                "- İstatistiksel Anlamlılık: "
                + ("GEÇTİ (p < 0.05)" if self.is_statistically_significant else "KALDI (p >= 0.05)")
            ),
            f"- Uyarı / Açıklama: {self.warning_message}",
            "",
            "## Yasal Uyarı",
            f"> {self.disclaimer}",
        ]
        return "\n".join(lines)


def evaluate_backtest_overfitting(
    registry: BacktestTrialRegistry,
    selected_trial_id: str | None = None,
    disclaimer: str = DEFAULT_OVERFITTING_DISCLAIMER,
) -> BacktestOverfittingReport:
    """Deneme defterindeki kayıtları analiz ederek aşırı uyum raporunu oluşturur."""
    trials = registry.get_trials()
    if not trials:
        raise ValueError("Overfitting değerlendirmesi için en az bir deneme kaydı bulunmalıdır.")

    if selected_trial_id is not None:
        target_trial = registry.get_trial(selected_trial_id)
        if target_trial is None:
            raise KeyError(f"Trial ID bulunamadı: {selected_trial_id}")
    else:
        # Varsayılan olarak en yüksek Sharpe oranına sahip deneme incelenir
        target_trial = max(trials, key=lambda t: t.sharpe_ratio)

    n_trials = registry.total_trials
    sharpe_dist = registry.get_sharpe_distribution()
    sharpe_std = float(np.std(sharpe_dist)) if len(sharpe_dist) > 1 else 0.50

    exp_max_sr = calculate_expected_max_sharpe(n_trials, sharpe_std)
    dsr = calculate_deflated_sharpe_ratio(
        selected_sharpe=target_trial.sharpe_ratio,
        sample_bars=target_trial.sample_bars,
        n_trials=n_trials,
        sharpe_std=sharpe_std,
        skewness=target_trial.skewness,
        kurtosis=target_trial.kurtosis,
    )
    haircut_sr = calculate_haircut_sharpe_ratio(
        selected_sharpe=target_trial.sharpe_ratio,
        n_trials=n_trials,
        sample_bars=target_trial.sample_bars,
    )
    min_trl = calculate_minimum_track_record_length(
        sharpe=target_trial.sharpe_ratio,
        n_trials=n_trials,
        sharpe_std=sharpe_std,
        skewness=target_trial.skewness,
        kurtosis=target_trial.kurtosis,
    )

    is_significant = dsr >= 0.95 and target_trial.sample_bars >= min_trl

    if dsr >= 0.95:
        risk_level = OverfittingRiskLevel.LOW
        warning_msg = (
            f"{n_trials} deneme sonrasında dahi Sharpe oranı çoklu test eşiğini aşmıştır; "
            "aşırı uyum riski düşüktür."
        )
    elif dsr >= 0.80:
        risk_level = OverfittingRiskLevel.MODERATE
        warning_msg = (
            "Sharpe oranı kabul edilebilir düzeydedir ancak yapılan deneme sayısı nedeniyle "
            "ılımlı bir seçim yanlılığı mevcuttur."
        )
    elif dsr >= 0.50:
        risk_level = OverfittingRiskLevel.HIGH
        warning_msg = (
            "Yapılan çok sayıda deneme nedeniyle bulunan yüksek getiri şans eseri olabilir; "
            "gerçek piyasada performans düşüşü muhtemeldir."
        )
    else:
        risk_level = OverfittingRiskLevel.CRITICAL
        warning_msg = (
            "DSR < 0.50: Model performansı tamamen backtest overfitting belirtisi göstermektedir. "
            "Kullanıcıya tahmin üretmek için yayınlanamaz."
        )

    return BacktestOverfittingReport(
        strategy_name=target_trial.strategy_name,
        selected_trial_id=target_trial.trial_id,
        symbol=target_trial.symbol,
        total_trials_tested=n_trials,
        nominal_sharpe=target_trial.sharpe_ratio,
        expected_max_sharpe=exp_max_sr,
        deflated_sharpe_ratio=dsr,
        haircut_sharpe=haircut_sr,
        min_track_record_length_bars=min_trl,
        actual_bars=target_trial.sample_bars,
        risk_level=risk_level,
        is_statistically_significant=is_significant,
        warning_message=warning_msg,
        disclaimer=disclaimer,
    )
