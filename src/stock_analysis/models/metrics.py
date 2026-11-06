"""Finansal zaman serisi tahmin modelleri için değerlendirme metrikleri.

FAZ-11 gereksinimi:
- MAE / RMSE yönü değerlendirmek için tek başına yeterli DEĞİLDİR.
- Regresyon büyüklük hatası küçük olsa dahi yanlış yönde tahmin yapmak sermaye kaybına yol açar.
- Yön doğruluğu (Directional Accuracy), istatistiksel işaret testi (Sign Test),
  Theil's U kıyası, asimetrik finansal kayıp, tahmin aralığı kapsaması (PICP/Winkler)
  ve olasılık kalibrasyonu (ECE/Brier) eksiksiz raporlanır.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DirectionalAssessment(StrEnum):
    """Tahminin finansal yön başarı derecesi."""

    EXCELLENT = "excellent"  # DA > 60% ve p < 0.05
    FAVORABLE = "favorable"  # DA > 53% ve naif kıyası geçiyor
    RANDOM_WALK = "random_walk"  # 47% <= DA <= 53% (şans eseri / yazı-tura)
    INVERSE_OR_POOR = "inverse_or_poor"  # DA < 47% (ters yönde veya yanıltıcı)


class SignTestResult(BaseModel):
    """İşaret testi (Sign Test) istatistiksel anlamlılık sonucu.

    H0: Modelin yön tahmin başarısı rastgele tahminden (%50) farksızdır.
    """

    model_config = ConfigDict(frozen=True)

    success_count: int = Field(ge=0, description="Doğru yön tahmin sayısı")
    trial_count: int = Field(ge=0, description="Değerlendirilen toplam tahmin sayısı")
    hit_ratio: float = Field(ge=0.0, le=1.0, description="Yön doğruluk oranı (DA)")
    z_score: float = Field(description="Standartlaştırılmış Z istatistiği")
    p_value: float = Field(ge=0.0, le=1.0, description="Çift taraflı p-değeri")
    is_statistically_significant: bool = Field(
        description="p < 0.05 ise rastlantısal olmadığı kabul edilir"
    )


class DirectionalPnLResult(BaseModel):
    """Tahmin yönüne göre alınan teorik pozisyonların ekonomik getiri analizi."""

    model_config = ConfigDict(frozen=True)

    total_trades: int = Field(ge=0)
    cumulative_return: float = Field(
        description="Tahmin işaretiyle ağırlıklandırılmış toplam getiri"
    )
    mean_return_per_trade: float = Field(description="İşlem başına ortalama getiri")
    win_rate: float = Field(ge=0.0, le=1.0, description="Karlı işlem oranı")
    profit_factor: float = Field(ge=0.0, description="Brüt kar / brüt zarar oranı")


class IntervalCoverageResult(BaseModel):
    """Tahmin aralığı kapsaması (Prediction Interval Coverage) analizi."""

    model_config = ConfigDict(frozen=True)

    nominal_coverage: float = Field(
        ge=0.0, le=1.0, description="Hedeflenen güven düzeyi (ör. 0.90)"
    )
    picp: float = Field(
        ge=0.0,
        le=1.0,
        description="Gerçekleşen kapsama oranı (Prediction Interval Coverage Probability)",
    )
    mpiw: float = Field(ge=0.0, description="Ortalama tahmin aralığı genişliği (Mean Width)")
    mean_winkler_score: float = Field(
        ge=0.0, description="Aralık genişliği ve dış taşma cezasını birleştiren Winkler skoru"
    )
    is_well_calibrated: bool = Field(
        description="Gerçekleşen PICP hedeflenen kapsamanın tolerans aralığında mı?"
    )


class CalibrationResult(BaseModel):
    """Yön olasılıkları için güvenilirlik ve kalibrasyon hatası."""

    model_config = ConfigDict(frozen=True)

    brier_score: float = Field(ge=0.0, le=1.0, description="Brier skoru (0 mükemmel)")
    expected_calibration_error: float = Field(
        ge=0.0, le=1.0, description="Beklenen Kalibrasyon Hatası (ECE)"
    )
    bin_confidences: list[float]
    bin_accuracies: list[float]
    bin_counts: list[int]


class FinancialMetricsReport(BaseModel):
    """Finansal zaman serisi tahminlerinin kapsamlı değerlendirme raporu."""

    model_config = ConfigDict(frozen=True)

    sample_count: int = Field(ge=1)
    mae: float = Field(ge=0.0, description="Ortalama Mutlak Hata")
    rmse: float = Field(ge=0.0, description="Kök Ortalama Kare Hata")
    mape: float | None = Field(default=None, description="Ortalama Mutlak Yüzde Hata")
    smape: float | None = Field(default=None, description="Simetrik MAPE")
    directional_accuracy: float = Field(ge=0.0, le=1.0, description="Yön doğruluğu")
    theils_u: float = Field(ge=0.0, description="Theil's U istatistiği (< 1 naif baseline'dan iyi)")
    direction_penalized_loss: float = Field(
        ge=0.0, description="Ters yön cezalı asimetrik finansal kayıp"
    )
    sign_test: SignTestResult
    directional_pnl: DirectionalPnLResult
    interval_coverage: IntervalCoverageResult | None = None
    calibration: CalibrationResult | None = None
    assessment: DirectionalAssessment
    beats_naive_baseline: bool = Field(
        description="Theil's U < 1.0 ve DA > 0.50 şartlarını sağlıyor mu?"
    )
    magnitude_directional_conflict: bool = Field(
        description="MAE düşük olmasına rağmen yön doğruluğunun şans düzeyinde kalması çelişkisi"
    )
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Temel Büyüklük (Magnitude) Metrikleri
# ---------------------------------------------------------------------------


def _validate_equal_nonempty(y_true: Sequence[float], y_pred: Sequence[float]) -> None:
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"Dizi boyutları uyuşmuyor: len(y_true)={len(y_true)}, len(y_pred)={len(y_pred)}"
        )
    if not y_true:
        raise ValueError("Girdi serileri boş olamaz.")


def calculate_mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Ortalama Mutlak Hata (Mean Absolute Error)."""
    _validate_equal_nonempty(y_true, y_pred)
    return float(sum(abs(t - p) for t, p in zip(y_true, y_pred, strict=True)) / len(y_true))


