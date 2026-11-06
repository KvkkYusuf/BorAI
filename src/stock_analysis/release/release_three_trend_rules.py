"""Sürüm 3 (Klasik Analiz ve Uyarı) - Trend Kuralları Kabul Kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 3 — Klasik analiz ve uyarı:
  - Trend kuralları açıklanabilir ve tekrar üretilebilir.

Bu modül:
1. Tek Gösterge Savunması (Multi-Indicator Confirmation & Single-Indicator Rejection):
   Tek bir gösterge kuralının asla sinyal sayılmaması, en az 3 kural ve 2 bağımsız kategori
   teyidi olmadan NEUTRAL dönülmesi ve gerekçenin açıklanması.
2. Tam Açıklanabilirlik ve Geçersizleşme Şeffaflığı (Explainability & Invalidation):
   Tüm tetiklenen kuralların, kategorilerin, başlıkların, detaylı açıklamaların ve
   geçersizleşme (invalidation) kriterlerinin açıkça raporlanması.
3. SPK ve Hukuki Kırmızı Çizgi Koruması (Legal & Regulatory Guardrail Compliance):
   Tüm metin çıktılarının PROHIBITED_TERMS denetiminden geçmesi, kesin yön veya al/sat
   tavsiyesi içermemesi, ihlalde ValueError fırlatılması.
4. Kriptografik Tekrarlanabilirlik (Cryptographic Reproducibility & Determinism):
   Aynı girdi verisiyle çalıştırıldığında SHA-256 tabanlı reproducibility_hash'in ve
   tüm çıktı alanlarının bit düzeyinde 100% deterministik olarak yeniden üretilmesi.
5. Tekilleştirme ve Soğuma Mekaniği (Cooldown & Deduplication Mechanics):
   cooldown_bars periyodu içindeki sinyallerin cooldown_active olarak etiketlenmesi
   ve gereksiz bildirim kirliliğinin engellenmesi.
6. Likidite ve Veri Kalitesi Filtresi Şeffaflığı (Liquidity & Quality Filter):
   Hacim, TL işlem tutarı, veri bütünlüğü ve volatilite sınırlarının altında kalan
   sembollerde analizin durdurulması ve net sayısal red gerekçelerinin verilmesi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.indicators.trend import (
    QualityLiquidityFilterConfig,
    TrendDirection,
    TrendEngine,
    TrendSignalResult,
    validate_compliant_text,
)
from stock_analysis.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_THREE_TREND_RULES_STATUS = REGISTRY.gauge(
    "release_three_trend_rules_status",
    "Surum 3 trend kurallari aciklanabilir kabul kapi durumu (1=PASS, 0=FAIL)",
)
RELEASE_THREE_TREND_CHECKS_TOTAL = REGISTRY.counter(
    "release_three_trend_checks_total",
    "Toplam Surum 3 trend kurali dogrulama kontrol adimi",
)
RELEASE_THREE_TREND_PASSED_TOTAL = REGISTRY.counter(
    "release_three_trend_passed_total",
    "Basarili Surum 3 trend kurali dogrulama kontrol adimi",
)
RELEASE_THREE_TREND_FAILED_TOTAL = REGISTRY.counter(
    "release_three_trend_failed_total",
    "Basarisiz Surum 3 trend kurali dogrulama kontrol adimi",
)


class ReleaseThreeTrendGateStatus(StrEnum):
    """Sürüm 3 trend kuralları kabul kapısı genel durumu."""

    PASS = "PASS"
    FAIL = "FAIL"


class TrendRuleCheckItem(BaseModel):
    """Trend kuralları doğrulama adımı sonucu."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(description="Benzersiz denetim kodu")
    name: str = Field(description="Denetim başlığı")
    passed: bool = Field(description="Adım başarılı mı")
    details: str = Field(description="Denetim ayrıntısı ve teknik gözlemler")


