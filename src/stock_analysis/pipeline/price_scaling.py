"""Multi-symbol price scaling, normalization, and corporate action integration module.

Solves the multi-symbol price discrepancy and corporate action distortions (FAZ-10):
- Different BIST symbols have vastly differing nominal prices (e.g., 5 TRY vs 1,500 TRY).
  Feeding unscaled prices directly into neural networks or regression models causes high-priced
  stocks to dominate losses and gradients.
- Corporate action adjustments: Forward/reverse splits, bonus issues, and cash dividends
  distort price series and trading volume if not properly adjusted before feature extraction.
- Volume adjustments: In stock splits, share count increases proportionally; volume must be
  adjusted alongside prices to prevent liquidity feature corruption.
- Look-ahead bias prevention: Scalers must strictly fit on the training partition only,
  then transform validation and test partitions without recalculating parameters.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.corporate_actions import (
    CorporateAction,
    CorporateActionType,
    apply_corporate_actions,
)
from stock_analysis.providers.base import MarketBarRecord


class ScalingMethod(StrEnum):
    """Method for price and feature normalization."""

    STANDARD = "standard"  # Z-score normalization: (x - mean) / std
    MINMAX = "minmax"  # Min-max scaling: (x - min) / (max - min) -> [feature_range]
    ROBUST = "robust"  # Robust median and IQR scaling: (x - median) / IQR
    LOG_RETURN = "log_return"  # Natural log return: ln(P_t / P_{t-1})
    PERCENT_RETURN = "percent_return"  # Percentage return: (P_t - P_{t-1}) / P_{t-1}


class SymbolScaleParams(BaseModel):
    """Fitted scaling parameters for an individual symbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    method: ScalingMethod
    center: float = Field(description="Center metric: mean, min, or median")
    scale: float = Field(description="Scale metric: std, range (max-min), or IQR")
    fit_sample_count: int
    fit_start_date: date | None = None
    fit_end_date: date | None = None
    extra_params: dict[str, float] = Field(default_factory=dict)