def calculate_rmse(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Kök Ortalama Kare Hata (Root Mean Squared Error)."""
    _validate_equal_nonempty(y_true, y_pred)
    mse = sum((t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)
    return float(math.sqrt(mse))


def calculate_mape(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    epsilon: float = 1e-8,
) -> float:
    """Ortalama Mutlak Yüzde Hata (Mean Absolute Percentage Error).

    Sıfıra çok yakın paydalarda sayısal patlamayı engellemek için epsilon kullanılır.
    """
    _validate_equal_nonempty(y_true, y_pred)

    total = sum(
        abs((t - p) / (abs(t) if abs(t) > epsilon else epsilon))
        for t, p in zip(y_true, y_pred, strict=True)
    )
    return float((total / len(y_true)) * 100.0)


def calculate_smape(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    epsilon: float = 1e-8,
) -> float:
    """Simetrik Ortalama Mutlak Yüzde Hata (Symmetric MAPE)."""
    _validate_equal_nonempty(y_true, y_pred)

    total = 0.0
    for t, p in zip(y_true, y_pred, strict=True):
        denom = abs(t) + abs(p)
        if denom > epsilon:
            total += 2.0 * abs(t - p) / denom
    return float((total / len(y_true)) * 100.0)


# ---------------------------------------------------------------------------
# Finansal Yön ve İstatistiksel Anlamlılık Metrikleri
# ---------------------------------------------------------------------------


def calculate_directional_accuracy(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    threshold: float = 0.0,
) -> float:
    """Tahminin getiri yönünü (artış/düşüş) doğru bilme oranı.

    Args:
        y_true: Gerçekleşen getiri serisi.
        y_pred: Model tarafından tahmin edilen getiri serisi.
        threshold: Yön ayrımı için nötr bant eşiği (varsayılan 0.0).

    Returns:
        [0.0, 1.0] aralığında yön doğruluk oranı.
    """
    _validate_equal_nonempty(y_true, y_pred)

    correct = 0
    total = len(y_true)
    for t, p in zip(y_true, y_pred, strict=True):
        sign_t = 1 if t > threshold else (-1 if t < -threshold else 0)
        sign_p = 1 if p > threshold else (-1 if p < -threshold else 0)

        # Eğer iki işaret de aynı yöndeyse doğru kabul et
        if sign_t == sign_p:
            correct += 1

    return float(correct / total)


def calculate_sign_test(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    threshold: float = 0.0,
    alpha: float = 0.05,
) -> SignTestResult:
    """Yön tahmin başarısının rastlantısal (yazı-tura) olmadığını test eden işaret testi.

    H0: Modelin başarı olasılığı p = 0.50 (yazı-tura).
    H1: Modelin başarı olasılığı p != 0.50.
    """
    _validate_equal_nonempty(y_true, y_pred)

    n = len(y_true)
    k = 0
    for t, p in zip(y_true, y_pred, strict=True):
        sign_t = 1 if t > threshold else (-1 if t < -threshold else 0)
        sign_p = 1 if p > threshold else (-1 if p < -threshold else 0)
        if sign_t == sign_p:
            k += 1

    hit_ratio = k / n

    # Normal yaklaşımı (süreklilik düzeltmesiyle birlikte)
    expected = 0.5 * n
    std = math.sqrt(0.25 * n)

    diff = abs(k - expected)
    corrected_diff = max(0.0, diff - 0.5)

    if std > 0:
        z = (corrected_diff / std) if (k >= expected) else -(corrected_diff / std)
    else:
        z = 0.0

    # İki taraflı p-değeri: 2 * (1 - Phi(|z|)) = erfc(|z| / sqrt(2))
    p_value = float(math.erfc(abs(z) / math.sqrt(2.0)))
    p_value = min(1.0, max(0.0, p_value))

    is_sig = bool(p_value < alpha and hit_ratio > 0.5)

    return SignTestResult(
        success_count=k,
        trial_count=n,
        hit_ratio=round(hit_ratio, 6),
        z_score=round(z, 4),
        p_value=round(p_value, 6),
        is_statistically_significant=is_sig,
    )


def calculate_theils_u(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    y_naive: Sequence[float] | None = None,
) -> float:
    """Theil's U istatistiği (Theil's U2).

    Tahmin modelinin RMSE'sinin naif baseline modelin RMSE'sine oranıdır:
    - U < 1.0: Model naif kıyastan DAHA İYİ performans gösteriyor.
    - U = 1.0: Model naif kıyastan farksız.
    - U > 1.0: Model naif kıyastan DAHA KÖTÜ (kullanılamaz!).

    Eğer y_naive verilmezse, getiri tahminlerinde naif kıyas 'sıfır getiri' kabul edilir.
    """
    _validate_equal_nonempty(y_true, y_pred)

    if y_naive is not None and len(y_naive) != len(y_true):
        raise ValueError("y_naive uzunluğu y_true ile eşleşmelidir.")

    model_mse = sum((t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)

    if y_naive is None:
        # Naif tahmin sıfır getiri (random walk drift = 0)
        naive_mse = sum(t**2 for t in y_true) / len(y_true)
    else:
        naive_mse = sum((t - n) ** 2 for t, n in zip(y_true, y_naive, strict=True)) / len(y_true)

    if naive_mse <= 1e-12:
        return 1.0 if model_mse <= 1e-12 else float("inf")

    return float(math.sqrt(model_mse / naive_mse))


def calculate_direction_penalized_loss(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    penalty_factor: float = 2.5,
) -> float:
    """Yön uyuşmazlığını büyüklük hatasından katbekat ağır cezalandıran finansal kayıp.

    Eğer tahmin ile gerçekleşen getiri zıt işaretliyse, kayıp `penalty_factor` ile çarpılır.
    """
    _validate_equal_nonempty(y_true, y_pred)
    if penalty_factor < 1.0:
        raise ValueError("penalty_factor 1.0'dan küçük olamaz.")

    total_loss = 0.0
    for t, p in zip(y_true, y_pred, strict=True):
        error = abs(t - p)
        # Zıt işaret kontrolü
        if (t > 0 and p < 0) or (t < 0 and p > 0):
            total_loss += penalty_factor * error
        else:
            total_loss += error

    return float(total_loss / len(y_true))


def calculate_directional_pnl(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    threshold: float = 0.0,
) -> DirectionalPnLResult:
    """Tahminin işaretine göre pozisyon alındığında oluşan teorik getiri metrikleri."""
    _validate_equal_nonempty(y_true, y_pred)

    returns: list[float] = []
    wins: list[float] = []
    losses: list[float] = []

    for t, p in zip(y_true, y_pred, strict=True):
        pos = 1.0 if p > threshold else (-1.0 if p < -threshold else 0.0)
        ret = pos * t
        returns.append(ret)
        if ret > 0:
            wins.append(ret)
        elif ret < 0:
            losses.append(abs(ret))

    cum_ret = sum(returns)
    mean_ret = cum_ret / len(returns)
    win_rate = len(wins) / len(returns) if returns else 0.0

    total_win = sum(wins)
    total_loss = sum(losses)
    if total_loss > 1e-9:
        profit_factor = total_win / total_loss
    else:
        profit_factor = 999.0 if total_win > 0 else 1.0

    return DirectionalPnLResult(
        total_trades=len(returns),
        cumulative_return=round(cum_ret, 6),
        mean_return_per_trade=round(mean_ret, 6),
        win_rate=round(win_rate, 4),
        profit_factor=round(min(profit_factor, 999.0), 4),
    )


# ---------------------------------------------------------------------------
# Tahmin Aralığı Kapsaması ve Kalibrasyon
# ---------------------------------------------------------------------------


def calculate_interval_coverage(
    y_true: Sequence[float],
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
    nominal_coverage: float = 0.90,
    tolerance: float = 0.05,
) -> IntervalCoverageResult:
    """Tahmin aralıklarının kapsaması (PICP), genişliği (MPIW) ve Winkler skorunu hesaplar."""
    n = len(y_true)
    if len(lower_bounds) != n or len(upper_bounds) != n:
        raise ValueError("y_true, lower_bounds ve upper_bounds eşit uzunlukta olmalıdır.")
    if n == 0:
        raise ValueError("Girdi serileri boş olamaz.")

    covered_count = 0
    total_width = 0.0
    winkler_scores: list[float] = []
    alpha = 1.0 - nominal_coverage
    inv_alpha = 2.0 / alpha if alpha > 1e-6 else 2.0

    for t, low, high in zip(y_true, lower_bounds, upper_bounds, strict=True):
        if low > high:
            raise ValueError(f"Alt sınır üst sınırdan büyük olamaz: low={low}, high={high}")

        width = high - low
        total_width += width

        if low <= t <= high:
            covered_count += 1
            winkler_scores.append(width)
        elif t < low:
            winkler_scores.append(width + inv_alpha * (low - t))
        else:  # t > high
            winkler_scores.append(width + inv_alpha * (t - high))

    picp = covered_count / n
    mpiw = total_width / n
    mean_winkler = sum(winkler_scores) / n

    is_calibrated = abs(picp - nominal_coverage) <= tolerance

    return IntervalCoverageResult(
        nominal_coverage=nominal_coverage,
        picp=round(picp, 4),
        mpiw=round(mpiw, 6),
        mean_winkler_score=round(mean_winkler, 6),
        is_well_calibrated=is_calibrated,
    )


def calculate_brier_score(
    y_true_binary: Sequence[int],
    probabilities: Sequence[float],
) -> float:
    """İkili sınıflandırma (artış/düşüş) olasılıkları için Brier Skoru."""
    if len(y_true_binary) != len(probabilities):
        raise ValueError("y_true_binary ve probabilities eşit uzunlukta olmalıdır.")
    if not y_true_binary:
        raise ValueError("Girdi serileri boş olamaz.")
    return float(
        sum((p - y) ** 2 for y, p in zip(y_true_binary, probabilities, strict=True))
        / len(y_true_binary)
    )


def calculate_expected_calibration_error(
    y_true_binary: Sequence[int],
    probabilities: Sequence[float],
    n_bins: int = 5,
) -> float:
    """Beklenen Kalibrasyon Hatası (Expected Calibration Error - ECE)."""
    calib = calculate_calibration(y_true_binary, probabilities, n_bins=n_bins)
    return calib.expected_calibration_error


def calculate_calibration(
    y_true_binary: Sequence[int],
    probabilities: Sequence[float],
    n_bins: int = 5,
) -> CalibrationResult:
    """Olasılık tahminleri için Brier Skoru ve Beklenen Kalibrasyon Hatası (ECE).

    Args:
        y_true_binary: Gerçekleşen ikili etiketler (1: pozitif/yükseliş, 0: negatif/düşüş).
        probabilities: Model tarafından üretilen pozitif sınıf olasılıkları [0, 1].
        n_bins: ECE için ayrılacak güven aralığı kutusu sayısı.
    """
    n = len(y_true_binary)
    if len(probabilities) != n:
        raise ValueError("y_true_binary ve probabilities eşit uzunlukta olmalıdır.")
    if n == 0:
        raise ValueError("Girdi serileri boş olamaz.")

    # Brier Skoru
    brier = calculate_brier_score(y_true_binary, probabilities)

    # ECE hesaplama
    bin_edges = [i / n_bins for i in range(n_bins + 1)]
    bin_confidences: list[float] = []
    bin_accuracies: list[float] = []
    bin_counts: list[int] = []

    ece = 0.0

    for b in range(n_bins):
        low = bin_edges[b]
        high = bin_edges[b + 1]

        # Son kutu için sağ sınır kapalı
        bin_indices = [
            i
            for i, p in enumerate(probabilities)
            if ((low <= p <= high) if b == n_bins - 1 else (low <= p < high))
        ]

        count = len(bin_indices)
        bin_counts.append(count)

        if count > 0:
            bin_conf = sum(probabilities[i] for i in bin_indices) / count
            bin_acc = sum(y_true_binary[i] for i in bin_indices) / count
            bin_confidences.append(round(bin_conf, 4))
            bin_accuracies.append(round(bin_acc, 4))
            ece += (count / n) * abs(bin_acc - bin_conf)
        else:
            bin_confidences.append(round((low + high) / 2.0, 4))
            bin_accuracies.append(0.0)

    return CalibrationResult(
        brier_score=round(brier, 6),
        expected_calibration_error=round(ece, 6),
        bin_confidences=bin_confidences,
        bin_accuracies=bin_accuracies,
        bin_counts=bin_counts,
    )


# ---------------------------------------------------------------------------
# Kapsamlı Finansal Değerlendirme Raporlayıcısı
# ---------------------------------------------------------------------------


def evaluate_financial_forecasts(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    y_naive: Sequence[float] | None = None,
    lower_bounds: Sequence[float] | None = None,
    upper_bounds: Sequence[float] | None = None,
    probabilities: Sequence[float] | None = None,
    nominal_coverage: float = 0.90,
    penalty_factor: float = 2.5,
) -> FinancialMetricsReport:
    """Tüm büyüklük, yön, kıyas ve kalibrasyon metriklerini birleştiren denetim raporu."""
    n = len(y_true)
    if n == 0:
        raise ValueError("y_true boş olamaz.")

    mae = calculate_mae(y_true, y_pred)
    rmse = calculate_rmse(y_true, y_pred)
    mape = calculate_mape(y_true, y_pred)
    smape = calculate_smape(y_true, y_pred)
    da = calculate_directional_accuracy(y_true, y_pred)
    sign_test = calculate_sign_test(y_true, y_pred)
    theils_u = calculate_theils_u(y_true, y_pred, y_naive=y_naive)
    dpl = calculate_direction_penalized_loss(y_true, y_pred, penalty_factor=penalty_factor)
    pnl = calculate_directional_pnl(y_true, y_pred)

    interval_result: IntervalCoverageResult | None = None
    if lower_bounds is not None and upper_bounds is not None:
        interval_result = calculate_interval_coverage(
            y_true, lower_bounds, upper_bounds, nominal_coverage=nominal_coverage
        )

    calib_result: CalibrationResult | None = None
    if probabilities is not None:
        binary_actuals = [1 if y > 0 else 0 for y in y_true]
        calib_result = calculate_calibration(binary_actuals, probabilities)

    # Değerlendirme derecesi
    if da >= 0.60 and sign_test.is_statistically_significant:
        assessment = DirectionalAssessment.EXCELLENT
    elif da >= 0.53 and theils_u < 1.0:
        assessment = DirectionalAssessment.FAVORABLE
    elif 0.47 <= da < 0.53:
        assessment = DirectionalAssessment.RANDOM_WALK
    else:
        assessment = DirectionalAssessment.INVERSE_OR_POOR

    beats_naive = bool(theils_u < 1.0 and da > 0.50)

    # MAE düşük ama yön başarısızlığı çelişkisi kontrolü
    # MAE < 0.02 (ör. getiri hatası küçük) ama DA <= 0.50 ise yanıltıcı düşük hata uyarısı
    conflict = bool(da <= 0.50 and theils_u >= 0.95)

    warnings: list[str] = []
    if conflict:
        warnings.append(
            "KRİTİK ÇELİŞKİ: Modelin büyüklük hatası (MAE/RMSE) düşük görünse de "
            f"yön doğruluğu ({da:.1%}) şans düzeyinin (%50) altındadır! "
            "Tek başına MAE/RMSE başarısı finansal karlıığı yansıtmaz."
        )
    if theils_u >= 1.0:
        warnings.append(
            f"BAŞARISIZLIK: Theil's U ({theils_u:.4f}) >= 1.0. "
            "Model basit naif kıyastan (drift=0 veya son değer) daha kötü tahmin üretiyor."
        )
    if not sign_test.is_statistically_significant:
        warnings.append(
            f"İSTATİSTİKSEL ANLAMSIZLIK: İşaret testi p-değeri ({sign_test.p_value:.4f}) >= 0.05. "
            "Yön başarısının yazı-tura atışından farksız olma ihtimali yüksektir."
        )
    if interval_result and not interval_result.is_well_calibrated:
        warnings.append(
            f"GÜVEN ARALIĞI HATASI: Gerçekleşen kapsama ({interval_result.picp:.1%}), "
            f"nominal hedeften ({interval_result.nominal_coverage:.1%}) sapmaktadır."
        )

    return FinancialMetricsReport(
        sample_count=n,
        mae=round(mae, 6),
        rmse=round(rmse, 6),
        mape=round(mape, 4),
        smape=round(smape, 4),
        directional_accuracy=round(da, 4),
        theils_u=round(theils_u, 4),
        direction_penalized_loss=round(dpl, 6),
        sign_test=sign_test,
        directional_pnl=pnl,
        interval_coverage=interval_result,
        calibration=calib_result,
        assessment=assessment,
        beats_naive_baseline=beats_naive,
        magnitude_directional_conflict=conflict,
        warnings=warnings,
    )