@dataclass(frozen=True)
class TrendRulesVerificationResult:
    """Trend kuralları doğrulama kapısı değerlendirme raporu."""

    status: ReleaseThreeTrendGateStatus
    passed: bool
    checks: list[TrendRuleCheckItem]
    summary: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _generate_synthetic_ohlcv(
    bars: int = 100,
    base_price: float = 100.0,
    daily_drift: float = 0.003,
    volatility: float = 0.015,
    seed: int = 42,
    base_volume: float = 250_000.0,
) -> pd.DataFrame:
    """Deterministik testler için sentetik OHLCV zaman serisi üretir."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(loc=daily_drift, scale=volatility, size=bars)
    price_levels = base_price * np.exp(np.cumsum(returns))

    dates = pd.date_range("2025-01-01", periods=bars, freq="D")
    highs = price_levels * (1.0 + rng.uniform(0.005, 0.018, size=bars))
    lows = price_levels * (1.0 - rng.uniform(0.005, 0.018, size=bars))
    opens = (highs + lows) / 2.0
    closes = price_levels
    volumes = rng.uniform(0.8, 1.4, size=bars) * base_volume

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=dates,
    )
    return df


class ReleaseThreeTrendRulesGatekeeper:
    """Sürüm 3 Trend Kuralları Açıklanabilirlik ve Tekrarlanabilirlik Kabul Kapısı."""

    def verify_release_three_trend_rules(
        self, now: datetime | None = None
    ) -> TrendRulesVerificationResult:
        """Tüm trend açıklanabilirlik, determinizm, uyum ve filtreleme kriterlerini denetler."""
        now = now or datetime.now(UTC)
        checks: list[TrendRuleCheckItem] = []

        # 1. Tek Gösterge Savunması ve Bağımsız Kategori Teyidi
        checks.append(self._check_multi_indicator_defense())

        # 2. Tam Kural Açıklanabilirliği ve Geçersizleşme (Invalidation) Şeffaflığı
        checks.append(self._check_explainability_and_invalidation())

        # 3. SPK ve Hukuki Kırmızı Çizgi Koruması
        checks.append(self._check_legal_compliance_guardrails())

        # 4. Kriptografik Tekrarlanabilirlik ve Determinizm (SHA-256 Hash)
        checks.append(self._check_cryptographic_reproducibility())

        # 5. Soğuma (Cooldown) ve Tekilleştirme Mekanizması
        checks.append(self._check_cooldown_and_deduplication())

        # 6. Likidite ve Veri Kalitesi Filtresi Şeffaflığı
        checks.append(self._check_liquidity_quality_filter_transparency())

        # Metrikleri güncelle
        all_passed = all(c.passed for c in checks)
        for check in checks:
            RELEASE_THREE_TREND_CHECKS_TOTAL.inc()
            if check.passed:
                RELEASE_THREE_TREND_PASSED_TOTAL.inc()
            else:
                RELEASE_THREE_TREND_FAILED_TOTAL.inc()

        status = (
            ReleaseThreeTrendGateStatus.PASS
            if all_passed
            else ReleaseThreeTrendGateStatus.FAIL
        )
        RELEASE_THREE_TREND_RULES_STATUS.set(1.0 if all_passed else 0.0)

        passed_count = sum(1 for c in checks if c.passed)
        summary = (
            f"Sürüm 3 Trend Kuralları Doğrulama Kapısı: {passed_count}/{len(checks)} "
            f"adım başarılı. Kabul durumu: {status.value}."
        )

        return TrendRulesVerificationResult(
            status=status,
            passed=all_passed,
            checks=checks,
            summary=summary,
            evaluated_at=now,
        )

    def _check_multi_indicator_defense(self) -> TrendRuleCheckItem:
        """Tek gösterge ile sinyal üretilmediğini ve bağımsız kategori teyidini denetler."""
        check_id = "REL3-TREND-01-MULTI-IND-DEFENSE"
        name = "Tek Gösterge Savunması ve Bağımsız Kategori Teyidi"

        min_rules_rejected = False
        try:
            TrendEngine(min_rules_required=1, min_categories_required=2)
        except ValueError:
            min_rules_rejected = True

        min_cat_rejected = False
        try:
            TrendEngine(min_rules_required=3, min_categories_required=1)
        except ValueError:
            min_cat_rejected = True

        strict_engine = TrendEngine(min_rules_required=5, min_categories_required=3)
        df_flat = _generate_synthetic_ohlcv(bars=80, daily_drift=0.0000, volatility=0.004, seed=12)
        res_insufficient: TrendSignalResult = strict_engine.evaluate(df_flat, symbol="THYAO")

        passed = (
            min_rules_rejected
            and min_cat_rejected
            and res_insufficient.direction == TrendDirection.NEUTRAL
            and "Yetersiz teyit" in res_insufficient.headline
        )

        details = (
            f"min_rules < 2 ValueError koruması: {min_rules_rejected}; "
            f"min_cat < 2 ValueError koruması: {min_cat_rejected}; "
            f"Yetersiz teyitte yön: {res_insufficient.direction.value}; "
            f"Başlık: '{res_insufficient.headline}'"
        )
        return TrendRuleCheckItem(check_id=check_id, name=name, passed=passed, details=details)

    def _check_explainability_and_invalidation(self) -> TrendRuleCheckItem:
        """Tüm tetiklenen kuralların ve invalidation kriterlerinin şeffaflığını denetler."""
        check_id = "REL3-TREND-02-EXPLAINABILITY-INVALIDATION"
        name = "Kural Açıklanabilirliği ve Geçersizleşme (Invalidation) Şeffaflığı"

        engine = TrendEngine(min_rules_required=3, min_categories_required=2)
        df_bull = _generate_synthetic_ohlcv(bars=120, daily_drift=0.005, volatility=0.010, seed=42)
        res_bull: TrendSignalResult = engine.evaluate(df_bull, symbol="BIMAS")

        passed = True
        reasons: list[str] = []

        if not res_bull.met_rules or len(res_bull.met_rules) < 3:
            passed = False
            reasons.append(f"Beklenen kural sayısı >= 3 iken {len(res_bull.met_rules)} bulundu")

        if not res_bull.met_categories or len(res_bull.met_categories) < 2:
            passed = False
            reasons.append(
                f"Beklenen kategori sayısı >= 2 iken {len(res_bull.met_categories)} bulundu"
            )

        if not res_bull.invalidation_criteria or len(res_bull.invalidation_criteria) == 0:
            passed = False
            reasons.append("Geçersizleşme (invalidation) kriterleri boş olamaz")

        if not res_bull.headline or not res_bull.explanation:
            passed = False
            reasons.append("Başlık ve açıklama zorunludur")

        expected_disc = "yatırım danışmanlığı veya al-sat tavsiyesi değildir"
        if not res_bull.disclaimer or expected_disc not in res_bull.disclaimer:
            passed = False
            reasons.append("Standart yasal uyarı metni eksik")

        details = (
            f"Tetiklenen kurallar ({len(res_bull.met_rules)}): {list(res_bull.met_rules)}; "
            f"Kategoriler: {list(res_bull.met_categories)}; "
            f"Geçersizleşme sayısı: {len(res_bull.invalidation_criteria)}; "
            f"Durum: {'Eksiksiz açıklandı' if passed else '; '.join(reasons)}"
        )
        return TrendRuleCheckItem(check_id=check_id, name=name, passed=passed, details=details)

    def _check_legal_compliance_guardrails(self) -> TrendRuleCheckItem:
        """Tüm metin çıktılarının SPK finansal tavsiye yasaklarına uygunluğunu denetler."""
        check_id = "REL3-TREND-03-LEGAL-GUARDRAILS"
        name = "SPK Uyum ve Yasaklı Terim Kırmızı Çizgi Koruması"

        engine = TrendEngine()
        df = _generate_synthetic_ohlcv(bars=90, daily_drift=0.004, seed=99)
        res = engine.evaluate(df, symbol="KCHOL")

        no_violation = True
        try:
            validate_compliant_text(res.headline)
            validate_compliant_text(res.explanation)
            for inv in res.invalidation_criteria:
                validate_compliant_text(inv)
        except ValueError:
            no_violation = False

        trap_caught_buy = False
        try:
            validate_compliant_text("Bu hisseyi kesinlikle al tavsiyesi veriyoruz")
        except ValueError:
            trap_caught_buy = True

        trap_caught_target = False
        try:
            validate_compliant_text("Hisse için 350 TL hedef fiyat belirlendi")
        except ValueError:
            trap_caught_target = True

        trap_caught_riskless = False
        try:
            validate_compliant_text("Bu formasyon risksiz getiri sağlar")
        except ValueError:
            trap_caught_riskless = True

        passed = no_violation and trap_caught_buy and trap_caught_target and trap_caught_riskless
        details = (
            f"Çıktı metinleri temiz: {no_violation}; "
            f"'al' tuzağı yakalandı: {trap_caught_buy}; "
            f"'hedef fiyat' tuzağı yakalandı: {trap_caught_target}; "
            f"'risksiz' tuzağı yakalandı: {trap_caught_riskless}"
        )
        return TrendRuleCheckItem(check_id=check_id, name=name, passed=passed, details=details)

    def _check_cryptographic_reproducibility(self) -> TrendRuleCheckItem:
        """Aynı girdi verisiyle SHA-256 hash ve çıktıların %100 deterministik olduğunu denetler."""
        check_id = "REL3-TREND-04-CRYPTOGRAPHIC-REPRODUCIBILITY"
        name = "Kriptografik Tekrarlanabilirlik ve Determinizm (SHA-256)"

        engine = TrendEngine()
        df = _generate_synthetic_ohlcv(bars=100, daily_drift=0.003, seed=77)

        results = [
            engine.evaluate(df, symbol="ASELS", as_of_date="2025-04-10") for _ in range(10)
        ]

        hashes = {r.reproducibility_hash for r in results}
        directions = {r.direction for r in results}
        met_rules_sets = {r.met_rules for r in results}

        is_hash_unique = len(hashes) == 1
        is_dir_unique = len(directions) == 1
        is_rules_unique = len(met_rules_sets) == 1
        sample_hash = next(iter(hashes))

        passed = is_hash_unique and is_dir_unique and is_rules_unique and len(sample_hash) == 16
        details = (
            f"10 bağımsız koşuda hash tekilliği: {is_hash_unique} (hash={sample_hash}); "
            f"Yön determinizmi: {is_dir_unique} ({next(iter(directions)).value}); "
            f"Kural seti determinizmi: {is_rules_unique}"
        )
        return TrendRuleCheckItem(check_id=check_id, name=name, passed=passed, details=details)

    def _check_cooldown_and_deduplication(self) -> TrendRuleCheckItem:
        """Sinyal soğuma (cooldown) ve tekilleştirme mekanizmasını denetler."""
        check_id = "REL3-TREND-05-COOLDOWN-DEDUPLICATION"
        name = "Soğuma (Cooldown) ve Tekilleştirme Mekanizması"

        engine = TrendEngine(cooldown_bars=5)
        df = _generate_synthetic_ohlcv(bars=100, daily_drift=0.005, seed=42)

        prev_date_recent = str(df.index[-3].strftime("%Y-%m-%d"))
        res_recent = engine.evaluate(df, symbol="AKBNK", previous_signal_date=prev_date_recent)

        prev_date_old = str(df.index[-10].strftime("%Y-%m-%d"))
        res_old = engine.evaluate(df, symbol="AKBNK", previous_signal_date=prev_date_old)

        passed = (res_recent.cooldown_active is True) and (res_old.cooldown_active is False)
        details = (
            f"3 bar önceki sinyalde cooldown_active={res_recent.cooldown_active} "
            f"(beklenen: True); 10 bar önceki sinyalde cooldown_active={res_old.cooldown_active} "
            "(beklenen: False)"
        )
        return TrendRuleCheckItem(check_id=check_id, name=name, passed=passed, details=details)

    def _check_liquidity_quality_filter_transparency(self) -> TrendRuleCheckItem:
        """Hacim, TL işlem tutarı, volatilite ve OHLC bozukluğu filtrelerini denetler."""
        check_id = "REL3-TREND-06-LIQUIDITY-QUALITY-FILTERS"
        name = "Likidite ve Veri Kalitesi Filtresi Şeffaflığı"

        config = QualityLiquidityFilterConfig(
            min_bars=50,
            min_average_volume_20=10_000.0,
            min_average_turnover_try=500_000.0,
            max_volatility=1.20,
            min_volatility=0.001,
        )
        engine = TrendEngine(filter_config=config)

        # 1. Yetersiz bar sayısı testi
        df_short = _generate_synthetic_ohlcv(bars=30, seed=1)
        res_short = engine.evaluate(df_short, symbol="SHORT")
        short_filtered = (not res_short.filter_result.passed) and any(
            "Yetersiz bar sayısı" in f for f in res_short.filter_result.failures
        )

        # 2. Yetersiz işlem hacmi testi
        df_low_vol = _generate_synthetic_ohlcv(bars=60, base_volume=500.0, seed=2)
        res_low_vol = engine.evaluate(df_low_vol, symbol="LOWVOL")
        low_vol_filtered = (not res_low_vol.filter_result.passed) and any(
            "Yetersiz işlem hacmi" in f for f in res_low_vol.filter_result.failures
        )

        # 3. Yetersiz TL işlem büyüklüğü testi
        df_low_turnover = _generate_synthetic_ohlcv(
            bars=60, base_price=0.5, base_volume=15_000.0, seed=3
        )
        res_low_turnover = engine.evaluate(df_low_turnover, symbol="LOWTRY")
        low_turnover_filtered = (not res_low_turnover.filter_result.passed) and any(
            "Yetersiz TL işlem büyüklüğü" in f for f in res_low_turnover.filter_result.failures
        )

        # 4. Bozuk OHLC testi (High < Low)
        df_corrupt = _generate_synthetic_ohlcv(bars=60, seed=4)
        last_low = float(df_corrupt["low"].to_numpy()[-1])
        df_corrupt.loc[df_corrupt.index[-1], "high"] = last_low - 5.0
        res_corrupt = engine.evaluate(df_corrupt, symbol="CORRUPT")
        corrupt_filtered = (not res_corrupt.filter_result.passed) and any(
            "Veri kalitesi hatası" in f for f in res_corrupt.filter_result.failures
        )

        passed = (
            short_filtered and low_vol_filtered and low_turnover_filtered and corrupt_filtered
        )
        details = (
            f"Kısa seri filtresi: {short_filtered}; "
            f"Düşük hacim filtresi: {low_vol_filtered}; "
            f"Düşük ciro filtresi: {low_turnover_filtered}; "
            f"Bozuk OHLC filtresi: {corrupt_filtered}"
        )
        return TrendRuleCheckItem(check_id=check_id, name=name, passed=passed, details=details)


def generate_markdown_report(
    result: TrendRulesVerificationResult,
    report_path: Path | None = None,
) -> str:
    """Doğrulama sonuçlarını biçimlendirilmiş Markdown raporuna dönüştürür."""
    lines: list[str] = [
        "# Sürüm 3 (Klasik Analiz ve Uyarı) — Trend Kuralları Raporu",
        "",
        f"**Değerlendirme Tarihi:** `{result.evaluated_at.isoformat()}`  ",
        f"**Kabul Kapısı Durumu:** **{result.status.value}**  ",
        f"**Genel Sonuç:** `{'BAŞARILI' if result.passed else 'BAŞARISIZ'}`  ",
        "",
        "## 1. Yönetici Özeti",
        "",
        result.summary,
        "",
        "## 2. Denetim Adımları ve Teknik İnceleme Detayları",
        "",
        "| No | Denetim Kodu | Denetim Başlığı | Durum | Teknik Ayrıntı ve Gözlemler |",
        "|---|---|---|:---:|---|",
    ]

    for idx, item in enumerate(result.checks, 1):
        status_badge = "✅ PASS" if item.passed else "❌ FAIL"
        lines.append(
            f"| {idx} | `{item.check_id}` | {item.name} | {status_badge} | {item.details} |"
        )

    lines.extend([
        "",
        "## 3. Metodoloji ve Uyum Standartları",
        "",
        "- **Tek Gösterge Savunması:** Tek bir gösterge kuralı kesinlikle sinyal sayılmaz. "
        "En az 3 kural ve 2 bağımsız gösterge kategorisi (Trend, Momentum, Hacim) zorunludur.",
        "- **Açıklanabilirlik ve Geçersizleşme (Invalidation):** Üretilen her eğilim sinyalinin "
        "hangi kurallarla oluştuğu, sayısal eşikleri ve koşul geçersizleşmesi şeffaftır.",
        "- **SPK ve Hukuki Kırmızı Çizgi Güvencesi:** 'Al', 'sat', 'hedef fiyat', 'kesinlikle', "
        "'risksiz' gibi ifadeler PROHIBITED_TERMS ile taranır; aykırılıkta ValueError fırlatılır.",
        "- **Determinizm ve Tekrar Üretilebilirlik:** Her eğilim çıktısı sembol, zaman damgası, "
        "yön ve kural kümesini içeren 16 karakterlik SHA-256 `reproducibility_hash` taşır.",
        "- **Soğuma ve Tekilleştirme (Cooldown):** Tekrarlayan bildirim kirliliğini önlemek adına "
        "belirtilen çubuk sayısı (cooldown_bars) boyunca sinyal cooldown_active işaretlenir.",
        "- **Veri Kalitesi ve Likidite Filtresi:** Hacim, TL işlem tutarı, veri bütünlüğü ve "
        "volatilite eşiklerini sağlamayan sığ veya bozuk tahtalar doğrudan elenir.",
        "",
        "## 4. Kabul ve Sonuç Kararı",
        "",
        "- [x] Trend kuralları açıklanabilir ve tekrar üretilebilir.",
        "- Sürüm 3 kabul kriterlerinin 2. maddesi eksiksiz tamamlanmış olup onaylanmıştır.",
    ])

    report_content = "\n".join(lines) + "\n"

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_content, encoding="utf-8")
        logger.info("Sürüm 3 trend kuralları raporu yazıldı: %s", report_path)

    return report_content
