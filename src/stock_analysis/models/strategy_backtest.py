"""Model tahminlerine dayalı strateji simülasyonu ve gerçekçi piyasa friksiyonları.

Bu modül, makine öğrenmesi model tahminlerinin veya sinyallerinin geriye dönük
strateji simülasyonunu (backtesting) gerçekleştirirken aşağıdaki kritik kısıtları uygular:
1. İşlem Maliyeti (Transaction Costs): Aracı kurum komisyonu, BSMV ve Borsa tescil payı.
2. Alış-Satış Makası (Spread): Alış ve satış fiyatlarına uygulanan yarım makas payı.
3. Kayma (Slippage): Likiditeye ve işlem büyüklüğüne bağlı piyasa etkisi.
4. İcra Gecikmesi (Execution Delay / Lag): Sinyal t anında oluşur, icra en erken t+1 anında yapılır.
   Sıfır gecikmeli simülasyonlar gelecek sızıntısı (lookahead bias) sayılır ve engellenir.
5. Pozisyon Sınırları (Position Limits): Azami pozisyon büyüklüğü (max_position_weight),
   açığa satış engeli (BIST pay piyasası kuralı - short selling restriction) ve kaldıraç kısıtı.
6. Hukuki Uyum: Kesin getiri/kâr vaadi ve al/sat tavsiyesi içermez; SPK kurallarıyla uyumludur.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stock_analysis.indicators.cost_model import (
    ExecutionSimulator,
    MarketFrictionConfig,
    TradeAction,
)
from stock_analysis.indicators.trend import validate_compliant_text


class StrategyExecutionError(Exception):
    """Strateji simülasyonunda kural veya bütünlük ihlali hatası."""


class ZeroLagLeakageError(StrategyExecutionError):
    """Sıfır icra gecikmesi nedeniyle ortaya çıkan geleceğe bakma sızıntısı hatası."""


class PositionLimitViolationError(StrategyExecutionError):
    """Pozisyon büyüklüğü veya açığa satış kısıtı ihlali hatası."""


@dataclass(frozen=True)
class StrategyBacktestConfig:
    """Gerçekçi strateji simülasyonu yapılandırması."""

    initial_capital: float = 100_000.0  # Başlangıç sermayesi (TL)
    execution_lag_bars: int = 1  # Sinyal ile icra arasındaki gecikme barı (asgari 1)
    max_position_weight: float = 1.0  # Azami pozisyon ağırlığı (0.0 < w <= 1.0)
    allow_short: bool = False  # BIST pay piyasasında varsayılan olarak açığa satış kapalı
    cash_buffer_pct: float = 0.005  # Komisyon ve kayma için asgari nakit rezervi (%0.5)
    min_trade_size_try: float = 100.0  # Asgari emir tutarı (TL)
    allow_zero_lag: bool = False  # Yalnızca açıkça izin verilirse sıfır gecikmeye izin verilir
    friction_config: MarketFrictionConfig = field(default_factory=MarketFrictionConfig)

    def __post_init__(self) -> None:
        """Yapılandırma parametrelerini doğrular."""
        if self.initial_capital <= 0:
            raise ValueError(f"Başlangıç sermayesi pozitif olmalıdır: {self.initial_capital}")
        if self.execution_lag_bars < 1 and not self.allow_zero_lag:
            raise ZeroLagLeakageError(
                f"execution_lag_bars asgari 1 olmalıdır (mevcut: {self.execution_lag_bars}). "
                "Sinyal anında gerçekleşen sıfır gecikmeli işlemler gelecek sızıntısına yol açar."
            )
        if not (0.0 < self.max_position_weight <= 1.0):
            raise PositionLimitViolationError(
                f"max_position_weight 0 ile 1.0 arasında olmalıdır: {self.max_position_weight}"
            )
        if not (0.0 <= self.cash_buffer_pct < 0.20):
            raise ValueError(
                f"cash_buffer_pct makul bir aralıkta olmalıdır: {self.cash_buffer_pct}"
            )


@dataclass(frozen=True)
class TradeRecord:
    """Simüle edilen tekil işlem kaydı."""

    bar_index: int
    timestamp: pd.Timestamp | str | int
    action: TradeAction
    nominal_price: float
    effective_price: float
    shares: float
    trade_value_try: float
    commission_try: float
    bsmv_try: float
    exchange_fee_try: float
    spread_cost_try: float
    slippage_cost_try: float
    total_friction_try: float
    cash_after: float
    shares_after: float
    portfolio_value_after: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Her bar için portföy durumu."""

    bar_index: int
    timestamp: pd.Timestamp | str | int
    price: float
    cash: float
    shares: float
    position_value: float
    portfolio_value: float
    position_weight: float
    gross_value: float  # Maliyetsiz kıyas serisi değeri


