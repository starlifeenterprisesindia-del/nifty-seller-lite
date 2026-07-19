from __future__ import annotations

import pandas as pd

from analysis.technical_utils import (
    atr_value,
    clamp,
    completed_candles,
    confirmed_swings,
)
from config import CONFIG
from models import PriceActionBundle, TimeframePriceAction


def _empty(timeframe: str, status: str) -> TimeframePriceAction:
    return TimeframePriceAction(
        timeframe=timeframe,
        as_of=None,
        structure="UNAVAILABLE",
        event="UNAVAILABLE",
        move_stage="UNAVAILABLE",
        last_swing_high=None,
        prior_swing_high=None,
        last_swing_low=None,
        prior_swing_low=None,
        invalidation_level=None,
        atr14=None,
        bullish_score=0.0,
        bearish_score=0.0,
        range_score=0.0,
        confidence=0.0,
        reasons=(),
        status=status,
    )


def _structure_scores(structure: str) -> tuple[float, float, float]:
    if structure == "BULLISH HH/HL":
        return 82.0, 12.0, 20.0
    if structure == "BEARISH LH/LL":
        return 12.0, 82.0, 20.0
    if structure == "RANGE":
        return 22.0, 22.0, 82.0
    return 44.0, 44.0, 52.0


def _detect_event(
    source: pd.DataFrame,
    structure: str,
    support: float | None,
    resistance: float | None,
    atr: float,
) -> str:
    if len(source) < 2:
        return "INSUFFICIENT BARS"
    last = source.iloc[-1]
    previous = source.iloc[-2]
    tolerance = max(0.5, atr * CONFIG.breakout_atr_tolerance)
    candle_range = max(float(last["high"] - last["low"]), 0.01)
    upper_wick = float(last["high"] - max(last["open"], last["close"])) / candle_range
    lower_wick = float(min(last["open"], last["close"]) - last["low"]) / candle_range

    if resistance is not None:
        if (
            float(last["high"]) > resistance + tolerance
            and float(last["close"]) <= resistance
        ):
            return "FALSE BREAKOUT / RESISTANCE REJECTION"
        if (
            float(previous["close"]) <= resistance
            and float(last["close"]) > resistance + tolerance
        ):
            return "BREAKOUT CONFIRMED"
        if abs(float(last["close"]) - resistance) <= tolerance and upper_wick >= 0.40:
            return "RESISTANCE REJECTION"

    if support is not None:
        if float(last["low"]) < support - tolerance and float(last["close"]) >= support:
            return "FALSE BREAKDOWN / SUPPORT REJECTION"
        if (
            float(previous["close"]) >= support
            and float(last["close"]) < support - tolerance
        ):
            return "BREAKDOWN CONFIRMED"
        if abs(float(last["close"]) - support) <= tolerance and lower_wick >= 0.40:
            return "SUPPORT HOLD / BULLISH REJECTION"

    if structure == "BULLISH HH/HL":
        if float(last["close"]) < float(previous["close"]):
            return "BULLISH PULLBACK"
        return "BULLISH CONTINUATION"
    if structure == "BEARISH LH/LL":
        if float(last["close"]) > float(previous["close"]):
            return "BEARISH RECOVERY"
        return "BEARISH CONTINUATION"
    if structure == "RANGE":
        return "RANGE ROTATION"
    return "STRUCTURE MIXED"


def _move_stage(
    structure: str,
    event: str,
    close: float,
    support: float | None,
    resistance: float | None,
    atr: float,
) -> str:
    if "FALSE" in event or "REJECTION" in event:
        return "EXHAUSTION / REVERSAL RISK"
    if "BREAKOUT CONFIRMED" in event or "BREAKDOWN CONFIRMED" in event:
        return "EARLY BREAK"
    if structure == "RANGE":
        return "RANGE"
    anchor = support if structure == "BULLISH HH/HL" else resistance
    if anchor is None or atr <= 0:
        return "DEVELOPING"
    displacement = abs(close - anchor) / atr
    if displacement < 1.0:
        return "EARLY"
    if displacement < 2.5:
        return "DEVELOPING"
    if displacement < 4.0:
        return "MATURE"
    return "EXHAUSTION RISK"


