"""Farklı piyasa rejimleri, kriz dönemleri ve sembol gruplarında stres testi motoru.

Bu modül, makine öğrenmesi modelleri ve sinyal stratejilerinin dayanıklılığını
aşırı piyasa koşulları altında denetlemek için şu özellikleri sağlar:
1. Piyasa Rejimleri (Market Regimes): Boğa (bull), Ayı (bear), Yatay (sideways) ve
   Yüksek Oynaklık (high volatility) rejimlerinin tespiti ve sınıflandırılması.
2. Kriz Senaryoları (Crisis Stress Testing): Ani Flaş Çöküş (Flash Crash), Likidite Kuruması
   (Liquidity Crunch), Uzatılmış Ayı Piyasası ve Oynaklık Patlaması.
3. Sembol Grupları (Symbol Baskets): Yüksek likiditeli BIST30 hisseleri, orta ölçekli
   ve düşük likiditeli yan tahtalar için özelleştirilmiş friksiyon sepetleri.
4. Sermaye Koruma ve Dayanıklılık Ölçümü: Stres altında maksimum düşüş (drawdown),
   sermaye koruma oranı (capital preservation ratio) ve risk denetimi.
5. Hukuki Uyum: SPK mevzuatına uygun standart yasal uyarılar; kesin getiri veya tavsiye içermez.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from stock_analysis.indicators.cost_model import MarketFrictionConfig
from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.models.strategy_backtest import (
    BacktestPerformanceReport,
    StrategyBacktestConfig,
    StrategySimulationResult,
    StrategySimulator,
    calculate_backtest_performance,
)


class MarketRegimeType(StrEnum):
    """Piyasa rejimi tipleri."""

    BULL = "bull"  # Yükselen trend, pozitif getiri, ılımlı volatilite
    BEAR = "bear"  # Düşen trend, negatif getiri, yüksek volatilite
    SIDEWAYS = "sideways"  # Yatay / sıkışık piyasa, sıfıra yakın getiri, düşük volatilite
    HIGH_VOLATILITY = "high_volatility"  # Türbülanslı / yön belirsiz, aşırı oynaklık


class StressScenarioType(StrEnum):
    """Kriz ve stres senaryosu tipleri."""

    FLASH_CRASH = "flash_crash"  # Ani ve sert çöküş (%15-25 düşüş, panik hacmi)
    LIQUIDITY_CRUNCH = "liquidity_crunch"  # Hacim çekilmesi, spread ve kayma katlanması
    PROLONGED_BEAR = "prolonged_bear"  # Uzun süreli düşüş trendi (ayı piyasası aşınması)
    VOLATILITY_EXPLOSION = "volatility_explosion"  # Oynaklık patlaması, gap açılışları
    CUSTOM = "custom"  # Kullanıcı tanımlı senaryo


class SymbolGroupType(StrEnum):
    """Sembol grubu ve likidite sepeti tipleri."""

    BIST30_LIQUID = "bist30_liquid"  # Yüksek hacim, dar spread, düşük kayma
    MID_CAP = "mid_cap"  # Standart BIST100 likidite profili
    SMALL_CAP_ILLIQUID = "small_cap_illiquid"  # Düşük hacim, geniş spread, yüksek kayma
    SECTOR_BASKET = "sector_basket"  # Sektörel hisse grubu


@dataclass(frozen=True)
class StressScenarioConfig:
    """Stres senaryosu yapılandırması."""

    scenario_type: StressScenarioType
    name: str
    description: str
    price_shock_pct: float = 0.0  # Fiyat şok oranı (örn. -0.20 = %20 anlık çöküş)
    shock_duration_bars: int = 1  # Şokun yayılacağı bar sayısı
    volume_multiplier: float = 1.0  # Hacim çarpanı (örn. 0.20 = %80 hacim kaybı)
    volatility_multiplier: float = 1.0  # Bar içi oynaklık çarpanı
    spread_multiplier: float = 1.0  # Alış-satış makası çarpanı (krizde spread açılması)
    slippage_multiplier: float = 1.0  # Kayma etkisi çarpanı
    start_bar_index: int | None = None  # Şokun başlayacağı bar (None ise serinin ortası)

    def __post_init__(self) -> None:
        """Parametre sınırlarını doğrular."""
        if self.volume_multiplier <= 0:
            raise ValueError(f"Hacim çarpanı pozitif olmalıdır: {self.volume_multiplier}")
        if self.spread_multiplier <= 0:
            raise ValueError(f"Spread çarpanı pozitif olmalıdır: {self.spread_multiplier}")
        if self.slippage_multiplier <= 0:
            raise ValueError(f"Kayma çarpanı pozitif olmalıdır: {self.slippage_multiplier}")
        if self.shock_duration_bars < 1:
            raise ValueError(f"Şok süresi en az 1 bar olmalıdır: {self.shock_duration_bars}")


@dataclass(frozen=True)
class SymbolGroupConfig:
    """Sembol grubu ve sürtünme parametreleri yapılandırması."""

    group_type: SymbolGroupType
    name: str
    symbols: tuple[str, ...]
    friction_config: MarketFrictionConfig
    description: str


@dataclass(frozen=True)
class StressScenarioResult:
    """Tek bir stres senaryosunun simülasyon çıktısı."""

    scenario_name: str
    scenario_type: StressScenarioType
    symbol: str
    performance: BacktestPerformanceReport
    simulation: StrategySimulationResult
    capital_preservation_ratio: float  # Son Sermaye / İlk Sermaye
    max_drawdown_pct: float
    survived_stress: bool  # Belirlenen tolerans sınırını aşmadı mı?
    description: str

    def to_dict(self) -> dict[str, object]:
        """Senaryo sonucunu sözlük formatında döndürür."""
        return {
            "scenario_name": self.scenario_name,
            "scenario_type": self.scenario_type.value,
            "symbol": self.symbol,
            "capital_preservation_ratio": round(self.capital_preservation_ratio, 4),
            "net_return_pct": round(self.simulation.net_return_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": round(self.performance.sharpe_ratio, 4),
            "survived_stress": self.survived_stress,
            "total_trades": self.simulation.total_trades,
            "description": self.description,
        }


@dataclass(frozen=True)
class StressTestReport:
    """Tüm stres senaryolarını özetleyen denetlenebilir rapor."""

    report_title: str
    strategy_name: str
    scenario_results: tuple[StressScenarioResult, ...]
    overall_worst_drawdown_pct: float
    overall_min_capital_ratio: float
    resilient_scenarios_count: int
    total_scenarios_count: int
    disclaimer: str

    def __post_init__(self) -> None:
        """Yasal metin uyumunu denetler."""
        validate_compliant_text(self.disclaimer)

    def to_dict(self) -> dict[str, object]:
        """Tüm raporu sözlük formatında döndürür."""
        return {
            "report_title": self.report_title,
            "strategy_name": self.strategy_name,
            "total_scenarios": self.total_scenarios_count,
            "resilient_scenarios": self.resilient_scenarios_count,
            "overall_worst_drawdown_pct": round(self.overall_worst_drawdown_pct, 4),
            "overall_min_capital_ratio": round(self.overall_min_capital_ratio, 4),
            "scenarios": [res.to_dict() for res in self.scenario_results],
            "disclaimer": self.disclaimer,
        }

    def format_summary(self) -> str:
        """Kullanıcı dostu metin özeti oluşturur."""
        lines = [
            f"# {self.report_title}",
            f"Strateji: {self.strategy_name}",
            "",
            "## Stres Özeti",
            f"- Test Edilen Senaryo Sayısı: {self.total_scenarios_count}",
            (
                "- Başarılı / Dayanıklı Senaryolar: "
                f"{self.resilient_scenarios_count}/{self.total_scenarios_count}"
            ),
            f"- En Kötü Maksimum Düşüş (Worst MDD): %{self.overall_worst_drawdown_pct:.2f}",
            f"- Asgari Sermaye Koruma Oranı: %{self.overall_min_capital_ratio * 100.0:.2f}",
            "",
            "## Senaryo Detayları",
        ]
        for res in self.scenario_results:
            status = "DAYANIKLI (PASS)" if res.survived_stress else "RİSKLİ (FAIL)"
            ret_str = f"Net Getiri %{res.simulation.net_return_pct:.2f}"
            mdd_str = f"Max DD %{res.max_drawdown_pct:.2f}"
            cap_str = f"Koruma %{res.capital_preservation_ratio * 100.0:.1f}"
            lines.append(
                f"- [{status}] {res.scenario_name} ({res.symbol}): {ret_str}, {mdd_str}, {cap_str}"
            )
        lines.append("")
        lines.append("## Yasal Uyarı")
        lines.append(f"> {self.disclaimer}")
        return "\n".join(lines)


DEFAULT_STRESS_DISCLAIMER = (
    "Burada sunulan stres testi, piyasa rejimi ve kriz senaryosu analizleri "
    "geçmiş veya sentetik simülasyonlara dayanmakta olup yatırım danışmanlığı, "
    "sermaye taahhüdü veya getiri vaadi içermez. Piyasa koşulları gelecekte "
    "farklılık gösterebilir."
)


def classify_market_regime(
    returns: Sequence[float] | np.ndarray,
    threshold_vol: float = 0.02,
    threshold_trend: float = 0.0005,
) -> MarketRegimeType:
    """Getiri serisinden piyasa rejimini sınıflandırır.

    Args:
        returns: Bar bazlı yüzdesel getiriler dizisi.
        threshold_vol: Yüksek oynaklık eşiği (standart sapma).
        threshold_trend: Trend yönü eşiği (ortalama getiri).

    Returns:
        MarketRegimeType: Tespit edilen piyasa rejimi.
    """
    arr = np.asarray(returns, dtype=float)
    if len(arr) < 2:
        return MarketRegimeType.SIDEWAYS

    mean_ret = float(np.mean(arr))
    vol = float(np.std(arr))

    if vol >= threshold_vol * 1.5:
        return MarketRegimeType.HIGH_VOLATILITY
    if mean_ret >= threshold_trend:
        return MarketRegimeType.BULL
    if mean_ret <= -threshold_trend:
        return MarketRegimeType.BEAR
    return MarketRegimeType.SIDEWAYS


def detect_regimes_in_series(
    prices: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Zaman serisi üzerinde yuvarlanan pencere ile piyasa rejimlerini tespit eder.

    Lookahead bias olmaması için yalnızca geçmiş 'window' barlık veriler kullanılır.
    """
    if len(prices) < window:
        raise ValueError(
            f"Fiyat serisi uzunluğu ({len(prices)}) rejim penceresinden ({window}) küçük olamaz."
        )

    returns = prices.pct_change().fillna(0.0)
    regimes = []

    for i in range(len(prices)):
        if i < window:
            regimes.append(MarketRegimeType.SIDEWAYS.value)
        else:
            w_rets = returns.iloc[i - window + 1 : i + 1].to_numpy()
            regime = classify_market_regime(w_rets)
            regimes.append(regime.value)

    return pd.Series(regimes, index=prices.index, name="market_regime")


