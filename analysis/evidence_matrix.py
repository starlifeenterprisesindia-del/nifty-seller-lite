from __future__ import annotations

from math import floor
from typing import Any

from analysis.technical_utils import clamp
from models import MarketSnapshot, TimeframeIndicators


def _normalise(
    bullish: float, bearish: float, neutral: float
) -> tuple[float, float, float]:
    values = [
        max(0.0, float(bullish)),
        max(0.0, float(bearish)),
        max(0.0, float(neutral)),
    ]
    total = sum(values)
    if total <= 0:
        return 0.0, 0.0, 0.0

    # Largest-remainder allocation in tenths guarantees that every directional
    # row displays exactly 100.0 after rounding.
    scaled = [value / total * 1000.0 for value in values]
    tenths = [floor(value) for value in scaled]
    remainder = 1000 - sum(tenths)
    order = sorted(
        range(len(scaled)),
        key=lambda index: scaled[index] - tenths[index],
        reverse=True,
    )
    for index in order[:remainder]:
        tenths[index] += 1
    return tuple(value / 10.0 for value in tenths)  # type: ignore[return-value]


def _dominant_label(bullish: float, bearish: float, neutral: float) -> str:
    leader = max(
        (("BULLISH", bullish), ("BEARISH", bearish), ("NEUTRAL / RANGE", neutral)),
        key=lambda item: item[1],
    )
    return leader[0]


def _weighted_scores(
    rows: list[tuple[float, float, float, float]],
) -> tuple[float, float, float]:
    usable = [item for item in rows if item[3] > 0]
    if not usable:
        return 0.0, 0.0, 0.0
    weight_total = sum(item[3] for item in usable)
    bullish = sum(item[0] * item[3] for item in usable) / weight_total
    bearish = sum(item[1] * item[3] for item in usable) / weight_total
    neutral = sum(item[2] * item[3] for item in usable) / weight_total
    return _normalise(bullish, bearish, neutral)


def _indicator_vote(item: TimeframeIndicators) -> tuple[float, float, float]:
    if item.status != "READY":
        return 0.0, 0.0, 0.0

    bullish = bearish = neutral = 0.0
    ema = item.ema_state.upper()
    if ema == "BULLISH ALIGNED":
        bullish += 1.0
    elif "BULLISH STRUCTURE" in ema:
        bullish += 0.65
        neutral += 0.35
    elif ema == "BEARISH ALIGNED":
        bearish += 1.0
    elif "BEARISH STRUCTURE" in ema:
        bearish += 0.65
        neutral += 0.35
    else:
        neutral += 1.0

    macd = item.macd_state.upper()
    if macd == "BULLISH":
        bullish += 1.0
    elif "BULLISH" in macd:
        bullish += 0.65
        neutral += 0.35
    elif macd == "BEARISH":
        bearish += 1.0
    elif "BEARISH" in macd:
        bearish += 0.65
        neutral += 0.35
    else:
        neutral += 1.0

    rsi = item.rsi_state.upper()
    if "BULLISH" in rsi and "OVEREXTENDED" in rsi:
        bullish += 0.55
        neutral += 0.45
    elif "BULLISH" in rsi:
        bullish += 0.80
        neutral += 0.20
    elif "BEARISH" in rsi and "OVERSOLD" in rsi:
        bearish += 0.55
        neutral += 0.45
    elif "BEARISH" in rsi:
        bearish += 0.80
        neutral += 0.20
    else:
        neutral += 1.0
    return _normalise(bullish, bearish, neutral)


