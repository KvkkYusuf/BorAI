"""Tests for UniverseScanner and Watchlist Trend Screening Job."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker

from stock_analysis.alerts import (
    BIST_30_UNIVERSE,
    ScanCriteria,
    ScanUniverseType,
    UniverseScanner,
)
from stock_analysis.db.base import Base
from stock_analysis.db.models import Instrument, MarketBar, User, Watchlist, WatchlistItem
from stock_analysis.indicators.trend import PROHIBITED_TERMS, TrendDirection


def _generate_synthetic_ohlcv(
    n_bars: int = 100,
    trend: str = "bullish",
    end_time: datetime | None = None,
    base_price: float = 100.0,
) -> pd.DataFrame:
    """Belirtilen eğilime uygun sentetik OHLCV DataFrame üretir."""
    end = end_time or datetime.now(UTC)
    dates = [end - timedelta(days=n_bars - 1 - i) for i in range(n_bars)]

    close = [base_price]
    for _ in range(1, n_bars):
        if trend == "bullish":
            change = np.random.normal(0.008, 0.015)
        elif trend == "bearish":
            change = np.random.normal(-0.008, 0.015)
        else:
            change = np.random.normal(0.0, 0.01)
        close.append(close[-1] * (1 + change))

    close_arr = np.array(close)
    high_arr = close_arr * (1 + np.abs(np.random.normal(0.005, 0.005, n_bars)))
    low_arr = close_arr * (1 - np.abs(np.random.normal(0.005, 0.005, n_bars)))
    open_arr = (high_arr + low_arr) / 2
    volume_arr = np.random.uniform(50_000, 200_000, n_bars)

    df = pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": volume_arr,
        },
        index=dates,
    )
    return df


@pytest.fixture
def sqlite_session() -> Generator[DBSession, None, None]:
    """Geçici bellek içi SQLite veritabanı oturumu."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def test_scanner_empty_universe() -> None:
    """Boş evren durumunda tarayıcı boş özet döndürmelidir."""
    scanner = UniverseScanner()
    summary = scanner.scan_universe(
        universe_type=ScanUniverseType.CUSTOM,
        custom_symbols=[],
    )
    assert summary.total_symbols_in_universe == 0
    assert summary.matched_symbols == 0
    assert len(summary.matches) == 0
    assert summary.execution_duration_ms >= 0


def test_scanner_with_data_provider() -> None:
    """Bellek içi veri sağlayıcısı ile hızlı tarama testi."""
    bull_df = _generate_synthetic_ohlcv(n_bars=80, trend="bullish")
    bear_df = _generate_synthetic_ohlcv(n_bars=80, trend="bearish")
    flat_df = _generate_synthetic_ohlcv(n_bars=80, trend="flat")
    insufficient_df = _generate_synthetic_ohlcv(n_bars=3, trend="bullish")

    data = {
        "THYAO": bull_df,
        "GARAN": bear_df,
        "ASELS": flat_df,
        "SHORT": insufficient_df,
    }

    scanner = UniverseScanner()
    # Yalnızca trend olanları ara (require_trend_only=True)
    summary = scanner.scan_universe(
        universe_type=ScanUniverseType.CUSTOM,
        custom_symbols=["THYAO", "GARAN", "ASELS", "SHORT", "NONEXISTENT"],
        data_provider=data,
        criteria=ScanCriteria(require_trend_only=False, min_confidence=0.5),
    )

    assert summary.total_symbols_in_universe == 5
    assert summary.evaluated_symbols == 3  # SHORT yetersiz bar, NONEXISTENT yok
    assert summary.skipped_symbols == 2
    assert summary.matched_symbols >= 1

    # Yasal uyum ve SPK yasaklı sözcük kontrolü
    for match in summary.matches:
        for term in PROHIBITED_TERMS:
            assert term not in f" {match.headline.lower()} "
            assert term not in f" {match.explanation.lower()} "
        assert match.last_close > 0


def test_scanner_filter_by_trend_direction() -> None:
    """Belirli bir trend yönüne göre filtreleme testi."""
    np.random.seed(42)
    bull_df = _generate_synthetic_ohlcv(n_bars=100, trend="bullish")
    bear_df = _generate_synthetic_ohlcv(n_bars=100, trend="bearish")

    data = {
        "BULL_STOCK": bull_df,
        "BEAR_STOCK": bear_df,
    }

    scanner = UniverseScanner()
    bull_summary = scanner.scan_universe(
        universe_type=ScanUniverseType.CUSTOM,
        custom_symbols=["BULL_STOCK", "BEAR_STOCK"],
        data_provider=data,
        criteria=ScanCriteria(
            direction_preference=TrendDirection.BULLISH_TENDENCY,
            min_confidence=0.5,
        ),
    )

    for match in bull_summary.matches:
        assert match.trend_direction == TrendDirection.BULLISH_TENDENCY