def get_default_stress_scenarios() -> tuple[StressScenarioConfig, ...]:
    """Sektör standardı varsayılan kriz ve stres senaryolarını döndürür."""
    return (
        StressScenarioConfig(
            scenario_type=StressScenarioType.FLASH_CRASH,
            name="Ani Flaş Çöküş (Flash Crash)",
            description="Piyasada 2 bar içinde %18 ani düşüş ve 3 kat hacim patlaması yaşanır.",
            price_shock_pct=-0.18,
            shock_duration_bars=2,
            volume_multiplier=3.0,
            volatility_multiplier=2.5,
            spread_multiplier=2.5,
            slippage_multiplier=3.0,
        ),
        StressScenarioConfig(
            scenario_type=StressScenarioType.LIQUIDITY_CRUNCH,
            name="Likidite Kuruması (Liquidity Crunch)",
            description="Hacim %80 düşer, spread 4 kat açılır, kayma 4 kat artar.",
            price_shock_pct=-0.05,
            shock_duration_bars=5,
            volume_multiplier=0.20,
            volatility_multiplier=1.8,
            spread_multiplier=4.0,
            slippage_multiplier=4.0,
        ),
        StressScenarioConfig(
            scenario_type=StressScenarioType.PROLONGED_BEAR,
            name="Uzatılmış Ayı Piyasası (Prolonged Bear)",
            description="15 bar boyunca kümülatif %30'a varan sürekli satış baskısı.",
            price_shock_pct=-0.30,
            shock_duration_bars=15,
            volume_multiplier=1.2,
            volatility_multiplier=1.5,
            spread_multiplier=1.5,
            slippage_multiplier=1.8,
        ),
        StressScenarioConfig(
            scenario_type=StressScenarioType.VOLATILITY_EXPLOSION,
            name="Oynaklık Patlaması (Volatility Explosion)",
            description="3 kat artan günlük volatilite ve genişleyen makas aralığı.",
            price_shock_pct=-0.08,
            shock_duration_bars=8,
            volume_multiplier=2.0,
            volatility_multiplier=3.0,
            spread_multiplier=2.0,
            slippage_multiplier=2.5,
        ),
    )


