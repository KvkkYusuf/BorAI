"""Technical analytics and rule-based screener endpoints.

Strictly non-advisory, neutral, and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from stock_analysis.api.cache import get_cache_backend
from stock_analysis.api.rate_limiter import analytics_rate_limiter
from stock_analysis.api.schemas import (
    ExplanationResponse,
    IndicatorMacd,
    IndicatorsData,
    IndicatorsResponse,
    ScreenerMatch,
    ScreenerResponse,
)
from stock_analysis.models.llm_explainer import SafeLLMExplainer

router = APIRouter(
    prefix="/analytics",
    tags=["Analytics & Indicators"],
    dependencies=[Depends(analytics_rate_limiter)],
)

_explainer = SafeLLMExplainer()


def get_llm_explainer() -> SafeLLMExplainer:
    return _explainer


# Deterministic reference catalog for screener demonstration
SCREENER_UNIVERSE = [
    ScreenerMatch(symbol="THYAO", close=310.25, rsi_14=58.4, sma_20=302.10, sma_50=295.40),
    ScreenerMatch(symbol="ASELS", close=62.50, rsi_14=42.1, sma_20=61.80, sma_50=60.20),
    ScreenerMatch(symbol="GARAN", close=118.40, rsi_14=64.8, sma_20=114.20, sma_50=110.00),
    ScreenerMatch(symbol="EKGYO", close=9.42, rsi_14=31.2, sma_20=9.80, sma_50=10.15),
    ScreenerMatch(symbol="EREGL", close=48.60, rsi_14=34.5, sma_20=49.10, sma_50=51.00),
]


@router.get(
    "/indicators/{symbol}",
    response_model=IndicatorsResponse,
    summary="Teknik gostergeler ve tarafsiz ozet",
    description="Hisseye ait RSI, SMA ve MACD gostergelerini tarafsiz aciklamayla sunar.",
)
def get_indicators(
    symbol: str,
    indicators: str = Query(
        default="rsi,sma20,sma50,macd",
        description="Istenen gosterge kodlari (virgulle ayrilmis)",
    ),
) -> IndicatorsResponse:
    """Return deterministic technical indicator metrics with strictly neutral commentary."""
    cleaned_symbol = symbol.strip().upper()
    if not cleaned_symbol:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SYMBOL", "message": "Sembol bos olamaz."},
        )

    cache = get_cache_backend()
    cache_key = f"analytics:indicators:{cleaned_symbol}:{indicators}"
    cached_payload = cache.get_sync(cache_key)
    if cached_payload is not None:
        return IndicatorsResponse.model_validate_json(cached_payload)

    # Deterministic indicators payload (fact-based, strictly non-advisory)
    requested = {i.strip().lower() for i in indicators.split(",")}

    data = IndicatorsData(
        rsi_14=58.4 if "rsi" in requested or "rsi14" in requested else None,
        sma_20=302.10 if "sma20" in requested or "sma_20" in requested else None,
        sma_50=295.40 if "sma50" in requested or "sma_50" in requested else None,
        macd=(
            IndicatorMacd(line=2.45, signal=1.90, histogram=0.55) if "macd" in requested else None
        ),
    )

    neutral_summary = (
        f"{cleaned_symbol} kapanis fiyati 20 ve 50 periyotluk hareketli ortalamalarin "
        "uzerindedir. RSI degeri 58.4 seviyesindedir."
    )

    response = IndicatorsResponse(
        symbol=cleaned_symbol,
        calculated_at=datetime.now(UTC),
        indicators=data,
        neutral_summary=neutral_summary,
    )
    cache.set_sync(cache_key, response.model_dump_json(), expire_seconds=60)
    return response


@router.get(
    "/screener",
    response_model=ScreenerResponse,
    summary="Kural bazli teknik filtreleme (Screener)",
    description="Kullanicinin belirledigi nesnel matematiksel kriterlere gore hisseleri listeler.",
)
def run_screener(
    rsi_max: float | None = Query(default=None, description="Maksimum RSI esik degeri"),
    sma_cross: str | None = Query(
        default=None,
        description="Ortalama karsilastirmasi ('above_sma50', 'below_sma50')",
    ),
    page: int = Query(default=1, ge=1, description="Sayfa numarasi (min: 1)"),
    page_size: int = Query(default=50, ge=1, le=100, description="Sayfa basina kayit (max: 100)"),
) -> ScreenerResponse:
    """Filter symbols according to objective technical rules with mandatory pagination."""
    cache = get_cache_backend()
    cache_key = f"analytics:screener:{rsi_max}:{sma_cross}:{page}:{page_size}"
    cached_payload = cache.get_sync(cache_key)
    if cached_payload is not None:
        return ScreenerResponse.model_validate_json(cached_payload)

    filters_applied: dict[str, str | float] = {}
    matches = SCREENER_UNIVERSE

    if rsi_max is not None:
        filters_applied["rsi_max"] = rsi_max
        matches = [m for m in matches if m.rsi_14 is not None and m.rsi_14 <= rsi_max]

    if sma_cross == "above_sma50":
        filters_applied["sma_cross"] = "above_sma50"
        matches = [m for m in matches if m.sma_50 is not None and m.close > m.sma_50]
    elif sma_cross == "below_sma50":
        filters_applied["sma_cross"] = "below_sma50"
        matches = [m for m in matches if m.sma_50 is not None and m.close < m.sma_50]

    total = len(matches)
    start = (page - 1) * page_size
    paginated_matches = matches[start : start + page_size]

    response = ScreenerResponse(
        filter_applied=filters_applied,
        matches=paginated_matches,
        total=total,
        page=page,
        page_size=page_size,
    )
    cache.set_sync(cache_key, response.model_dump_json(), expire_seconds=60)
    return response


@router.get(
    "/explain/{symbol}",
    response_model=ExplanationResponse,
    summary="Gosterge aciklama katmani (deterministik / istege bagli LLM)",
    description=(
        "Teknik gostergeleri tarafsiz sekilde aciklar. LLM_ENABLED=false iken kural tabanli, "
        "acikken guvenlik filtreli LLM ile calisir. Asla tavsiye veya hedef fiyat vermez."
    ),
)
def get_symbol_explanation(
    symbol: str,
    context: str | None = Query(default=None, description="Istege bagli harici baglam metni"),
    explainer: SafeLLMExplainer = Depends(get_llm_explainer),
) -> ExplanationResponse:
    """Return safe neutral explanation of technical indicators."""
    cleaned_symbol = symbol.strip().upper()
    if not cleaned_symbol:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SYMBOL", "message": "Sembol bos olamaz."},
        )

    # Use deterministic reference indicators or calculate
    indicators = {
        "rsi_14": 58.4,
        "sma_20": 302.10,
        "sma_50": 295.40,
        "macd": {"line": 2.45, "signal": 1.90, "histogram": 0.55},
    }

    schema_result, meta = explainer.explain(
        symbol=cleaned_symbol,
        indicators=indicators,
        forecast=None,
        external_context=context,
    )

    return ExplanationResponse(
        symbol=schema_result.symbol,
        context_summary=schema_result.context_summary,
        technical_observations=schema_result.technical_observations,
        risk_notes=schema_result.risk_notes,
        disclaimer=schema_result.disclaimer,
        llm_enabled=meta["llm_enabled"],
        llm_used=meta["llm_used"],
        cached=meta["cached"],
        fallback_used=meta["fallback_used"],
        reason=meta["reason"],
    )
