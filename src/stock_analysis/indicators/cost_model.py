"""İşlem maliyeti, alış-satış makası (spread) ve kayma (slippage) simülasyonu.

Bu modül, finansal piyasalarda ve Borsa İstanbul'da (BIST) analitik stratejilerin
performansını değerlendirirken piyasa sürtünmelerini (friksiyon) zorunlu kılar:
- Sıfır maliyetli ("frictionless") analizleri ASLA gerçekçi kabul etmez (`is_realistic=False`).
- Aracı kurum komisyonu, BSMV, borsa tescil/takas payını hesaba katar.
- Alış-satış makasını (spread) ve emir büyüklüğü/likidite kaynaklı kaymayı (slippage) uygular.
- SPK ve finans-hukuk kurallarına uygun olarak geçmiş eğilimlerin maliyet duyarlılığını ölçer,
  kesin getiri vaadinde bulunmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from stock_analysis.indicators.trend import validate_compliant_text


class TradeAction(StrEnum):
    """Simüle edilen işlem yönü."""

    ENTRY = "ENTRY"  # Pozisyona giriş simülasyonu
    EXIT = "EXIT"  # Pozisyondan çıkış simülasyonu


@dataclass(frozen=True)
class MarketFrictionConfig:
    """Gerçekçi işlem maliyeti ve piyasa sürtünme parametreleri."""

    commission_rate: float = 0.002  # Binde 2 aracı kurum komisyonu (0.0020)
    bsmv_rate: float = 0.05  # Komisyon üzerinden %5 BSMV (Banka ve Sigorta Muameleleri Vergisi)
    exchange_fee_rate: float = 0.00005  # BIST / Takasbank borsa payı (yüz binde 5)
    half_spread_pct: float = 0.0010  # Binde 1 ortalama yarım makas payı (alış/satış)
    slippage_impact_factor: float = 0.05  # Likidite etki katsayısı (kayma modeli)
    require_frictions: bool = True  # Maliyetsiz hesaplamayı doğrudan geçersiz sayma kuralı


@dataclass(frozen=True)
class TradeExecutionDetail:
    """Tekil bir simüle işlem için gerçekleşme ve friksiyon detayları."""

    action: TradeAction
    nominal_price: float
    effective_price: float
    volume_shares: float
    trade_value_try: float
    commission_try: float
    bsmv_try: float
    exchange_fee_try: float
    spread_cost_try: float
    slippage_cost_try: float
    total_friction_try: float
    effective_friction_pct: float


@dataclass(frozen=True)
class StrategyPerformanceResult:
    """Strateji geriye dönük maliyet ve net getiri değerlendirmesi."""

    symbol: str
    is_realistic: bool
    frictions_applied: bool
    gross_return_pct: float
    net_return_pct: float
    friction_drag_pct: float  # Maliyetlerin getiriden sildiği toplam puan
    total_trades: int
    total_friction_try: float
    summary_assessment: str
    validity_notes: tuple[str, ...]
    trade_details: tuple[TradeExecutionDetail, ...] = field(default_factory=tuple)
    disclaimer: str = ""

    def __post_init__(self) -> None:
        """Metin çıktılarının SPK uyumunu denetler."""
        validate_compliant_text(self.summary_assessment)
        for note in self.validity_notes:
            validate_compliant_text(note)


class ExecutionSimulator:
    """İşlem maliyeti, spread ve kayma payını hesaplayan simülasyon motoru."""

    DEFAULT_DISCLAIMER = (
        "Bu analiz yalnızca işlem maliyetlerinin ve piyasa sürtünmelerinin eğilim modelleri "
        "üzerindeki duyarlılığını ölçmek amacıyla simüle edilmiştir; yatırım danışmanlığı veya "
        "al-sat tavsiyesi niteliğinde değildir. Geçmiş simülasyon sonuçları gelecekteki "
        "sonuçların göstergesi olamaz."
    )

    def __init__(self, config: MarketFrictionConfig | None = None) -> None:
        """Simülatör başlatma."""
        self.config = config or MarketFrictionConfig()

    def calculate_execution_price(
        self,
        nominal_price: float,
        action: TradeAction,
        order_shares: float,
        daily_volume: float = 100_000.0,
    ) -> TradeExecutionDetail:
        """Nominal fiyatı spread, slippage ve yasal komisyonlarla düzeltir.

        Alışta (ENTRY):
            effective_price = nominal_price * (1 + half_spread + slippage)
        Satışta (EXIT):
            effective_price = nominal_price * (1 - half_spread - slippage)
        """
        if nominal_price <= 0 or order_shares <= 0:
            raise ValueError("Fiyat ve pay adedi pozitif olmalıdır.")

        # Kayma (Slippage) hesabı: order_shares / daily_volume oranı ile ölçeklenir
        participation_rate = min(0.5, order_shares / max(1.0, daily_volume))
        slippage_pct = self.config.slippage_impact_factor * float(np.sqrt(participation_rate))

        spread_pct = self.config.half_spread_pct

        if action == TradeAction.ENTRY:
            price_impact_pct = spread_pct + slippage_pct
            effective_price = nominal_price * (1.0 + price_impact_pct)
        else:
            price_impact_pct = spread_pct + slippage_pct
            effective_price = nominal_price * (1.0 - price_impact_pct)

        trade_value = order_shares * nominal_price
        effective_trade_value = order_shares * effective_price

        # Yasal komisyon ve vergiler
        commission = effective_trade_value * self.config.commission_rate
        bsmv = commission * self.config.bsmv_rate
        exchange_fee = effective_trade_value * self.config.exchange_fee_rate

        spread_cost = trade_value * spread_pct
        slippage_cost = trade_value * slippage_pct
        total_friction = commission + bsmv + exchange_fee + spread_cost + slippage_cost
        friction_pct = (total_friction / trade_value) * 100.0 if trade_value > 0 else 0.0

        return TradeExecutionDetail(
            action=action,
            nominal_price=nominal_price,
            effective_price=effective_price,
            volume_shares=order_shares,
            trade_value_try=trade_value,
            commission_try=commission,
            bsmv_try=bsmv,
            exchange_fee_try=exchange_fee,
            spread_cost_try=spread_cost,
            slippage_cost_try=slippage_cost,
            total_friction_try=total_friction,
            effective_friction_pct=friction_pct,
        )

    def evaluate_strategy_friction(
        self,
        df: pd.DataFrame,
        signals: pd.Series,
        symbol: str = "BIST_STOCK",
        initial_capital: float = 100_000.0,
        enable_frictions: bool = True,
    ) -> StrategyPerformanceResult:
        """Belirli bir sinyal serisinin brüt vs net performansını simüle eder.

        Args:
            df: OHLCV DataFrame serisi (en az 'close', 'volume' sütunları).
            signals: 1 (pozisyon aç/tut), 0 (nakitte kal) değerleri içeren seri.
            symbol: Hisse senedi kodu.
            initial_capital: Başlangıç sermayesi (TL).
            enable_frictions: Maliyetlerin dahil edilip edilmeyeceği.
        """
        if len(df) != len(signals):
            raise ValueError("Fiyat serisi ile sinyal serisinin uzunlukları eşit olmalıdır.")

        notes: list[str] = []
        has_friction_rates = self.config.commission_rate > 0 or self.config.half_spread_pct > 0
        is_realistic = enable_frictions and has_friction_rates

        if not is_realistic:
            notes.append(
                "İşlem maliyetleri, spread ve kayma payı hesaba katılmamıştır. "
                "Bu sonuç gerçekçi bir piyasa performansını temsil etmez."
            )

        closes = df["close"].to_numpy()
        volumes = df["volume"].to_numpy() if "volume" in df.columns else np.full(len(df), 100_000.0)
        sig = signals.to_numpy()

        gross_capital = initial_capital
        net_capital = initial_capital

        in_pos = False
        shares = 0.0
        trade_details: list[TradeExecutionDetail] = []
        total_friction_sum = 0.0

        for i in range(1, len(df)):
            curr_close = closes[i]
            prev_sig = sig[i - 1]
            curr_vol = max(100.0, float(volumes[i]))

            # Alım sinyali: Pozisyonda değilken 1 oldu
            if prev_sig == 1 and not in_pos:
                if enable_frictions:
                    cost_buffer = 1.0 + (
                        self.config.half_spread_pct + self.config.commission_rate * 2.0 + 0.005
                    )
                    nominal_shares = float(np.floor(net_capital / (curr_close * cost_buffer)))
                    if nominal_shares > 0:
                        detail = self.calculate_execution_price(
                            nominal_price=curr_close,
                            action=TradeAction.ENTRY,
                            order_shares=nominal_shares,
                            daily_volume=curr_vol,
                        )
                        # Efektif maliyetle net sermayeden düş
                        total_entry_cost = (
                            detail.effective_price * nominal_shares
                            + detail.commission_try
                            + detail.bsmv_try
                            + detail.exchange_fee_try
                        )
                        if total_entry_cost <= net_capital:
                            shares = nominal_shares
                            net_capital -= total_entry_cost
                            total_friction_sum += detail.total_friction_try
                            trade_details.append(detail)
                            in_pos = True
                else:
                    shares = float(np.floor(gross_capital / curr_close))
                    gross_capital -= shares * curr_close
                    in_pos = True

            # Satım sinyali: Pozisyondayken 0 oldu
            elif prev_sig == 0 and in_pos and shares > 0:
                if enable_frictions:
                    detail = self.calculate_execution_price(
                        nominal_price=curr_close,
                        action=TradeAction.EXIT,
                        order_shares=shares,
                        daily_volume=curr_vol,
                    )
                    net_proceeds = (
                        detail.effective_price * shares
                        - detail.commission_try
                        - detail.bsmv_try
                        - detail.exchange_fee_try
                    )
                    net_capital += net_proceeds
                    total_friction_sum += detail.total_friction_try
                    trade_details.append(detail)
                    shares = 0.0
                    in_pos = False
                else:
                    gross_capital += shares * curr_close
                    shares = 0.0
                    in_pos = False

        # Dönem sonunda açık pozisyonu son fiyattan kapat
        last_price = closes[-1]
        last_vol = max(100.0, float(volumes[-1]))
        if in_pos and shares > 0:
            if enable_frictions:
                detail = self.calculate_execution_price(
                    nominal_price=last_price,
                    action=TradeAction.EXIT,
                    order_shares=shares,
                    daily_volume=last_vol,
                )
                net_capital += (
                    detail.effective_price * shares
                    - detail.commission_try
                    - detail.bsmv_try
                    - detail.exchange_fee_try
                )
                total_friction_sum += detail.total_friction_try
                trade_details.append(detail)
            else:
                gross_capital += shares * last_price

        # Brüt getiri (friksiyonsuz karşılaştırma)
        # Basit "close-to-close" sinyal getirisi
        price_returns = np.diff(closes) / closes[:-1]
        gross_strategy_returns = price_returns * sig[:-1]
        gross_cum_return = float((np.prod(1.0 + gross_strategy_returns) - 1.0) * 100.0)

        net_cum_return = (
            float(((net_capital - initial_capital) / initial_capital) * 100.0)
            if enable_frictions
            else gross_cum_return
        )

        friction_drag = gross_cum_return - net_cum_return

        if friction_drag > 5.0:
            notes.append(
                f"Yüksek işlem sürtünmesi: Maliyetler toplam getiriden "
                f"{friction_drag:.2f} puan eksiltmiştir."
            )

        if gross_cum_return > 0 and net_cum_return < 0:
            notes.append(
                "Maliyet tuzağı: Strateji brüt bazda kârda görünmesine rağmen işlem komisyonu, "
                "makas ve kayma hesaba katıldığında net zarara uğramıştır."
            )

        assessment = (
            f"Net simülasyon getirisi: %{net_cum_return:.2f} (Brüt: %{gross_cum_return:.2f}, "
            f"Friksiyon kaybı: {friction_drag:.2f} puan, Toplam işlem: {len(trade_details)} adet)."
        )

        return StrategyPerformanceResult(
            symbol=symbol,
            is_realistic=is_realistic,
            frictions_applied=enable_frictions,
            gross_return_pct=gross_cum_return,
            net_return_pct=net_cum_return,
            friction_drag_pct=friction_drag,
            total_trades=len(trade_details),
            total_friction_try=total_friction_sum,
            summary_assessment=assessment,
            validity_notes=tuple(notes),
            trade_details=tuple(trade_details),
            disclaimer=self.DEFAULT_DISCLAIMER,
        )