def _level_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    levels = snapshot.levels
    if levels.status != "READY":
        return 0.0, 0.0, 100.0

    bullish, bearish, neutral = 25.0, 25.0, 50.0
    support = levels.immediate_support
    resistance = levels.immediate_resistance

    if support and support.status == "BROKEN":
        bearish += 45.0
        neutral -= 25.0
    elif support and any(token in support.status for token in ("HOLDING", "REJECTED")):
        bullish += 35.0
        neutral -= 20.0

    if resistance and resistance.status == "BROKEN":
        bullish += 45.0
        neutral -= 25.0
    elif resistance and "REJECTED" in resistance.status:
        bearish += 35.0
        neutral -= 20.0

    if levels.current_position == "NEAR SUPPORT":
        bullish += 18.0
        neutral -= 8.0
    elif levels.current_position == "NEAR RESISTANCE":
        bearish += 18.0
        neutral -= 8.0
    elif levels.upside_room is not None and levels.downside_room is not None:
        gap = levels.upside_room - levels.downside_room
        if gap >= 12:
            bullish += 10.0
            neutral -= 5.0
        elif gap <= -12:
            bearish += 10.0
            neutral -= 5.0

    return _normalise(bullish, bearish, max(0.0, neutral))


def _volume_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    state = snapshot.volume.overall_view.upper()
    if "BULLISH" in state:
        return 70.0, 10.0, 20.0
    if "BEARISH" in state:
        return 10.0, 70.0, 20.0
    if "UNAVAILABLE" in state:
        return 0.0, 0.0, 100.0
    if "WEAK" in state:
        return 20.0, 20.0, 60.0
    return 25.0, 25.0, 50.0


def _heavyweight_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    state = snapshot.heavyweights.state.upper()
    if "BROAD BULLISH" in state:
        return 80.0, 8.0, 12.0
    if "NARROW BULLISH" in state:
        return 60.0, 18.0, 22.0
    if "BROAD BEARISH" in state:
        return 8.0, 80.0, 12.0
    if "NARROW BEARISH" in state:
        return 18.0, 60.0, 22.0
    if snapshot.heavyweights.status == "UNAVAILABLE":
        return 0.0, 0.0, 100.0
    return 25.0, 25.0, 50.0


def _institutional_scores(snapshot: MarketSnapshot) -> tuple[float, float, float]:
    context = snapshot.institutional_context
    state = context.state.upper()
    if context.status == "MISSING":
        return 0.0, 0.0, 100.0
    if "SUPPORT" in state or "FII BUYING" in state:
        return 62.0, 18.0, 20.0
    if "PRESSURE" in state or "FII SELLING" in state:
        return 18.0, 62.0, 20.0
    return 25.0, 25.0, 50.0


def _feed_confidence(snapshot: MarketSnapshot) -> float:
    statuses = list(snapshot.feed_status.values())
    if not statuses:
        return 0.0
    usable = sum(status.ok for status in statuses)
    live = sum(status.use_state == "LIVE" for status in statuses)
    base = usable / len(statuses) * 70.0 + live / len(statuses) * 30.0
    if not snapshot.market_session.is_live:
        base = min(base, 55.0)
    return round(clamp(base, 0.0, 100.0), 1)


def _row(
    module: str,
    bullish: float | None,
    bearish: float | None,
    neutral: float | None,
    confidence: float,
    result: str,
) -> dict[str, Any]:
    return {
        "Module": module,
        "Bullish %": bullish,
        "Bearish %": bearish,
        "Neutral %": neutral,
        "Confidence %": round(clamp(confidence, 0.0, 100.0), 1),
        "Result": result,
    }


