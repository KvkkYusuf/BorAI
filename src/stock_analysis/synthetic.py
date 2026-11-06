"""Synthetic BIST EOD market data generator for local development and tests."""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

BIST_SAMPLE_SYMBOLS: list[str] = [
    "THYAO",
    "ASELS",
    "GARAN",
    "KCHOL",
    "EREGL",
    "BIMAS",
]


class SyntheticDailyBar(BaseModel):
    """Pydantic model representing a single synthetic EOD daily bar."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1, max_length=10)
    trade_date: date
    raw_open: Decimal = Field(gt=Decimal("0"))
    raw_high: Decimal = Field(gt=Decimal("0"))
    raw_low: Decimal = Field(gt=Decimal("0"))
    raw_close: Decimal = Field(gt=Decimal("0"))
    adj_open: Decimal = Field(gt=Decimal("0"))
    adj_high: Decimal = Field(gt=Decimal("0"))
    adj_low: Decimal = Field(gt=Decimal("0"))
    adj_close: Decimal = Field(gt=Decimal("0"))
    volume: int = Field(ge=0)
    turnover_try: Decimal = Field(ge=Decimal("0"))
    split_factor: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    dividend_try: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"))
    source_name: str = Field(default="synthetic_generator")
    is_verified: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_ohlc_consistency(self) -> "SyntheticDailyBar":
        """Verify standard OHLC logical relationships."""
        if self.raw_high < self.raw_low:
            raise ValueError(
                f"raw_high ({self.raw_high}) cannot be less than raw_low ({self.raw_low})"
            )
        if self.raw_high < self.raw_open or self.raw_high < self.raw_close:
            raise ValueError("raw_high must be greater than or equal to raw_open and raw_close")
        if self.raw_low > self.raw_open or self.raw_low > self.raw_close:
            raise ValueError("raw_low must be less than or equal to raw_open and raw_close")

        if self.adj_high < self.adj_low:
            raise ValueError(
                f"adj_high ({self.adj_high}) cannot be less than adj_low ({self.adj_low})"
            )
        if self.adj_high < self.adj_open or self.adj_high < self.adj_close:
            raise ValueError("adj_high must be greater than or equal to adj_open and adj_close")
        if self.adj_low > self.adj_open or self.adj_low > self.adj_close:
            raise ValueError("adj_low must be less than or equal to adj_open and adj_close")

        return self


def generate_synthetic_ohlcv(
    symbol: str,
    start_date: date,
    end_date: date,
    initial_price: float = 100.0,
    seed: int = 42,
    drift: float = 0.0002,
    volatility: float = 0.02,
) -> list[SyntheticDailyBar]:
    """Generate deterministic synthetic daily bars for a given symbol and date range.

    Skips weekends (Saturday, Sunday) to simulate realistic trading days.
    """
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) cannot be after end_date ({end_date})")

    rng = np.random.default_rng(seed)
    current_date = start_date
    current_price = initial_price

    bars: list[SyntheticDailyBar] = []

    while current_date <= end_date:
        # 0 = Monday, ..., 4 = Friday, 5 = Saturday, 6 = Sunday
        if current_date.weekday() < 5:
            daily_return = float(rng.normal(drift, volatility))
            # Open price close to previous close
            open_drift = float(rng.normal(0, volatility * 0.3))
            open_price = max(0.5, current_price * (1.0 + open_drift))
            close_price = max(0.5, current_price * (1.0 + daily_return))

            # Realistic high and low bounds
            upper_bound = max(open_price, close_price)
            lower_bound = min(open_price, close_price)

            high_wiggle = abs(float(rng.normal(0, volatility * 0.6)))
            low_wiggle = abs(float(rng.normal(0, volatility * 0.6)))

            high_price = upper_bound * (1.0 + high_wiggle)
            low_price = max(0.1, lower_bound * (1.0 - low_wiggle))

            # Simulate volume and turnover
            volume = int(rng.integers(10_000, 5_000_000))
            avg_price = (open_price + high_price + low_price + close_price) / 4.0
            turnover = float(volume * avg_price)

            # Round prices to 4 decimal places for TRY precision
            raw_open = Decimal(f"{open_price:.4f}")
            raw_high = Decimal(f"{high_price:.4f}")
            raw_low = Decimal(f"{low_price:.4f}")
            raw_close = Decimal(f"{close_price:.4f}")

            # For base synthetic generator, adjusted prices match raw
            adj_open = raw_open
            adj_high = raw_high
            adj_low = raw_low
            adj_close = raw_close

            bar = SyntheticDailyBar(
                symbol=symbol.upper(),
                trade_date=current_date,
                raw_open=raw_open,
                raw_high=raw_high,
                raw_low=raw_low,
                raw_close=raw_close,
                adj_open=adj_open,
                adj_high=adj_high,
                adj_low=adj_low,
                adj_close=adj_close,
                volume=volume,
                turnover_try=Decimal(f"{turnover:.2f}"),
                split_factor=Decimal("1.000000"),
                dividend_try=Decimal("0.0000"),
                source_name="synthetic_generator",
                is_verified=True,
            )
            bars.append(bar)
            current_price = close_price

        current_date += timedelta(days=1)

    return bars


def generate_synthetic_dataframe(
    symbol: str,
    start_date: date,
    end_date: date,
    initial_price: float = 100.0,
    seed: int = 42,
    drift: float = 0.0002,
    volatility: float = 0.02,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data directly as a pandas DataFrame."""
    bars = generate_synthetic_ohlcv(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        initial_price=initial_price,
        seed=seed,
        drift=drift,
        volatility=volatility,
    )
    data = [
        {
            "symbol": b.symbol,
            "trade_date": b.trade_date,
            "raw_open": float(b.raw_open),
            "raw_high": float(b.raw_high),
            "raw_low": float(b.raw_low),
            "raw_close": float(b.raw_close),
            "adj_open": float(b.adj_open),
            "adj_high": float(b.adj_high),
            "adj_low": float(b.adj_low),
            "adj_close": float(b.adj_close),
            "volume": b.volume,
            "turnover_try": float(b.turnover_try),
            "split_factor": float(b.split_factor),
            "dividend_try": float(b.dividend_try),
            "source_name": b.source_name,
            "is_verified": b.is_verified,
        }
        for b in bars
    ]
    df = pd.DataFrame(data)
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df.set_index("trade_date", inplace=False)
    return df