def test_scanner_limit_matches() -> None:
    """Eşleşme limiti testi."""
    df1 = _generate_synthetic_ohlcv(n_bars=90, trend="bullish")
    df2 = _generate_synthetic_ohlcv(n_bars=90, trend="bullish")
    df3 = _generate_synthetic_ohlcv(n_bars=90, trend="bullish")

    data = {"S1": df1, "S2": df2, "S3": df3}
    scanner = UniverseScanner()
    summary = scanner.scan_universe(
        universe_type=ScanUniverseType.CUSTOM,
        custom_symbols=["S1", "S2", "S3"],
        data_provider=data,
        criteria=ScanCriteria(limit=1, require_trend_only=False, min_confidence=0.4),
    )

    assert summary.matched_symbols <= 1


def test_scanner_delayed_data_detection() -> None:
    """Gecikmeli veri (15 dk üzeri) bayrağı tespiti testi."""
    old_time = datetime.now(UTC) - timedelta(minutes=45)
    old_df = _generate_synthetic_ohlcv(n_bars=80, trend="bullish", end_time=old_time)

    scanner = UniverseScanner(delay_threshold_seconds=900)
    summary = scanner.scan_universe(
        universe_type=ScanUniverseType.CUSTOM,
        custom_symbols=["DELAYED"],
        data_provider={"DELAYED": old_df},
        criteria=ScanCriteria(require_trend_only=False, min_confidence=0.4),
        as_of_time=datetime.now(UTC),
    )

    if summary.matches:
        match = summary.matches[0]
        assert match.is_delayed is True
        assert match.provider_delay_seconds >= 900


def test_scanner_db_watchlist_integration(sqlite_session: DBSession) -> None:
    """Veritabanındaki izleme listesini tarama entegrasyon testi."""
    # 1. Kullanıcı ve hisse oluştur
    user = User(
        email="trader@example.com",
        hashed_password="fakehash",
        is_active=True,
    )
    sqlite_session.add(user)
    sqlite_session.flush()

    inst1 = Instrument(symbol="THYAO", name="Türk Hava Yolları", is_active=True)
    inst2 = Instrument(symbol="GARAN", name="Garanti BBVA", is_active=True)
    sqlite_session.add_all([inst1, inst2])
    sqlite_session.flush()

    wl = Watchlist(user_id=user.id, name="Favorilerim")
    sqlite_session.add(wl)
    sqlite_session.flush()

    item1 = WatchlistItem(watchlist_id=wl.id, instrument_id=inst1.id)
    sqlite_session.add(item1)
    sqlite_session.flush()

    # THYAO için sentetik barlar ekle
    synth_df = _generate_synthetic_ohlcv(n_bars=80, trend="bullish")
    for ts, row in synth_df.iterrows():
        bar = MarketBar(
            instrument_id=inst1.id,
            timestamp=pd.Timestamp(str(ts)).to_pydatetime(),
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            volume=float(row["volume"]),
            is_adjusted=False,
            data_version="v1",
        )
        sqlite_session.add(bar)
    sqlite_session.commit()

    # Kullanıcının izleme listesini tara
    scanner = UniverseScanner()
    summary = scanner.scan_watchlist(
        user_id=user.id,
        session=sqlite_session,
        criteria=ScanCriteria(require_trend_only=False, min_confidence=0.4),
    )

    assert summary.universe_type == ScanUniverseType.WATCHLIST
    assert summary.total_symbols_in_universe == 1
    assert summary.evaluated_symbols == 1
    assert summary.matched_symbols >= 1
    assert summary.matches[0].symbol == "THYAO"
    assert summary.matches[0].instrument_name == "Türk Hava Yolları"
    assert summary.disclaimer != ""


def test_bist30_universe_symbols_exist() -> None:
    """BIST 30 evreninde beklenen temel hisselerin varlığı."""
    assert len(BIST_30_UNIVERSE) == 30
    assert "THYAO" in BIST_30_UNIVERSE
    assert "GARAN" in BIST_30_UNIVERSE
    assert "ASELS" in BIST_30_UNIVERSE
    assert "EREGL" in BIST_30_UNIVERSE