def build_compact_evidence_matrix(snapshot: MarketSnapshot) -> list[dict[str, Any]]:
    """Build a six-row display matrix without influencing the final decision brain."""

    pa3 = snapshot.price_action.three_minute
    pa15 = snapshot.price_action.fifteen_minute
    price_bull, price_bear, price_neutral = _weighted_scores(
        [
            (
                pa3.bullish_score,
                pa3.bearish_score,
                pa3.range_score,
                0.40 if pa3.status == "READY" else 0.0,
            ),
            (
                pa15.bullish_score,
                pa15.bearish_score,
                pa15.range_score,
                0.60 if pa15.status == "READY" else 0.0,
            ),
        ]
    )
    price_result = (
        f"{_dominant_label(price_bull, price_bear, price_neutral)} | "
        f"3m {pa3.structure}; 15m {pa15.structure}"
    )

    options = snapshot.option_intelligence
    option_bull, option_bear, option_neutral = _normalise(
        options.bullish_score, options.bearish_score, options.range_score
    )
    windows_ready = sum(item.status == "READY" for item in options.windows)
    option_result = (
        f"{options.market_bias} | {options.persistence}; windows {windows_ready}/3; "
        f"PCR {options.pcr.state}"
    )

    ind3 = _indicator_vote(snapshot.indicators.three_minute)
    ind15 = _indicator_vote(snapshot.indicators.fifteen_minute)
    indicator_bull, indicator_bear, indicator_neutral = _weighted_scores(
        [
            (
                *ind3,
                0.40 if snapshot.indicators.three_minute.status == "READY" else 0.0,
            ),
            (
                *ind15,
                0.60 if snapshot.indicators.fifteen_minute.status == "READY" else 0.0,
            ),
        ]
    )
    indicator_confidence = (
        90.0
        if all(
            item.status == "READY"
            for item in (
                snapshot.indicators.three_minute,
                snapshot.indicators.fifteen_minute,
            )
        )
        else 45.0
        if any(
            item.status == "READY"
            for item in (
                snapshot.indicators.three_minute,
                snapshot.indicators.fifteen_minute,
            )
        )
        else 0.0
    )
    indicator_result = (
        f"{_dominant_label(indicator_bull, indicator_bear, indicator_neutral)} | "
        f"15m {snapshot.indicators.fifteen_minute.ema_state}, "
        f"{snapshot.indicators.fifteen_minute.macd_state}, {snapshot.indicators.fifteen_minute.rsi_state}"
    )

    level_scores = _level_scores(snapshot)
    volume_scores = _volume_scores(snapshot)
    levels_bull, levels_bear, levels_neutral = _weighted_scores(
        [(*level_scores, 0.45), (*volume_scores, 0.55)]
    )
    levels_confidence = (
        70.0 if snapshot.levels.status == "READY" else 0.0
    ) * 0.45 + snapshot.volume.confidence * 0.55
    levels_result = (
        f"{snapshot.levels.current_position} | {snapshot.volume.overall_view}; "
        f"room ↑{snapshot.levels.upside_room if snapshot.levels.upside_room is not None else '—'} "
        f"↓{snapshot.levels.downside_room if snapshot.levels.downside_room is not None else '—'}"
    )

    heavy_scores = _heavyweight_scores(snapshot)
    inst_scores = _institutional_scores(snapshot)
    support_bull, support_bear, support_neutral = _weighted_scores(
        [(*heavy_scores, 0.70), (*inst_scores, 0.30)]
    )
    support_confidence = (
        snapshot.heavyweights.confidence * 0.70
        + snapshot.institutional_context.confidence * 0.30
    )
    support_result = f"Top-7 {snapshot.heavyweights.state} | FII/DII {snapshot.institutional_context.state}"

    vix = snapshot.vix_context
    risk_result = (
        f"{snapshot.market_session.label} | VIX {vix.seller_environment}; "
        f"event {snapshot.event_risk.level}"
    )

    return [
        _row(
            "Price Action",
            price_bull,
            price_bear,
            price_neutral,
            snapshot.price_action.confidence,
            price_result,
        ),
        _row(
            "OI & Options Flow",
            option_bull,
            option_bear,
            option_neutral,
            options.confidence,
            option_result,
        ),
        _row(
            "EMA / MACD / RSI",
            indicator_bull,
            indicator_bear,
            indicator_neutral,
            indicator_confidence,
            indicator_result,
        ),
        _row(
            "Levels & Volume",
            levels_bull,
            levels_bear,
            levels_neutral,
            levels_confidence,
            levels_result,
        ),
        _row(
            "Top-7 & FII/DII",
            support_bull,
            support_bear,
            support_neutral,
            support_confidence,
            support_result,
        ),
        _row(
            "VIX / Data / Event Risk",
            None,
            None,
            None,
            _feed_confidence(snapshot),
            risk_result,
        ),
    ]