class ScaledBarFeature(BaseModel):
    """Normalized feature record representing a scaled market bar for machine learning."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trade_date: date
    open_scaled: float
    high_scaled: float
    low_scaled: float
    close_scaled: float
    volume_scaled: float
    raw_close: float
    adj_close: float
    adjustment_factor: float = 1.0


class MultiSymbolPriceScaler:
    """Scales and normalizes multi-symbol price and volume series for machine learning models.

    Ensures zero leakage across temporal splits by enforcing fit exclusively on training data.
    Provides inverse transformation capability to convert model predictions back
    to TRY price scales.
    """

    def __init__(
        self,
        method: ScalingMethod = ScalingMethod.STANDARD,
        *,
        feature_range: tuple[float, float] = (0.0, 1.0),
        clip_outliers: bool = True,
        clip_std_threshold: float = 5.0,
        adjust_volume_for_splits: bool = True,
    ) -> None:
        """Initialize MultiSymbolPriceScaler.

        Args:
            method: Normalization method (STANDARD, MINMAX, ROBUST, LOG_RETURN, PERCENT_RETURN).
            feature_range: (min, max) range for MINMAX scaling (default: (0.0, 1.0)).
            clip_outliers: Whether to clip extreme outliers in standardized/robust outputs.
            clip_std_threshold: Clipping threshold in units of scale/std (default: 5.0).
            adjust_volume_for_splits: Whether to adjust trading volume inversely to price splits.
        """
        self.method = method
        self.feature_range = feature_range
        self.clip_outliers = clip_outliers
        self.clip_std_threshold = clip_std_threshold
        self.adjust_volume_for_splits = adjust_volume_for_splits
        self._fitted_params: dict[str, SymbolScaleParams] = {}
        self._is_fitted = False

    @property
    def is_fitted(self) -> bool:
        """Return whether scaler parameters have been fitted."""
        return self._is_fitted

    @property
    def fitted_symbols(self) -> set[str]:
        """Return set of symbols that have been fitted."""
        return set(self._fitted_params.keys())

    def get_params(self, symbol: str) -> SymbolScaleParams:
        """Get fitted scale parameters for a specific symbol."""
        if symbol not in self._fitted_params:
            raise KeyError(f"Symbol '{symbol}' has not been fitted.")
        return self._fitted_params[symbol]

    def export_params(self) -> dict[str, Any]:
        """Export all fitted parameters to a serializable dictionary for model registry."""
        return {
            "method": self.method.value,
            "feature_range": list(self.feature_range),
            "clip_outliers": self.clip_outliers,
            "clip_std_threshold": self.clip_std_threshold,
            "adjust_volume_for_splits": self.adjust_volume_for_splits,
            "symbols": {sym: p.model_dump(mode="json") for sym, p in self._fitted_params.items()},
        }

    @classmethod
    def from_params(cls, data: dict[str, Any]) -> MultiSymbolPriceScaler:
        """Instantiate a fitted scaler from exported parameters."""
        fr = data.get("feature_range", [0.0, 1.0])
        scaler = cls(
            method=ScalingMethod(data["method"]),
            feature_range=(float(fr[0]), float(fr[1])),
            clip_outliers=data.get("clip_outliers", True),
            clip_std_threshold=data.get("clip_std_threshold", 5.0),
            adjust_volume_for_splits=data.get("adjust_volume_for_splits", True),
        )
        for sym, p_dict in data.get("symbols", {}).items():
            scaler._fitted_params[sym] = SymbolScaleParams.model_validate(p_dict)
        scaler._is_fitted = bool(scaler._fitted_params)
        return scaler

    def fit(
        self,
        bars: Sequence[MarketBarRecord],
        actions: Sequence[CorporateAction] | None = None,
    ) -> MultiSymbolPriceScaler:
        """Fit scaling parameters per symbol strictly on the training partition.

        Args:
            bars: Market bars from the training period.
            actions: Optional corporate actions to adjust prices before fitting.

        Returns:
            Self (fitted scaler).
        """
        if not bars:
            raise ValueError("Cannot fit scaler on empty bar collection.")

        # Group bars by symbol
        bars_by_symbol = self._group_and_adjust_bars(bars, actions)

        self._fitted_params.clear()

        for symbol, sym_bars in bars_by_symbol.items():
            if not sym_bars:
                continue

            sorted_bars = sorted(sym_bars, key=lambda b: b.trade_date)
            # Use adj_close if available and positive, else raw_close
            prices = [
                float(b.adj_close if b.adj_close > Decimal("0") else b.raw_close)
                for b in sorted_bars
            ]
            volumes = [float(b.volume) for b in sorted_bars]
            sample_count = len(prices)
            start_date = sorted_bars[0].trade_date
            end_date = sorted_bars[-1].trade_date

            params = self._compute_symbol_params(
                symbol=symbol,
                prices=prices,
                volumes=volumes,
                sample_count=sample_count,
                start_date=start_date,
                end_date=end_date,
            )
            self._fitted_params[symbol] = params

        self._is_fitted = True
        return self

    def transform(
        self,
        bars: Sequence[MarketBarRecord],
        actions: Sequence[CorporateAction] | None = None,
    ) -> list[ScaledBarFeature]:
        """Transform market bars into normalized features using previously fitted parameters.

        Args:
            bars: Market bars (e.g. from validation or test period).
            actions: Optional corporate actions to apply adjustments.

        Returns:
            List of ScaledBarFeature records.
        """
        if not self._is_fitted:
            raise RuntimeError("MultiSymbolPriceScaler must be fit before calling transform.")

        if not bars:
            return []

        bars_by_symbol = self._group_and_adjust_bars(bars, actions)
        results: list[ScaledBarFeature] = []

        for symbol, sym_bars in bars_by_symbol.items():
            if symbol not in self._fitted_params:
                raise ValueError(
                    f"Symbol '{symbol}' was not present during fit(). "
                    "Cannot scale unseen symbol without risking data leakage or missing baseline."
                )

            params = self._fitted_params[symbol]
            sorted_bars = sorted(sym_bars, key=lambda b: b.trade_date)

            # Transform each bar
            sym_results = self._transform_symbol_bars(symbol, sorted_bars, params)
            results.extend(sym_results)

        # Sort overall results chronologically then by symbol
        return sorted(results, key=lambda r: (r.trade_date, r.symbol))

    def fit_transform(
        self,
        bars: Sequence[MarketBarRecord],
        actions: Sequence[CorporateAction] | None = None,
    ) -> list[ScaledBarFeature]:
        """Fit on provided training bars and return scaled features."""
        self.fit(bars, actions)
        return self.transform(bars, actions)

    def inverse_transform_close(
        self,
        symbol: str,
        scaled_values: Sequence[float],
    ) -> list[float]:
        """Map scaled close feature values back to the original nominal price/return scale.

        Args:
            symbol: Ticker symbol.
            scaled_values: Scaled float values to invert.

        Returns:
            List of unscaled price or return values.
        """
        params = self.get_params(symbol)
        center = params.center
        scale = params.scale

        output: list[float] = []
        for val in scaled_values:
            match self.method:
                case ScalingMethod.STANDARD | ScalingMethod.ROBUST:
                    unscaled = (val * scale) + center
                case ScalingMethod.MINMAX:
                    min_val, max_val = self.feature_range
                    norm = (val - min_val) / (max_val - min_val) if max_val > min_val else 0.5
                    unscaled = (norm * scale) + center
                case ScalingMethod.LOG_RETURN | ScalingMethod.PERCENT_RETURN:
                    # In return mode, scale maps standard returns
                    unscaled = (val * scale) + center
                case _:
                    unscaled = val
            output.append(unscaled)

        return output

    def _group_and_adjust_bars(
        self,
        bars: Sequence[MarketBarRecord],
        actions: Sequence[CorporateAction] | None = None,
    ) -> dict[str, list[MarketBarRecord]]:
        """Group bars by symbol and apply corporate actions with volume adjustment."""
        raw_grouped: dict[str, list[MarketBarRecord]] = defaultdict(list)
        for b in bars:
            raw_grouped[b.symbol].append(b)

        adjusted_grouped: dict[str, list[MarketBarRecord]] = {}

        for sym, sym_bars in raw_grouped.items():
            sym_actions = [a for a in (actions or []) if a.symbol == sym]
            if sym_actions:
                # Apply price adjustments
                adj_bars = apply_corporate_actions(sym_bars, sym_actions)
                # Apply volume adjustment if enabled
                if self.adjust_volume_for_splits:
                    adj_bars = self._adjust_bar_volumes(adj_bars, sym_actions)
                adjusted_grouped[sym] = adj_bars
            else:
                adjusted_grouped[sym] = list(sym_bars)

        return adjusted_grouped

    def _adjust_bar_volumes(
        self,
        bars: Sequence[MarketBarRecord],
        actions: Sequence[CorporateAction],
    ) -> list[MarketBarRecord]:
        """Adjust trading volume inversely to price splits to maintain dollar-volume invariance.

        When a stock splits 2:1 (price halves), the share volume historically doubles.
        """
        sorted_bars = sorted(bars, key=lambda b: b.trade_date)
        sorted_actions = sorted(actions, key=lambda a: a.ex_date)

        updated_bars: list[MarketBarRecord] = []
        for b in sorted_bars:
            cum_factor = Decimal("1.0")
            for action in sorted_actions:
                if b.trade_date < action.ex_date:
                    if action.action_type in (
                        CorporateActionType.SPLIT,
                        CorporateActionType.REVERSE_SPLIT,
                    ):
                        # For split ratio 2: price factor is 0.5, volume multiplier is 2.0
                        cum_factor *= action.ratio
                    elif action.action_type == CorporateActionType.BONUS_ISSUE:
                        # Bonus issue increases share count by (1 + ratio)
                        cum_factor *= Decimal("1.0") + action.ratio

            if cum_factor != Decimal("1.0") and cum_factor > Decimal("0"):
                new_volume = int(Decimal(b.volume) * cum_factor)
                b = b.model_copy(update={"volume": new_volume})

            updated_bars.append(b)

        return updated_bars

    def _compute_symbol_params(
        self,
        symbol: str,
        prices: list[float],
        volumes: list[float],
        sample_count: int,
        start_date: date,
        end_date: date,
    ) -> SymbolScaleParams:
        """Compute scale parameters for a single symbol based on selected method."""
        extra_params: dict[str, float] = {}

        # Compute volume scaling reference (median and std of log volume)
        log_vols = [math.log(max(v, 1.0)) for v in volumes]
        extra_params["vol_mean"] = sum(log_vols) / len(log_vols) if log_vols else 0.0
        extra_params["vol_std"] = (
            math.sqrt(sum((x - extra_params["vol_mean"]) ** 2 for x in log_vols) / len(log_vols))
            if len(log_vols) > 1
            else 1.0
        )
        if extra_params["vol_std"] < 1e-6:
            extra_params["vol_std"] = 1.0

        match self.method:
            case ScalingMethod.STANDARD:
                mean = sum(prices) / sample_count
                variance = (
                    sum((p - mean) ** 2 for p in prices) / (sample_count - 1)
                    if sample_count > 1
                    else 0.0
                )
                std = math.sqrt(variance) if variance > 0.0 else 1.0
                if std < 1e-6:
                    std = 1.0  # Guard against zero-variance
                center = mean
                scale = std

            case ScalingMethod.MINMAX:
                min_p = min(prices)
                max_p = max(prices)
                diff = max_p - min_p
                center = min_p
                scale = diff if diff > 1e-6 else 1.0

            case ScalingMethod.ROBUST:
                sorted_p = sorted(prices)
                median = self._percentile(sorted_p, 0.50)
                q25 = self._percentile(sorted_p, 0.25)
                q75 = self._percentile(sorted_p, 0.75)
                iqr = q75 - q25
                center = median
                scale = iqr if iqr > 1e-6 else (sorted_p[-1] - sorted_p[0] or 1.0)

            case ScalingMethod.LOG_RETURN | ScalingMethod.PERCENT_RETURN:
                returns = self._compute_series_returns(prices, self.method)
                if returns:
                    ret_mean = sum(returns) / len(returns)
                    ret_var = (
                        sum((r - ret_mean) ** 2 for r in returns) / len(returns)
                        if len(returns) > 1
                        else 0.0
                    )
                    ret_std = math.sqrt(ret_var) if ret_var > 0.0 else 0.02
                    if ret_std < 1e-6:
                        ret_std = 0.02
                else:
                    ret_mean = 0.0
                    ret_std = 0.02
                center = ret_mean
                scale = ret_std
                extra_params["last_fitted_price"] = prices[-1]

            case _:
                center = 0.0
                scale = 1.0

        return SymbolScaleParams(
            symbol=symbol,
            method=self.method,
            center=center,
            scale=scale,
            fit_sample_count=sample_count,
            fit_start_date=start_date,
            fit_end_date=end_date,
            extra_params=extra_params,
        )

    def _transform_symbol_bars(
        self,
        symbol: str,
        bars: list[MarketBarRecord],
        params: SymbolScaleParams,
    ) -> list[ScaledBarFeature]:
        """Apply scaling transformation to bars of a single symbol."""
        features: list[ScaledBarFeature] = []

        vol_mean = params.extra_params.get("vol_mean", 0.0)
        vol_std = params.extra_params.get("vol_std", 1.0)

        for i, bar in enumerate(bars):
            # Prefer adj prices when available
            raw_c = float(bar.raw_close)
            adj_c = float(bar.adj_close if bar.adj_close > Decimal("0") else bar.raw_close)
            adj_o = float(bar.adj_open if bar.adj_open > Decimal("0") else bar.raw_open)
            adj_h = float(bar.adj_high if bar.adj_high > Decimal("0") else bar.raw_high)
            adj_l = float(bar.adj_low if bar.adj_low > Decimal("0") else bar.raw_low)

            factor = adj_c / raw_c if raw_c > 0.0 else 1.0

            # Transform price values
            if self.method in (ScalingMethod.LOG_RETURN, ScalingMethod.PERCENT_RETURN):
                if i == 0:
                    scaled_c = 0.0
                    scaled_o = 0.0
                    scaled_h = 0.0
                    scaled_l = 0.0
                else:
                    prev_c = float(
                        bars[i - 1].adj_close
                        if bars[i - 1].adj_close > Decimal("0")
                        else bars[i - 1].raw_close
                    )
                    scaled_c = self._scale_return(adj_c, prev_c, params)
                    scaled_o = self._scale_return(adj_o, prev_c, params)
                    scaled_h = self._scale_return(adj_h, prev_c, params)
                    scaled_l = self._scale_return(adj_l, prev_c, params)
            else:
                scaled_o = self._scale_value(adj_o, params)
                scaled_h = self._scale_value(adj_h, params)
                scaled_l = self._scale_value(adj_l, params)
                scaled_c = self._scale_value(adj_c, params)

            # Volume scaling (standardized log-volume)
            log_vol = math.log(max(float(bar.volume), 1.0))
            vol_scaled = (log_vol - vol_mean) / vol_std
            if self.clip_outliers:
                vol_scaled = max(-self.clip_std_threshold, min(self.clip_std_threshold, vol_scaled))

            features.append(
                ScaledBarFeature(
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    open_scaled=round(scaled_o, 6),
                    high_scaled=round(scaled_h, 6),
                    low_scaled=round(scaled_l, 6),
                    close_scaled=round(scaled_c, 6),
                    volume_scaled=round(vol_scaled, 6),
                    raw_close=round(raw_c, 4),
                    adj_close=round(adj_c, 4),
                    adjustment_factor=round(factor, 6),
                )
            )

        return features

    def _scale_value(self, value: float, params: SymbolScaleParams) -> float:
        """Scale a single price value using fitted parameters."""
        match self.method:
            case ScalingMethod.STANDARD | ScalingMethod.ROBUST:
                res = (value - params.center) / params.scale
                if self.clip_outliers:
                    res = max(-self.clip_std_threshold, min(self.clip_std_threshold, res))
                return res

            case ScalingMethod.MINMAX:
                min_range, max_range = self.feature_range
                norm = (value - params.center) / params.scale
                scaled = (norm * (max_range - min_range)) + min_range
                return scaled

            case _:
                return value

    def _scale_return(self, curr_p: float, prev_p: float, params: SymbolScaleParams) -> float:
        """Compute standardized return relative to previous bar."""
        if prev_p <= 0.0 or curr_p <= 0.0:
            return 0.0

        if self.method == ScalingMethod.LOG_RETURN:
            ret = math.log(curr_p / prev_p)
        else:
            ret = (curr_p - prev_p) / prev_p

        # Standardize return using fitted mean and std of returns
        std_ret = (ret - params.center) / params.scale
        if self.clip_outliers:
            std_ret = max(-self.clip_std_threshold, min(self.clip_std_threshold, std_ret))
        return std_ret

    @staticmethod
    def _compute_series_returns(prices: list[float], method: ScalingMethod) -> list[float]:
        """Compute sequential returns for a price series."""
        returns: list[float] = []
        for i in range(1, len(prices)):
            p_prev = prices[i - 1]
            p_curr = prices[i]
            if p_prev > 0.0 and p_curr > 0.0:
                if method == ScalingMethod.LOG_RETURN:
                    returns.append(math.log(p_curr / p_prev))
                else:
                    returns.append((p_curr - p_prev) / p_prev)
        return returns

    @staticmethod
    def _percentile(sorted_data: list[float], p: float) -> float:
        """Calculate the p-th percentile from a sorted list of floats."""
        if not sorted_data:
            return 0.0
        n = len(sorted_data)
        if n == 1:
            return sorted_data[0]
        pos = (n - 1) * p
        lower = int(pos)
        upper = lower + 1
        weight = pos - lower
        if upper >= n:
            return sorted_data[-1]
        return (1.0 - weight) * sorted_data[lower] + weight * sorted_data[upper]


# Alias for roadmap order compatibility
PriceScaleManager = MultiSymbolPriceScaler