def get_default_symbol_groups() -> tuple[SymbolGroupConfig, ...]:
    """Farklı likidite ve piyasa özelliklerine sahip sembol gruplarını döndürür."""
    # BIST30 Likit: Düşük spread ve düşük kayma
    bist30_friction = MarketFrictionConfig(
        commission_rate=0.0008,
        bsmv_rate=0.05,
        exchange_fee_rate=0.00004,
        half_spread_pct=0.0003,  # %0.03
        slippage_impact_factor=0.00005,
        require_frictions=True,
    )

    # Orta Ölçek (Mid-Cap): Standart friksiyon
    midcap_friction = MarketFrictionConfig(
        commission_rate=0.0010,
        bsmv_rate=0.05,
        exchange_fee_rate=0.00004,
        half_spread_pct=0.0008,  # %0.08
        slippage_impact_factor=0.00015,
        require_frictions=True,
    )

    # Düşük Likidite (Small-Cap Illiquid): Geniş spread ve yüksek kayma
    smallcap_friction = MarketFrictionConfig(
        commission_rate=0.0015,
        bsmv_rate=0.05,
        exchange_fee_rate=0.00004,
        half_spread_pct=0.0025,  # %0.25 geniş makas
        slippage_impact_factor=0.00080,  # 8 kat daha yüksek kayma
        require_frictions=True,
    )

    return (
        SymbolGroupConfig(
            group_type=SymbolGroupType.BIST30_LIQUID,
            name="BIST30 Likit Hisseler",
            symbols=("THYAO", "ASELS", "KCHOL", "GARAN", "BIMAS"),
            friction_config=bist30_friction,
            description="Yüksek işlem hacmine ve dar alış-satış makasına sahip derin tahtalar.",
        ),
        SymbolGroupConfig(
            group_type=SymbolGroupType.MID_CAP,
            name="BIST Orta Ölçek (Mid-Cap)",
            symbols=("SOKM", "BERA", "VESBE", "CIMSA"),
            friction_config=midcap_friction,
            description="Dengeli hacim ve ortalama piyasa etkisine sahip hisseler.",
        ),
        SymbolGroupConfig(
            group_type=SymbolGroupType.SMALL_CAP_ILLIQUID,
            name="Düşük Likiditeli Yan Tahtalar (Small-Cap)",
            symbols=("ALCAR", "KENT", "ISBIR"),
            friction_config=smallcap_friction,
            description="Sığ tahtalar; işlem büyüklüğü piyasayı ciddi ölçüde saptırır.",
        ),
    )