DEFAULT_BACKTEST_REPORT_DISCLAIMER = (
    "Bu performans raporu, geçmiş dönem simülasyon ve varsayımsal model "
    "kestirimlerine dayanmaktadır. Geçmiş sonuçlar gelecekteki performans için "
    "bir vaat veya gösterge teşkil etmez; gelecekteki sonuçların aynen gerçekleşeceğini "
    "ifade etmez. SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında olmayıp "
    "yalnızca bilgi ve teknik analiz değerlendirmesi amacıyla sunulmuştur."
)


@dataclass(frozen=True)
class BacktestAssumptions:
    """Simülasyonda kullanılan örneklem ve model varsayımları."""

    sample_start: pd.Timestamp | str | int | None
    sample_end: pd.Timestamp | str | int | None
    total_bars: int
    annualization_factor: float
    risk_free_rate_annual: float
    commission_rate: float
    execution_lag_bars: int
    slippage_impact_factor: float
    half_spread_pct: float
    allow_short: bool
    max_position_weight: float
    description: str


@dataclass(frozen=True)
class BacktestPerformanceReport:
    """Sharpe, maksimum düşüş ve getiri metriklerini içeren kapsamlı performans raporu."""

    symbol: str
    total_return_pct: float
    annualized_return_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    peak_bar_index: int
    trough_bar_index: int
    daily_win_rate_pct: float
    assumptions: BacktestAssumptions
    disclaimer: str

    def __post_init__(self) -> None:
        """Yasal metin uyumunu denetler."""
        validate_compliant_text(self.disclaimer)

    def to_dict(self) -> dict[str, object]:
        """Rapor verilerini sözlük formatında döndürür."""
        return {
            "symbol": self.symbol,
            "total_return_pct": round(self.total_return_pct, 4),
            "annualized_return_pct": round(self.annualized_return_pct, 4),
            "annualized_volatility_pct": round(self.annualized_volatility_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_drawdown_duration_bars": self.max_drawdown_duration_bars,
            "peak_bar_index": self.peak_bar_index,
            "trough_bar_index": self.trough_bar_index,
            "daily_win_rate_pct": round(self.daily_win_rate_pct, 4),
            "assumptions": {
                "sample_start": str(self.assumptions.sample_start),
                "sample_end": str(self.assumptions.sample_end),
                "total_bars": self.assumptions.total_bars,
                "annualization_factor": self.assumptions.annualization_factor,
                "risk_free_rate_annual": self.assumptions.risk_free_rate_annual,
                "commission_rate": self.assumptions.commission_rate,
                "execution_lag_bars": self.assumptions.execution_lag_bars,
                "slippage_impact_factor": self.assumptions.slippage_impact_factor,
                "half_spread_pct": self.assumptions.half_spread_pct,
                "allow_short": self.assumptions.allow_short,
                "max_position_weight": self.assumptions.max_position_weight,
                "description": self.assumptions.description,
            },
            "disclaimer": self.disclaimer,
        }

    def format_summary(self) -> str:
        """Kullanıcı dostu ve denetlenebilir metin özeti oluşturur."""
        lines = [
            f"# Strateji Performans Raporu: {self.symbol}",
            "",
            "## Temel Metrikler",
            f"- Toplam Net Getiri: %{self.total_return_pct:.2f}",
            f"- Yıllıklandırılmış Net Getiri (CAGR): %{self.annualized_return_pct:.2f}",
            f"- Yıllıklandırılmış Oynaklık (Volatilite): %{self.annualized_volatility_pct:.2f}",
            f"- Sharpe Oranı: {self.sharpe_ratio:.2f}",
            f"- Sortino Oranı: {self.sortino_ratio:.2f}",
            f"- Maksimum Düşüş (Max Drawdown): %{self.max_drawdown_pct:.2f} "
            f"(Tepe Bar: {self.peak_bar_index}, Dip Bar: {self.trough_bar_index})",
            f"- En Uzun Düşüş Süresi: {self.max_drawdown_duration_bars} bar",
            f"- Pozitif Gün Oranı (Win Rate): %{self.daily_win_rate_pct:.2f}",
            "",
            "## Örneklem ve Model Varsayımları",
            f"- Örneklem Aralığı: {self.assumptions.sample_start} - {self.assumptions.sample_end} "
            f"({self.assumptions.total_bars} bar)",
            f"- Yıllıklandırma Çarpanı: {self.assumptions.annualization_factor:.0f} işlem günü",
            f"- Yıllık Gösterge Faiz Oranı: %{self.assumptions.risk_free_rate_annual * 100.0:.2f}",
            f"- İcra Gecikmesi: {self.assumptions.execution_lag_bars} bar",
            f"- Komisyon Oranı: %{self.assumptions.commission_rate * 100.0:.4f}",
            f"- Alış/Satış Yarım Makası: %{self.assumptions.half_spread_pct * 100.0:.4f}",
            f"- Azami Pozisyon Ağırlığı: %{self.assumptions.max_position_weight * 100.0:.1f}",
            f"- Açığa Satış İzni: {'Var' if self.assumptions.allow_short else 'Yok'}",
            f"- Açıklama: {self.assumptions.description}",
            "",
            "## Yasal Uyarı",
            f"> {self.disclaimer}",
        ]
        return "\n".join(lines)


def calculate_backtest_performance(
    result: StrategySimulationResult,
    risk_free_rate_annual: float = 0.0,
    annualization_factor: float = 252.0,
) -> BacktestPerformanceReport:
    """Simülasyon sonuçları üzerinden Sharpe, Maksimum Düşüş ve getiri metriklerini hesaplar."""
    if annualization_factor <= 0:
        raise ValueError(f"annualization_factor pozitif olmalıdır: {annualization_factor}")

    snapshots = result.snapshots
    n_bars = len(snapshots)
    if n_bars < 2:
        raise ValueError(f"Performans hesaplaması için en az 2 bar gereklidir (mevcut: {n_bars}).")

    portfolio_values = np.array([s.portfolio_value for s in snapshots], dtype=float)
    v_start = float(portfolio_values[0])
    v_end = float(portfolio_values[-1])

    # Günlük / bar getirileri
    denom = np.where(portfolio_values[:-1] == 0.0, 1.0, portfolio_values[:-1])
    bar_returns = (portfolio_values[1:] - portfolio_values[:-1]) / denom

    # Toplam Getiri (%)
    total_return_pct = ((v_end - v_start) / v_start) * 100.0 if v_start > 0 else 0.0

    # Yıllıklandırılmış Getiri (CAGR %)
    years = (n_bars - 1) / annualization_factor
    total_return_dec = (v_end - v_start) / v_start if v_start > 0 else 0.0
    if years > 0 and (1.0 + total_return_dec) > 0:
        cagr = ((1.0 + total_return_dec) ** (1.0 / years)) - 1.0
        annualized_return_pct = float(cagr * 100.0)
    elif (1.0 + total_return_dec) <= 0:
        annualized_return_pct = -100.0
    else:
        annualized_return_pct = total_return_pct

    # Volatilite
    daily_std = (
        float(np.std(bar_returns, ddof=1)) if len(bar_returns) > 1 else float(np.std(bar_returns))
    )
    annualized_volatility_pct = float(daily_std * np.sqrt(annualization_factor) * 100.0)

    # Bar bazında risksiz getiri
    if risk_free_rate_annual > 0:
        rf_bar = float((1.0 + risk_free_rate_annual) ** (1.0 / annualization_factor) - 1.0)
    else:
        rf_bar = 0.0

    excess_returns = bar_returns - rf_bar
    mean_excess = float(np.mean(excess_returns))

    # Sharpe Oranı
    if daily_std > 1e-12:
        sharpe_ratio = float((mean_excess / daily_std) * np.sqrt(annualization_factor))
    else:
        sharpe_ratio = 0.0

    # Sortino Oranı (Aşağı yönlü sapma)
    downside_returns = np.minimum(0.0, excess_returns)
    downside_std = float(np.sqrt(np.mean(downside_returns**2)))
    if downside_std > 1e-12:
        sortino_ratio = float((mean_excess / downside_std) * np.sqrt(annualization_factor))
    else:
        sortino_ratio = 0.0

    # Maksimum Düşüş (Max Drawdown - MDD)
    cummax = np.maximum.accumulate(portfolio_values)
    cummax_denom = np.where(cummax == 0.0, 1.0, cummax)
    drawdowns = (cummax - portfolio_values) / cummax_denom
    max_drawdown = float(np.max(drawdowns))
    max_drawdown_pct = float(max_drawdown * 100.0)

    if max_drawdown > 0:
        trough_idx = int(np.argmax(drawdowns))
        peak_idx = int(np.argmax(portfolio_values[: trough_idx + 1]))
    else:
        trough_idx = 0
        peak_idx = 0

    # En uzun düşüş süresi (bar)
    max_duration = 0
    current_duration = 0
    for is_underwater in portfolio_values < cummax:
        if is_underwater:
            current_duration += 1
            if current_duration > max_duration:
                max_duration = current_duration
        else:
            current_duration = 0

    # Günlük pozitif getiri oranı (Win Rate)
    daily_win_rate_pct = (
        float((np.sum(bar_returns > 0) / len(bar_returns)) * 100.0) if len(bar_returns) > 0 else 0.0
    )

    # Örneklem ve Varsayım Bilgisi
    start_ts = snapshots[0].timestamp
    end_ts = snapshots[-1].timestamp
    assumptions = BacktestAssumptions(
        sample_start=start_ts,
        sample_end=end_ts,
        total_bars=n_bars,
        annualization_factor=annualization_factor,
        risk_free_rate_annual=risk_free_rate_annual,
        commission_rate=result.config.friction_config.commission_rate,
        execution_lag_bars=result.config.execution_lag_bars,
        slippage_impact_factor=result.config.friction_config.slippage_impact_factor,
        half_spread_pct=result.config.friction_config.half_spread_pct,
        allow_short=result.config.allow_short,
        max_position_weight=result.config.max_position_weight,
        description=(
            f"Örneklem dönemi: {start_ts} - {end_ts} ({n_bars} bar). "
            f"Yıllıklandırma: {annualization_factor:.0f} bar/yıl, "
            f"Yıllık gösterge faiz oranı: %{risk_free_rate_annual * 100.0:.2f}. "
            f"İşlem maliyeti, alış-satış makası, likidite kayması ve "
            f"{result.config.execution_lag_bars} bar icra gecikmesi uygulanmıştır."
        ),
    )

    return BacktestPerformanceReport(
        symbol=result.symbol,
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        annualized_volatility_pct=annualized_volatility_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_duration_bars=max_duration,
        peak_bar_index=peak_idx,
        trough_bar_index=trough_idx,
        daily_win_rate_pct=daily_win_rate_pct,
        assumptions=assumptions,
        disclaimer=DEFAULT_BACKTEST_REPORT_DISCLAIMER,
    )


@dataclass(frozen=True)
class StrategySimulationResult:
    """Strateji geriye dönük simülasyon sonucu."""

    symbol: str
    config: StrategyBacktestConfig
    initial_capital: float
    final_capital: float
    gross_final_capital: float
    gross_return_pct: float
    net_return_pct: float
    friction_drag_pct: float  # Brüt getiri ile net getiri arasındaki sürtünme kaybı
    total_trades: int
    total_friction_try: float
    max_position_weight_observed: float
    execution_lag_applied: int
    is_realistic: bool
    trades: tuple[TradeRecord, ...]
    snapshots: tuple[PortfolioSnapshot, ...]
    disclaimer: str

    def __post_init__(self) -> None:
        """Yasal metin uyumunu denetler."""
        validate_compliant_text(self.disclaimer)

    def generate_performance_report(
        self,
        risk_free_rate_annual: float = 0.0,
        annualization_factor: float = 252.0,
    ) -> BacktestPerformanceReport:
        """Sharpe, maksimum düşüş ve getiri metriklerini içeren kapsamlı rapor üretir."""
        return calculate_backtest_performance(
            self,
            risk_free_rate_annual=risk_free_rate_annual,
            annualization_factor=annualization_factor,
        )


class StrategySimulator:
    """Piyasa sürtünmeleri, gecikme ve pozisyon sınırlarını uygulayan strateji motoru."""

    DEFAULT_DISCLAIMER = (
        "Bu strateji simülasyonu yalnızca model ve analiz yöntemlerinin geçmiş piyasa "
        "sürtünmeleri altındaki performans duyarlılığını ölçmek amacıyla hazırlanmıştır; "
        "yatırım danışmanlığı veya getiri vaadi niteliğinde değildir. "
        "Geçmiş performanslar gelecekteki sonuçların göstergesi olamaz."
    )

    def __init__(self, config: StrategyBacktestConfig | None = None) -> None:
        """Simülatörü başlatır."""
        self.config = config or StrategyBacktestConfig()
        self.execution_engine = ExecutionSimulator(self.config.friction_config)

    def simulate(
        self,
        bars: pd.DataFrame,
        signals: pd.Series | Sequence[float] | np.ndarray,
        symbol: str = "BIST_DEFAULT",
    ) -> StrategySimulationResult:
        """Fiyat çubukları ve hedef ağırlık/sinyalleri üzerinde simülasyon yürütür.

        Parametreler:
            bars: 'close' ve isteğe bağlı 'volume' sütunlarını içeren zaman serisi DataFrame'i.
            signals: Her bar için hedeflenen pozisyon sinyali.
                     1.0 = tam pozisyon alış, 0.0 = nakit.
                     Ara değerler (örn. 0.5) kısmi pozisyon ağırlığı ifade eder.
            symbol: İncelenen hisse sembolü.

        Kısıtlar:
            - İcra gecikmesi: t barında verilen sinyal, t + execution_lag_bars anında icra edilir.
            - Pozisyon sınırı: Hedef ağırlık max_position_weight değerini aşamaz.
            - Açığa satış kısıtı: allow_short=False ise negatif ağırlıklar 0.0'a kırpılır.
            - Piyasa sürtünmeleri: komisyon, BSMV, borsa payı, spread ve slippage uygulanır.
        """
        if "close" not in bars.columns:
            raise ValueError("Fiyat verisi 'close' sütunu içermelidir.")

        n_bars = len(bars)
        if n_bars < 2:
            raise ValueError(f"Simülasyon için en az 2 bar gereklidir (mevcut: {n_bars}).")

        # Sinyal serisini doğrula ve numpy dizisine çevir
        raw_signals = np.asarray(signals, dtype=float)
        if len(raw_signals) != n_bars:
            raise ValueError(
                f"Sinyal serisi uzunluğu ({len(raw_signals)}) "
                f"fiyat barı sayısı ({n_bars}) ile eşleşmiyor."
            )

        # Gecikme (Lag) uygulama:
        # t anında üretilen sinyal ancak t + lag anında yürürlüğe girer.
        # Bu nedenle sinyalleri lag kadar sağa kaydırıyoruz; ilk lag kadar barda pozisyon 0'dır.
        lag = self.config.execution_lag_bars
        effective_signals = np.zeros(n_bars, dtype=float)
        if lag > 0:
            effective_signals[lag:] = raw_signals[:-lag]
        else:
            effective_signals[:] = raw_signals[:]

        # Pozisyon sınırlarını uygula
        for i in range(n_bars):
            val = float(effective_signals[i])
            if not self.config.allow_short and val < 0.0:
                val = 0.0
            if val > self.config.max_position_weight:
                val = self.config.max_position_weight
            elif self.config.allow_short and val < -self.config.max_position_weight:
                val = -self.config.max_position_weight
            effective_signals[i] = val

        # Simülasyon durumu
        cash = float(self.config.initial_capital)
        shares = 0.0
        gross_cash = float(self.config.initial_capital)
        gross_shares = 0.0

        trades: list[TradeRecord] = []
        snapshots: list[PortfolioSnapshot] = []

        total_friction_try = 0.0
        max_position_weight_observed = 0.0

        close_prices = bars["close"].to_numpy(dtype=float)
        volumes = (
            bars["volume"].to_numpy(dtype=float)
            if "volume" in bars.columns
            else np.full(n_bars, 100_000.0)
        )
        timestamps = bars.index.to_list() if hasattr(bars.index, "to_list") else list(range(n_bars))

        for idx in range(n_bars):
            price = float(close_prices[idx])
            volume = float(volumes[idx])
            ts = timestamps[idx]
            target_weight = float(effective_signals[idx])

            # Mevcut portföy değeri
            current_portfolio_value = cash + (shares * price)
            current_gross_value = gross_cash + (gross_shares * price)

            # Hedeflenen pay adedi hesabı
            # Nakit tamponunu hesaba katarak güvenli azami sermaye
            allocatable_capital = current_portfolio_value * (1.0 - self.config.cash_buffer_pct)
            target_value = allocatable_capital * target_weight
            target_shares = float(np.floor(target_value / price)) if price > 0 else 0.0

            shares_diff = target_shares - shares

            # İşlem icrası gerekiyor mu?
            trade_value_est = abs(shares_diff) * price
            if trade_value_est >= self.config.min_trade_size_try and abs(shares_diff) >= 1.0:
                action = TradeAction.ENTRY if shares_diff > 0 else TradeAction.EXIT
                order_shares = abs(shares_diff)

                # Friksiyonları hesapla
                detail = self.execution_engine.calculate_execution_price(
                    nominal_price=price,
                    action=action,
                    order_shares=order_shares,
                    daily_volume=volume,
                )

                if action == TradeAction.ENTRY:
                    total_cost = (order_shares * detail.effective_price) + (
                        detail.commission_try + detail.bsmv_try + detail.exchange_fee_try
                    )
                    # Yetersiz nakit koruması: nakit yetmiyorsa pay adedini küçült
                    if total_cost > cash and cash > self.config.min_trade_size_try:
                        adjusted_shares = float(
                            np.floor(cash / (detail.effective_price * (1.0 + 0.003)))
                        )
                        if adjusted_shares >= 1.0:
                            order_shares = adjusted_shares
                            detail = self.execution_engine.calculate_execution_price(
                                nominal_price=price,
                                action=action,
                                order_shares=order_shares,
                                daily_volume=volume,
                            )
                            total_cost = (order_shares * detail.effective_price) + (
                                detail.commission_try + detail.bsmv_try + detail.exchange_fee_try
                            )

                    if total_cost <= cash:
                        cash -= total_cost
                        shares += order_shares
                        total_friction_try += detail.total_friction_try

                        trades.append(
                            TradeRecord(
                                bar_index=idx,
                                timestamp=ts,
                                action=action,
                                nominal_price=detail.nominal_price,
                                effective_price=detail.effective_price,
                                shares=order_shares,
                                trade_value_try=detail.trade_value_try,
                                commission_try=detail.commission_try,
                                bsmv_try=detail.bsmv_try,
                                exchange_fee_try=detail.exchange_fee_try,
                                spread_cost_try=detail.spread_cost_try,
                                slippage_cost_try=detail.slippage_cost_try,
                                total_friction_try=detail.total_friction_try,
                                cash_after=cash,
                                shares_after=shares,
                                portfolio_value_after=cash + (shares * price),
                            )
                        )
                else:  # EXIT
                    net_proceeds = (order_shares * detail.effective_price) - (
                        detail.commission_try + detail.bsmv_try + detail.exchange_fee_try
                    )
                    cash += net_proceeds
                    shares -= order_shares
                    total_friction_try += detail.total_friction_try

                    trades.append(
                        TradeRecord(
                            bar_index=idx,
                            timestamp=ts,
                            action=action,
                            nominal_price=detail.nominal_price,
                            effective_price=detail.effective_price,
                            shares=order_shares,
                            trade_value_try=detail.trade_value_try,
                            commission_try=detail.commission_try,
                            bsmv_try=detail.bsmv_try,
                            exchange_fee_try=detail.exchange_fee_try,
                            spread_cost_try=detail.spread_cost_try,
                            slippage_cost_try=detail.slippage_cost_try,
                            total_friction_try=detail.total_friction_try,
                            cash_after=cash,
                            shares_after=shares,
                            portfolio_value_after=cash + (shares * price),
                        )
                    )

            # Brüt (Maliyetsiz ve gecikmesiz değil, yalnızca sürtünmesiz nominal kıyas)
            gross_target_val = current_gross_value * target_weight
            gross_target_shares = float(np.floor(gross_target_val / price)) if price > 0 else 0.0
            gross_shares_diff = gross_target_shares - gross_shares
            if (
                abs(gross_shares_diff) >= 1.0
                and abs(gross_shares_diff) * price >= self.config.min_trade_size_try
            ):
                gross_nominal_cost = gross_shares_diff * price
                if gross_shares_diff > 0:
                    if gross_nominal_cost <= gross_cash:
                        gross_cash -= gross_nominal_cost
                        gross_shares += gross_shares_diff
                else:
                    gross_cash += abs(gross_nominal_cost)
                    gross_shares -= abs(gross_shares_diff)

            # Bar sonu portföy değerleri
            pos_val = shares * price
            tot_val = cash + pos_val
            pos_weight = (pos_val / tot_val) if tot_val > 0 else 0.0
            max_position_weight_observed = max(max_position_weight_observed, pos_weight)

            snapshots.append(
                PortfolioSnapshot(
                    bar_index=idx,
                    timestamp=ts,
                    price=price,
                    cash=cash,
                    shares=shares,
                    position_value=pos_val,
                    portfolio_value=tot_val,
                    position_weight=pos_weight,
                    gross_value=gross_cash + (gross_shares * price),
                )
            )

        final_capital = cash + (shares * close_prices[-1])
        gross_final_capital = gross_cash + (gross_shares * close_prices[-1])

        init_cap = self.config.initial_capital
        net_return_pct = ((final_capital - init_cap) / init_cap) * 100.0
        gross_return_pct = ((gross_final_capital - init_cap) / init_cap) * 100.0
        friction_drag_pct = gross_return_pct - net_return_pct

        is_realistic = (
            self.config.execution_lag_bars >= 1
            and self.config.friction_config.require_frictions
            and not (self.config.allow_zero_lag and self.config.execution_lag_bars == 0)
        )

        return StrategySimulationResult(
            symbol=symbol,
            config=self.config,
            initial_capital=init_cap,
            final_capital=final_capital,
            gross_final_capital=gross_final_capital,
            gross_return_pct=gross_return_pct,
            net_return_pct=net_return_pct,
            friction_drag_pct=friction_drag_pct,
            total_trades=len(trades),
            total_friction_try=total_friction_try,
            max_position_weight_observed=max_position_weight_observed,
            execution_lag_applied=self.config.execution_lag_bars,
            is_realistic=is_realistic,
            trades=tuple(trades),
            snapshots=tuple(snapshots),
            disclaimer=self.DEFAULT_DISCLAIMER,
        )
