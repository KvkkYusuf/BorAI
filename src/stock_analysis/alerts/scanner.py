"""Kullanıcı izleme listesi ve piyasa evreni üzerinde trend tarama işi motoru.

Bu modül, FAZ-12 gereksinimlerine ve SPK mevzuatına tam uyumlu çalışır:
- Kullanıcının izleme listesi (Watchlist) veya seçtiği evren (BIST 30, BIST 100, Özel) üzerinde
  çoklu gösterge teyidiyle tarama yapar.
- N+1 sorgu kalıplarını engellemek için toplu veri çekme ve parça işleme uygular.
- Veri gecikmesini (provider delay) saniye cinsinden hesaplar ve gecikmeli veriyi raporlar.
- Çıktılarda kesinlikle al/sat, hedef fiyat veya yönlendirici tavsiye ifadeleri bulunmaz.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import desc, select

from stock_analysis.db.models import Instrument, MarketBar, Watchlist, WatchlistItem
from stock_analysis.indicators.trend import (
    QualityLiquidityFilterConfig,
    TrendDirection,
    TrendEngine,
    TrendSignalResult,
    validate_compliant_text,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession

# BIST 30 ve BIST 100 için standart piyasa evrenleri
BIST_30_UNIVERSE: tuple[str, ...] = (
    "AKBNK",
    "ALARK",
    "ARCLK",
    "ASELS",
    "ASTOR",
    "BIMAS",
    "EKGYO",
    "ENKAI",
    "EREGL",
    "FROTO",
    "GARAN",
    "HALKB",
    "ISCTR",
    "KCHOL",
    "KOZAL",
    "KRDMD",
    "MGROS",
    "OYAKC",
    "PETKM",
    "PGSUS",
    "SAHOL",
    "SISE",
    "TAVHL",
    "TCELL",
    "THYAO",
    "TOASO",
    "TTKOM",
    "TUPRS",
    "VAKBN",
    "YKBNK",
)

BIST_100_SAMPLE: tuple[str, ...] = BIST_30_UNIVERSE + (
    "AEFES",
    "AGHOL",
    "AHGAZ",
    "AKSA",
    "AKSEN",
    "ALGYO",
    "ANSGR",
    "BERA",
    "BRSAN",
    "CANTE",
    "CCOLA",
    "CIMSA",
    "DOHOL",
    "EGEEN",
    "ENJSA",
    "GESAN",
    "GUBRF",
    "HEKTS",
    "ISGYO",
    "KCAER",
    "KONTR",
    "KORDS",
    "MAVI",
    "ODAS",
    "OTKAR",
    "OYAKC",
    "QUAGR",
    "SASA",
    "SOKM",
    "TKFEN",
    "TMSN",
    "TSKB",
    "TTRAK",
    "ULKER",
    "VESBE",
    "VESTL",
    "YEOTK",
    "ZOREN",
)

DISCLAIMER_SCANNER: str = (
    "Bu tarama sonuçları yalnızca teknik ve matematiksel gösterge modellerinin anlık durumunu "
    "özetlemektedir; kesinlikle yatırım danışmanlığı, yönlendirme veya al/sat tavsiyesi değildir."
)


class ScanUniverseType(StrEnum):
    """Tarama yapılacak evren türü."""

    WATCHLIST = "WATCHLIST"
    BIST_30 = "BIST_30"
    BIST_100 = "BIST_100"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class ScanCriteria:
    """Tarama parametreleri ve filtreleme koşulları."""

    direction_preference: TrendDirection | None = None  # None ise hem BULLISH hem BEARISH taranır
    min_rules_met: int = 2
    min_categories_met: int = 2
    filter_config: QualityLiquidityFilterConfig | None = None
    require_trend_only: bool = True  # True ise NEUTRAL sinyaller eşleşme sayılmaz
    min_confidence: float = 0.50
    lookback_bars: int = 60
    limit: int | None = None


@dataclass(frozen=True)
class SymbolScanMatch:
    """Evren taramasında bir hisse için üretilen eşleşme sonucu."""

    symbol: str
    instrument_name: str
    last_close: float
    trend_direction: TrendDirection
    confidence_score: float
    headline: str
    explanation: str
    met_rules: tuple[str, ...]
    met_categories: tuple[str, ...]
    invalidation_criteria: tuple[str, ...]
    liquidity_passed: bool
    filter_failures: tuple[str, ...]
    data_timestamp_utc: datetime
    provider_delay_seconds: int
    is_delayed: bool
    evaluated_at_utc: datetime

    def __post_init__(self) -> None:
        """Tüm metin çıktılarının SPK kırmızı çizgilerine uygunluğunu doğrula."""
        validate_compliant_text(self.headline)
        validate_compliant_text(self.explanation)
        for crit in self.invalidation_criteria:
            validate_compliant_text(crit)


@dataclass
class ScanExecutionSummary:
    """Tarama işi yürütme özeti ve metrikleri."""

    universe_type: ScanUniverseType
    universe_identifier: str
    total_symbols_in_universe: int
    evaluated_symbols: int
    matched_symbols: int
    skipped_symbols: int
    execution_duration_ms: float
    executed_at_utc: datetime
    matches: list[SymbolScanMatch] = field(default_factory=list)
    disclaimer: str = DISCLAIMER_SCANNER


class UniverseScanner:
    """İzleme listesi ve hisse senedi evreni üzerinde teknik trend tarayıcısı."""

    def __init__(
        self,
        trend_engine: TrendEngine | None = None,
        chunk_size: int = 25,
        delay_threshold_seconds: int = 900,  # 15 dakika üzeri gecikmeli veri kabul edilir
    ) -> None:
        self.trend_engine = trend_engine or TrendEngine()
        self.chunk_size = chunk_size
        self.delay_threshold_seconds = delay_threshold_seconds

    def scan_watchlist(
        self,
        user_id: int,
        session: DBSession,
        criteria: ScanCriteria | None = None,
        as_of_time: datetime | None = None,
    ) -> ScanExecutionSummary:
        """Kullanıcının veritabanındaki izleme listesini tarar."""
        # Kullanıcının varsayılan izleme listesindeki sembolleri tek sorguda çek
        stmt = (
            select(Instrument.symbol)
            .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
            .where(Watchlist.user_id == user_id, Instrument.is_active == True)  # noqa: E712
            .order_by(WatchlistItem.added_at.desc())
        )
        symbols = list(session.execute(stmt).scalars().all())

        return self.scan_universe(
            universe_type=ScanUniverseType.WATCHLIST,
            session=session,
            custom_symbols=symbols,
            criteria=criteria,
            universe_identifier=f"user_{user_id}_watchlist",
            as_of_time=as_of_time,
        )

    def scan_universe(
        self,
        universe_type: ScanUniverseType,
        session: DBSession | None = None,
        custom_symbols: list[str] | None = None,
        criteria: ScanCriteria | None = None,
        data_provider: dict[str, pd.DataFrame] | None = None,
        universe_identifier: str | None = None,
        as_of_time: datetime | None = None,
    ) -> ScanExecutionSummary:
        """Belirtilen evren ve ölçütlere göre tarama işini çalıştırır.

        Veri kaynağı olarak ya bir DBSession ya da önbellekten/bellekten
        hazır DataFrameler (`data_provider`) verilebilir.
        """
        start_time = time.perf_counter()
        now_utc = as_of_time or datetime.now(UTC)
        scan_criteria = criteria or ScanCriteria()

        # 1. Taranacak sembol kümesini belirle
        target_symbols: list[str] = self._resolve_universe_symbols(
            universe_type=universe_type,
            custom_symbols=custom_symbols,
        )

        total_symbols = len(target_symbols)
        if total_symbols == 0:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ScanExecutionSummary(
                universe_type=universe_type,
                universe_identifier=universe_identifier or universe_type.value,
                total_symbols_in_universe=0,
                evaluated_symbols=0,
                matched_symbols=0,
                skipped_symbols=0,
                execution_duration_ms=round(duration_ms, 2),
                executed_at_utc=now_utc,
                matches=[],
            )

        # 2. Sembol verilerini toplu veya sağlayıcıdan yükle
        matches: list[SymbolScanMatch] = []
        evaluated_count = 0
        skipped_count = 0

        # Eğer data_provider verilmişse bellek üzerinden çalış (sıfır DB sorgusu)
        if data_provider is not None:
            for symbol in target_symbols:
                df = data_provider.get(symbol)
                if df is None or df.empty or len(df) < 5:
                    skipped_count += 1
                    continue

                evaluated_count += 1
                match = self._evaluate_symbol_dataframe(
                    symbol=symbol,
                    instrument_name=symbol,
                    df=df,
                    criteria=scan_criteria,
                    now_utc=now_utc,
                )
                if match is not None:
                    matches.append(match)
                    if scan_criteria.limit and len(matches) >= scan_criteria.limit:
                        break
        else:
            if session is None:
                raise ValueError(
                    "Veri taraması için ya 'session' ya da 'data_provider' gereklidir."
                )

            # N+1 engellemek için sembolleri parçalar halinde yükle
            for i in range(0, total_symbols, self.chunk_size):
                chunk = target_symbols[i : i + self.chunk_size]
                chunk_data = self._fetch_chunk_bars(session, chunk, scan_criteria.lookback_bars)

                for symbol in chunk:
                    if symbol not in chunk_data:
                        skipped_count += 1
                        continue

                    inst_name, df = chunk_data[symbol]
                    if df.empty or len(df) < 5:
                        skipped_count += 1
                        continue

                    evaluated_count += 1
                    match = self._evaluate_symbol_dataframe(
                        symbol=symbol,
                        instrument_name=inst_name,
                        df=df,
                        criteria=scan_criteria,
                        now_utc=now_utc,
                    )
                    if match is not None:
                        matches.append(match)
                        if scan_criteria.limit and len(matches) >= scan_criteria.limit:
                            break

                if scan_criteria.limit and len(matches) >= scan_criteria.limit:
                    break

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return ScanExecutionSummary(
            universe_type=universe_type,
            universe_identifier=universe_identifier or universe_type.value,
            total_symbols_in_universe=total_symbols,
            evaluated_symbols=evaluated_count,
            matched_symbols=len(matches),
            skipped_symbols=skipped_count,
            execution_duration_ms=round(duration_ms, 2),
            executed_at_utc=now_utc,
            matches=matches,
        )

    def _resolve_universe_symbols(
        self,
        universe_type: ScanUniverseType,
        custom_symbols: list[str] | None,
    ) -> list[str]:
        """Evren türüne göre taranacak sembol listesini çıkarır."""
        if universe_type == ScanUniverseType.WATCHLIST or universe_type == ScanUniverseType.CUSTOM:
            return list(dict.fromkeys(custom_symbols or []))
        if universe_type == ScanUniverseType.BIST_30:
            return list(BIST_30_UNIVERSE)
        if universe_type == ScanUniverseType.BIST_100:
            return list(BIST_100_SAMPLE)
        return []

    def _fetch_chunk_bars(
        self,
        session: DBSession,
        symbols: list[str],
        lookback_bars: int,
    ) -> dict[str, tuple[str, pd.DataFrame]]:
        """N+1 sorgularını engelleyen toplu bar çekme mekanizması."""
        # 1. Enstrümanları tek sorguda çek
        inst_stmt = select(Instrument).where(
            Instrument.symbol.in_(symbols),
            Instrument.is_active == True,  # noqa: E712
        )
        instruments = session.execute(inst_stmt).scalars().all()
        if not instruments:
            return {}

        inst_map = {inst.id: inst for inst in instruments}
        inst_ids = list(inst_map.keys())

        # 2. Barları tek bir sorguyla çek (lookback sınırını koruyacak yeterli bar)
        # Her sembol için en son (lookback_bars + 30) barı çekmek üzere timestamp desc sorgusu
        bars_stmt = (
            select(MarketBar)
            .where(
                MarketBar.instrument_id.in_(inst_ids),
                MarketBar.is_adjusted == False,  # noqa: E712
            )
            .order_by(MarketBar.instrument_id, desc(MarketBar.timestamp))
        )
        all_bars = session.execute(bars_stmt).scalars().all()

        # Enstrüman bazında grupla (en fazla lookback_bars + 20 adet)
        max_bars_per_symbol = lookback_bars + 20
        grouped_bars: dict[int, list[MarketBar]] = defaultdict(list)
        for bar in all_bars:
            if len(grouped_bars[bar.instrument_id]) < max_bars_per_symbol:
                grouped_bars[bar.instrument_id].append(bar)

        results: dict[str, tuple[str, pd.DataFrame]] = {}
        for inst_id, bars in grouped_bars.items():
            inst = inst_map.get(inst_id)
            if not inst or not bars:
                continue

            # Barları kronolojik sıraya çevir
            bars.reverse()
            data = [
                {
                    "timestamp": b.timestamp,
                    "open": b.open_price,
                    "high": b.high_price,
                    "low": b.low_price,
                    "close": b.close_price,
                    "volume": b.volume,
                }
                for b in bars
            ]
            df = pd.DataFrame(data)
            df.set_index("timestamp", inplace=True)
            results[inst.symbol] = (inst.name, df)

        return results

    def _evaluate_symbol_dataframe(
        self,
        symbol: str,
        instrument_name: str,
        df: pd.DataFrame,
        criteria: ScanCriteria,
        now_utc: datetime,
    ) -> SymbolScanMatch | None:
        """Bir hisse senedi DataFrame verisini tarama ölçütlerine göre değerlendirir."""
        # Trend analizi yap
        trend_result: TrendSignalResult = self.trend_engine.evaluate(df=df, symbol=symbol)

        # 1. Trend yönü kontrolü
        if criteria.require_trend_only and trend_result.direction == TrendDirection.NEUTRAL:
            return None

        if (
            criteria.direction_preference is not None
            and trend_result.direction != criteria.direction_preference
        ):
            return None

        # 2. Gösterge teyit sayısı ve kategorisi kontrolü
        if len(trend_result.met_rules) < criteria.min_rules_met:
            return None
        if len(trend_result.met_categories) < criteria.min_categories_met:
            return None

        # 3. Güven skoru hesabı (kural ve kategori zenginliğine göre oransal)
        # Örnek: 4 kuraldan 3'ü met ise 0.75 taban
        confidence = min(
            1.0,
            0.50 + (len(trend_result.met_rules) * 0.15) + (len(trend_result.met_categories) * 0.10),
        )
        if confidence < criteria.min_confidence:
            return None

        # 4. Son bar ve zaman kontrolü
        last_bar_time = df.index[-1]
        if isinstance(last_bar_time, str):
            try:
                data_timestamp = datetime.fromisoformat(last_bar_time)
                if data_timestamp.tzinfo is None:
                    data_timestamp = data_timestamp.replace(tzinfo=UTC)
            except ValueError:
                data_timestamp = now_utc
        elif isinstance(last_bar_time, pd.Timestamp):
            data_timestamp = (
                last_bar_time.to_pydatetime()
                if last_bar_time.tzinfo
                else last_bar_time.to_pydatetime().replace(tzinfo=UTC)
            )
        else:
            data_timestamp = now_utc

        # Veri sağlayıcı gecikmesini ölç
        provider_delay = max(0, int((now_utc - data_timestamp).total_seconds()))
        is_delayed = provider_delay > self.delay_threshold_seconds

        last_close = float(df["close"].iloc[-1])

        return SymbolScanMatch(
            symbol=symbol,
            instrument_name=instrument_name,
            last_close=last_close,
            trend_direction=trend_result.direction,
            confidence_score=round(confidence, 2),
            headline=trend_result.headline,
            explanation=trend_result.explanation,
            met_rules=trend_result.met_rules,
            met_categories=trend_result.met_categories,
            invalidation_criteria=trend_result.invalidation_criteria,
            liquidity_passed=trend_result.filter_result.passed,
            filter_failures=trend_result.filter_result.failures,
            data_timestamp_utc=data_timestamp,
            provider_delay_seconds=provider_delay,
            is_delayed=is_delayed,
            evaluated_at_utc=now_utc,
        )