def calculate_timeframe_price_action(
    frame: pd.DataFrame,
    timeframe: str,
) -> TimeframePriceAction:
    source = completed_candles(frame)
    if len(source) < 12:
        return _empty(timeframe, f"INSUFFICIENT COMPLETED CANDLES ({len(source)}/12)")

    atr = atr_value(source)
    if atr is None or atr <= 0:
        return _empty(timeframe, "ATR UNAVAILABLE")
    highs, lows = confirmed_swings(source)
    required = CONFIG.minimum_structure_swings
    if len(highs) < required or len(lows) < required:
        return _empty(
            timeframe,
            f"INSUFFICIENT CONFIRMED SWINGS (H{len(highs)}/L{len(lows)})",
        )

    prior_high, last_high = highs[-2], highs[-1]
    prior_low, last_low = lows[-2], lows[-1]
    tolerance = max(0.5, atr * CONFIG.breakout_atr_tolerance)

    higher_high = last_high.price > prior_high.price + tolerance
    higher_low = last_low.price > prior_low.price + tolerance
    lower_high = last_high.price < prior_high.price - tolerance
    lower_low = last_low.price < prior_low.price - tolerance

    if higher_high and higher_low:
        structure = "BULLISH HH/HL"
    elif lower_high and lower_low:
        structure = "BEARISH LH/LL"
    else:
        swing_width = max(last_high.price, prior_high.price) - min(
            last_low.price, prior_low.price
        )
        structure = "RANGE" if swing_width <= atr * 4.0 else "MIXED / TRANSITION"

    bullish, bearish, range_score = _structure_scores(structure)
    event = _detect_event(source, structure, last_low.price, last_high.price, atr)
    close = float(source.iloc[-1]["close"])
    stage = _move_stage(structure, event, close, last_low.price, last_high.price, atr)

    if "BREAKOUT CONFIRMED" in event or "SUPPORT HOLD" in event:
        bullish = clamp(bullish + 10, 0, 100)
        bearish = clamp(bearish - 5, 0, 100)
    if "BREAKDOWN CONFIRMED" in event or "RESISTANCE REJECTION" in event:
        bearish = clamp(bearish + 10, 0, 100)
        bullish = clamp(bullish - 5, 0, 100)
    if "FALSE" in event:
        range_score = clamp(range_score + 12, 0, 100)
    if stage.startswith("EXHAUSTION"):
        range_score = clamp(range_score + 8, 0, 100)

    reasons: list[str] = []
    reasons.append(structure)
    reasons.append(event)
    reasons.append(
        f"Confirmed swings: H {prior_high.price:.2f}→{last_high.price:.2f}, L {prior_low.price:.2f}→{last_low.price:.2f}"
    )
    confidence = clamp(58 + min(len(highs) + len(lows), 12) * 2.5, 0, 92)
    invalidation = (
        last_low.price
        if structure == "BULLISH HH/HL"
        else last_high.price
        if structure == "BEARISH LH/LL"
        else None
    )
    return TimeframePriceAction(
        timeframe=timeframe,
        as_of=pd.Timestamp(source.iloc[-1]["timestamp"]).to_pydatetime(),
        structure=structure,
        event=event,
        move_stage=stage,
        last_swing_high=last_high.price,
        prior_swing_high=prior_high.price,
        last_swing_low=last_low.price,
        prior_swing_low=prior_low.price,
        invalidation_level=invalidation,
        atr14=atr,
        bullish_score=round(bullish, 1),
        bearish_score=round(bearish, 1),
        range_score=round(range_score, 1),
        confidence=round(confidence, 1),
        reasons=tuple(reasons[:3]),
        status="READY",
    )


def calculate_price_action_bundle(
    candles_3m: pd.DataFrame,
    candles_15m: pd.DataFrame,
) -> PriceActionBundle:
    three = calculate_timeframe_price_action(candles_3m, "3 Minute")
    fifteen = calculate_timeframe_price_action(candles_15m, "15 Minute")

    if fifteen.status != "READY" or three.status != "READY":
        relationship = "TIMEFRAME EVIDENCE INCOMPLETE"
        combined = "UNAVAILABLE"
        confidence = min(three.confidence, fifteen.confidence)
    elif fifteen.structure == "BULLISH HH/HL" and three.structure == "BEARISH LH/LL":
        relationship = "15M BULLISH / 3M PULLBACK"
        combined = "BULLISH PULLBACK"
        confidence = (fifteen.confidence * 0.65) + (three.confidence * 0.35)
    elif fifteen.structure == "BEARISH LH/LL" and three.structure == "BULLISH HH/HL":
        relationship = "15M BEARISH / 3M RECOVERY"
        combined = "BEARISH RECOVERY"
        confidence = (fifteen.confidence * 0.65) + (three.confidence * 0.35)
    elif fifteen.structure == three.structure:
        relationship = "TIMEFRAMES ALIGNED"
        combined = fifteen.structure
        confidence = (fifteen.confidence + three.confidence) / 2
    elif "RANGE" in {fifteen.structure, three.structure}:
        relationship = "TREND / RANGE CONFLICT"
        combined = "MIXED / RANGE"
        confidence = min(fifteen.confidence, three.confidence) * 0.85
    else:
        relationship = "TIMEFRAMES MIXED"
        combined = "MIXED / TRANSITION"
        confidence = min(fifteen.confidence, three.confidence) * 0.80

    return PriceActionBundle(
        three_minute=three,
        fifteen_minute=fifteen,
        relationship=relationship,
        combined_state=combined,
        confidence=round(confidence, 1),
    )