def apply_stress_to_dataframe(
    bars: pd.DataFrame,
    scenario: StressScenarioConfig,
) -> tuple[pd.DataFrame, MarketFrictionConfig]:
    """Veri çerçevesine stres senaryosunu uygular ve uyarlanmış friksiyon yapılandırmasını üretir.

    Stres şoku, serinin belirtilen bar indeksinde veya ortasında başlar ve
    'shock_duration_bars' boyunca kademeli yansıtılır.
    """
    df = bars.copy()
    n_bars = len(df)
    if n_bars < 5:
        raise ValueError(f"Stres testi için en az 5 bar gereklidir (mevcut: {n_bars}).")

    start_idx = (
        scenario.start_bar_index if scenario.start_bar_index is not None else max(1, n_bars // 3)
    )
    start_idx = max(0, min(start_idx, n_bars - scenario.shock_duration_bars))
    end_idx = min(n_bars, start_idx + scenario.shock_duration_bars)

    close_col = "close"
    vol_col = "volume" if "volume" in df.columns else None

    # Kademeli fiyat şoku
    # Toplam şoku bar sayısına eşit logaritmik veya geometrik olarak bölelim
    duration = end_idx - start_idx
    step_multiplier = (1.0 + scenario.price_shock_pct) ** (1.0 / max(1, duration))

    prices = df[close_col].to_numpy(dtype=float, copy=True)
    curr_factor = 1.0

    for i in range(start_idx, n_bars):
        if i < end_idx:
            curr_factor *= step_multiplier
        # Şok sonrasında fiyat kalıcı veya düşük seviyede devam eder
        prices[i] = max(0.01, prices[i] * curr_factor)

    df[close_col] = prices

    # High / Low sütunları varsa onları da orantıla
    if "high" in df.columns and "low" in df.columns:
        df["high"] = np.maximum(df["high"] * curr_factor, df[close_col])
        df["low"] = np.minimum(df["low"] * curr_factor, df[close_col])

    # Hacim şoku
    if vol_col is not None:
        volumes = df[vol_col].to_numpy(dtype=float, copy=True)
        for i in range(start_idx, end_idx):
            volumes[i] = max(100.0, volumes[i] * scenario.volume_multiplier)
        df[vol_col] = volumes

    # Kriz koşullarında genişleyen friksiyonlar
    fric = MarketFrictionConfig(
        commission_rate=0.0010,
        bsmv_rate=0.05,
        exchange_fee_rate=0.00004,
        half_spread_pct=0.0005 * scenario.spread_multiplier,
        slippage_impact_factor=0.00010 * scenario.slippage_multiplier,
        require_frictions=True,
    )

    return df, fric


def run_stress_test(
    bars: pd.DataFrame,
    signals: Sequence[float] | pd.Series,
    symbol: str = "THYAO",
    strategy_name: str = "ML-Strategy",
    scenarios: Sequence[StressScenarioConfig] | None = None,
    base_config: StrategyBacktestConfig | None = None,
    max_acceptable_drawdown_pct: float = 40.0,
    min_acceptable_capital_ratio: float = 0.60,
    disclaimer: str = DEFAULT_STRESS_DISCLAIMER,
) -> StressTestReport:
    """Belirli bir hisse ve sinyal için kriz senaryoları stres testini çalıştırır.

    Args:
        bars: OHLCV barlarını içeren DataFrame (en az 'close' gereklidir).
        signals: Hedef pozisyon ağırlıkları (0.0 ile 1.0 arası).
        symbol: Hisse senedi sembolü.
        strategy_name: Model veya strateji etiketi.
        scenarios: Uygulanacak kriz senaryoları listesi (None ise varsayılan senaryolar).
        base_config: Temel backtest yapılandırması.
        max_acceptable_drawdown_pct: Dayanıklılık kabulü için azami izin verilen Drawdown (%).
        min_acceptable_capital_ratio: Dayanıklılık kabulü için asgari sermaye koruma oranı.
        disclaimer: SPK uyumlu yasal uyarı metni.

    Returns:
        StressTestReport: Tüm senaryoların sonuçlarını içeren detaylı rapor.
    """
    if scenarios is None:
        scenarios = get_default_stress_scenarios()

    results: list[StressScenarioResult] = []
    worst_mdd = 0.0
    min_cap_ratio = 1.0

    for sc in scenarios:
        stressed_bars, stressed_friction = apply_stress_to_dataframe(bars, sc)

        # Temel yapılandırmayı stresli friksiyonlarla güncelle
        if base_config is not None:
            cfg = StrategyBacktestConfig(
                initial_capital=base_config.initial_capital,
                execution_lag_bars=base_config.execution_lag_bars,
                max_position_weight=base_config.max_position_weight,
                allow_short=base_config.allow_short,
                cash_buffer_pct=base_config.cash_buffer_pct,
                min_trade_size_try=base_config.min_trade_size_try,
                allow_zero_lag=base_config.allow_zero_lag,
                friction_config=stressed_friction,
            )
        else:
            cfg = StrategyBacktestConfig(
                friction_config=stressed_friction,
            )

        sim = StrategySimulator(config=cfg)
        sim_result = sim.simulate(stressed_bars, signals, symbol=symbol)
        perf_report = calculate_backtest_performance(sim_result)

        cap_ratio = sim_result.final_capital / sim_result.initial_capital
        mdd = perf_report.max_drawdown_pct

        survived = (
            mdd <= max_acceptable_drawdown_pct
            and cap_ratio >= min_acceptable_capital_ratio
            and not np.isnan(cap_ratio)
        )

        results.append(
            StressScenarioResult(
                scenario_name=sc.name,
                scenario_type=sc.scenario_type,
                symbol=symbol,
                performance=perf_report,
                simulation=sim_result,
                capital_preservation_ratio=cap_ratio,
                max_drawdown_pct=mdd,
                survived_stress=survived,
                description=sc.description,
            )
        )

        worst_mdd = max(worst_mdd, mdd)
        min_cap_ratio = min(min_cap_ratio, cap_ratio)

    resilient_count = sum(1 for r in results if r.survived_stress)

    return StressTestReport(
        report_title=f"Kriz ve Stres Testi Raporu: {symbol}",
        strategy_name=strategy_name,
        scenario_results=tuple(results),
        overall_worst_drawdown_pct=worst_mdd,
        overall_min_capital_ratio=min_cap_ratio,
        resilient_scenarios_count=resilient_count,
        total_scenarios_count=len(results),
        disclaimer=disclaimer,
    )


def run_symbol_basket_stress_test(
    basket_data: Mapping[str, pd.DataFrame],
    basket_signals: Mapping[str, Sequence[float] | pd.Series],
    symbol_group: SymbolGroupConfig,
    strategy_name: str = "Basket-Strategy",
    disclaimer: str = DEFAULT_STRESS_DISCLAIMER,
) -> StressTestReport:
    """Belirli bir sembol sepetindeki hisseler için stres testi simülasyonu yapar.

    Grup bazlı friksiyonlar uygulanarak stratejinin likidite farklılıklarına
    duyarlılığı ölçülür.
    """
    results: list[StressScenarioResult] = []
    worst_mdd = 0.0
    min_cap_ratio = 1.0

    # Sepetteki her sembol için simülasyonu grubun friksiyon profiliyle çalıştır
    for sym in symbol_group.symbols:
        if sym not in basket_data or sym not in basket_signals:
            continue

        bars = basket_data[sym]
        sigs = basket_signals[sym]

        cfg = StrategyBacktestConfig(
            friction_config=symbol_group.friction_config,
        )
        sim = StrategySimulator(config=cfg)
        sim_result = sim.simulate(bars, sigs, symbol=sym)
        perf_report = calculate_backtest_performance(sim_result)

        cap_ratio = sim_result.final_capital / sim_result.initial_capital
        mdd = perf_report.max_drawdown_pct

        survived = mdd <= 45.0 and cap_ratio >= 0.55

        results.append(
            StressScenarioResult(
                scenario_name=f"{symbol_group.name} - {sym}",
                scenario_type=StressScenarioType.CUSTOM,
                symbol=sym,
                performance=perf_report,
                simulation=sim_result,
                capital_preservation_ratio=cap_ratio,
                max_drawdown_pct=mdd,
                survived_stress=survived,
                description=symbol_group.description,
            )
        )

        worst_mdd = max(worst_mdd, mdd)
        min_cap_ratio = min(min_cap_ratio, cap_ratio)

    resilient_count = sum(1 for r in results if r.survived_stress)

    return StressTestReport(
        report_title=f"Sembol Grubu Dayanıklılık Raporu: {symbol_group.name}",
        strategy_name=strategy_name,
        scenario_results=tuple(results),
        overall_worst_drawdown_pct=worst_mdd,
        overall_min_capital_ratio=min_cap_ratio,
        resilient_scenarios_count=resilient_count,
        total_scenarios_count=len(results),
        disclaimer=disclaimer,
    )
