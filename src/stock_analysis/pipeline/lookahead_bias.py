"""Look-ahead bias and future data leakage detection module.

Enforces strict information causality in financial feature engineering (FAZ-10):
- Prevents features from accessing data timestamps t' > t (information from the future).
- Prohibits centered rolling windows (which look both backward and forward).
- Detects target leakage (features identical to, or derived with future information
  from, prediction targets).
- Audits normalizers and scalers to ensure preprocessing parameters are fitted
  strictly on training sets, preventing global distribution leakage into validation/test folds.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.time_series_splitter import DataLeakageError, _extract_record_date

__all__ = [
    "DataLeakageError",
    "LeakageViolation",
    "LeakageViolationType",
    "LookaheadAuditResult",
    "LookaheadBiasDetector",
]


class LeakageViolationType(StrEnum):
    """Specific categories of look-ahead data leakage."""

    FUTURE_TIMESTAMP = "future_timestamp"
    CENTERED_WINDOW = "centered_window"
    FORWARD_SHIFT = "forward_shift"
    TARGET_LEAKAGE = "target_leakage"
    GLOBAL_PREPROCESSING_LEAKAGE = "global_preprocessing_leakage"


class LeakageViolation(BaseModel):
    """Structured record of a single look-ahead leakage violation."""

    model_config = ConfigDict(frozen=True)

    violation_type: LeakageViolationType
    feature_name: str
    details: str
    timestamp_or_index: str | None = None


class LookaheadAuditResult(BaseModel):
    """Comprehensive report detailing anti-leakage audit results."""

    model_config = ConfigDict(frozen=True)

    is_leak_free: bool
    violations: list[LeakageViolation] = Field(default_factory=list)
    features_audited: list[str] = Field(default_factory=list)
    total_records_checked: int = 0
    summary: str


class LookaheadBiasDetector:
    """Rigorous look-ahead bias detector for financial datasets and ML pipelines."""

    @staticmethod
    def verify_window_configuration(
        window_size: int,
        *,
        is_centered: bool = False,
        forward_shift: int = 0,
    ) -> None:
        """Verify that rolling window calculations do not look into the future.

        Raises:
            DataLeakageError: If centered window is requested or forward shift > 0.
        """
        if window_size < 1:
            raise ValueError(f"Window size must be at least 1, got {window_size}")

        if is_centered:
            raise DataLeakageError(
                "Centered rolling windows (center=True) incorporate future observations "
                "into the current timestamp, causing fatal look-ahead bias."
            )

        if forward_shift > 0:
            raise DataLeakageError(
                f"Negative/forward shift (shift=-{forward_shift}) incorporates "
                f"future t+{forward_shift} data into feature inputs. Input features "
                "must only use past data (shift >= 0)."
            )

    @classmethod
    def check_feature_timestamp_alignment(
        cls,
        decision_timestamp: date | datetime,
        feature_timestamps: dict[str, date | datetime],
    ) -> list[LeakageViolation]:
        """Verify that no feature timestamp occurred after the decision point t.

        In market trading, a model predicting at close of day t cannot consume
        data published at day t+1 or intraday after market close.
        """
        violations: list[LeakageViolation] = []
        decision_dt = (
            datetime.combine(decision_timestamp, datetime.min.time())
            if isinstance(decision_timestamp, date) and not isinstance(decision_timestamp, datetime)
            else decision_timestamp
        )

        for feat_name, feat_ts in feature_timestamps.items():
            f_dt = (
                datetime.combine(feat_ts, datetime.min.time())
                if isinstance(feat_ts, date) and not isinstance(feat_ts, datetime)
                else feat_ts
            )
            if f_dt > decision_dt:
                violations.append(
                    LeakageViolation(
                        violation_type=LeakageViolationType.FUTURE_TIMESTAMP,
                        feature_name=feat_name,
                        details=(
                            f"Feature '{feat_name}' has timestamp {feat_ts} which is after "
                            f"decision timestamp {decision_timestamp}."
                        ),
                        timestamp_or_index=str(decision_timestamp),
                    )
                )
        return violations

    @classmethod
    def check_target_leakage(
        cls,
        records: Sequence[dict[str, Any]],
        *,
        target_name: str,
        feature_names: Sequence[str],
        correlation_threshold: float = 0.999,
    ) -> list[LeakageViolation]:
        """Detect features that inadvertently contain future target labels or perfect copies.

        Computes Pearson correlation between features and the target. A near 1.0 or -1.0
        correlation often signals that the target column was inadvertently included
        as a feature or unshifted return was used.
        """
        violations: list[LeakageViolation] = []
        if len(records) < 3:
            return violations

        target_floats: list[float] = []
        for r in records:
            v = r.get(target_name)
            if v is None:
                return violations  # Incomplete target series
            target_floats.append(float(v))

        t_mean = sum(target_floats) / len(target_floats)
        t_var = sum((x - t_mean) ** 2 for x in target_floats)
        if t_var <= 1e-12:
            return violations  # Constant target, no variance

        for feat in feature_names:
            if feat == target_name:
                violations.append(
                    LeakageViolation(
                        violation_type=LeakageViolationType.TARGET_LEAKAGE,
                        feature_name=feat,
                        details=(
                            f"Target column '{target_name}' was included directly in "
                            "the input feature set."
                        ),
                    )
                )
                continue

            f_floats: list[float] = []
            for r in records:
                fv = r.get(feat)
                if fv is None:
                    break
                f_floats.append(float(fv))

            if len(f_floats) != len(records):
                continue

            f_mean = sum(f_floats) / len(f_floats)
            f_var = sum((x - f_mean) ** 2 for x in f_floats)
            if f_var <= 1e-12:
                continue

            cov = sum(
                (f - f_mean) * (t - t_mean) for f, t in zip(f_floats, target_floats, strict=True)
            )
            corr = cov / math.sqrt(f_var * t_var)

            if abs(corr) >= correlation_threshold:
                violations.append(
                    LeakageViolation(
                        violation_type=LeakageViolationType.TARGET_LEAKAGE,
                        feature_name=feat,
                        details=(
                            f"Feature '{feat}' has suspicious absolute correlation of "
                            f"{abs(corr):.4f} with target '{target_name}', exceeding safe "
                            f"threshold {correlation_threshold}. "
                            "Probable future target leakage into feature matrix."
                        ),
                    )
                )

        return violations

    @classmethod
    def verify_preprocessing_isolation(
        cls,
        train_records: Sequence[Any],
        eval_records: Sequence[Any],
        *,
        fitted_scaler_params: dict[str, dict[str, float]],
        tolerance: float = 1e-5,
    ) -> list[LeakageViolation]:
        """Verify normalizer/scaler parameters were derived strictly from train partition.

        Checks if fitted scaler mean/std/min/max diverge from train-only empirical values,
        indicating that eval (validation/test) data was leaked during preprocessing fitting.
        """
        violations: list[LeakageViolation] = []
        if not train_records:
            raise ValueError("Train records cannot be empty for scaler audit.")

        for feat_name, params in fitted_scaler_params.items():
            # Calculate train-only values
            train_vals: list[float] = []
            for r in train_records:
                val = r.get(feat_name) if isinstance(r, dict) else getattr(r, feat_name, None)
                if val is not None:
                    train_vals.append(float(val))

            if not train_vals:
                continue

            train_mean = sum(train_vals) / len(train_vals)

            # Calculate pooled (train + eval) values to test if scaler used full data
            eval_vals: list[float] = []
            for r in eval_records:
                val = r.get(feat_name) if isinstance(r, dict) else getattr(r, feat_name, None)
                if val is not None:
                    eval_vals.append(float(val))

            pooled_vals = train_vals + eval_vals
            pooled_mean = sum(pooled_vals) / len(pooled_vals) if pooled_vals else train_mean

            fitted_mean = params.get("mean")
            if fitted_mean is not None:
                # If fitted mean is significantly closer to pooled mean than train mean
                divergence_from_train = abs(fitted_mean - train_mean)
                divergence_from_pooled = abs(fitted_mean - pooled_mean)

                if (
                    divergence_from_train > tolerance
                    and divergence_from_pooled < divergence_from_train
                    and abs(train_mean - pooled_mean) > tolerance
                ):
                    violations.append(
                        LeakageViolation(
                            violation_type=LeakageViolationType.GLOBAL_PREPROCESSING_LEAKAGE,
                            feature_name=feat_name,
                            details=(
                                f"Feature '{feat_name}' scaler mean ({fitted_mean:.4f}) matches "
                                f"pooled dataset ({pooled_mean:.4f}) instead of strictly train "
                                f"set ({train_mean:.4f}). Validation/test distributions leaked."
                            ),
                        )
                    )

        return violations

    @classmethod
    def audit_chronological_feature_matrix(
        cls,
        records: Sequence[dict[str, Any]],
        *,
        feature_names: Sequence[str],
        target_name: str | None = None,
        date_key: str = "date",
    ) -> LookaheadAuditResult:
        """Execute full look-ahead bias audit over a feature matrix."""
        violations: list[LeakageViolation] = []

        if not records:
            return LookaheadAuditResult(
                is_leak_free=True,
                violations=[],
                features_audited=list(feature_names),
                total_records_checked=0,
                summary="Empty dataset; no look-ahead violations detected.",
            )

        # 1. Check chronological sorting
        try:
            prev_dt = _extract_record_date(records[0])
            for idx, r in enumerate(records[1:], start=1):
                cur_dt = _extract_record_date(r)
                if cur_dt < prev_dt:
                    violations.append(
                        LeakageViolation(
                            violation_type=LeakageViolationType.FUTURE_TIMESTAMP,
                            feature_name=date_key,
                            details=(
                                f"Record at index {idx} has date {cur_dt} < prior date {prev_dt}. "
                                "Non-chronological order detected."
                            ),
                            timestamp_or_index=str(cur_dt),
                        )
                    )
                    break
                prev_dt = cur_dt
        except Exception as e:
            violations.append(
                LeakageViolation(
                    violation_type=LeakageViolationType.FUTURE_TIMESTAMP,
                    feature_name=date_key,
                    details=f"Failed to verify record dates: {e}",
                )
            )

        # 2. Check target leakage if target specified
        if target_name:
            target_viols = cls.check_target_leakage(
                records,
                target_name=target_name,
                feature_names=feature_names,
            )
            violations.extend(target_viols)

        is_leak_free = len(violations) == 0
        summary = (
            f"Audit passed: 0 look-ahead violations detected across {len(records)} records and "
            f"{len(feature_names)} features."
            if is_leak_free
            else f"Audit FAILED: {len(violations)} look-ahead leakage violation(s) detected!"
        )

        return LookaheadAuditResult(
            is_leak_free=is_leak_free,
            violations=violations,
            features_audited=list(feature_names),
            total_records_checked=len(records),
            summary=summary,
        )
