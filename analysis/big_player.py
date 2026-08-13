from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from analysis.technical_utils import clamp
from models import (
    BarrierMap,
    BigPlayerActivity,
    CoreMarketEvidence,
    HeavyweightBundle,
    MarketSession,
    OptionIntelligence,
    VolumeBundle,
)


def _time_window(as_of: datetime) -> str:
    current = as_of.time().replace(tzinfo=None)
    if current < time(10, 0):
        return "OPENING HIGH ACTIVITY"
    if current < time(13, 15):
        return "MIDDAY / NORMAL"
    if current < time(14, 15):
        return "AFTERNOON BUILD-UP"
    if current < time(15, 0):
        return "CLOSING PRESSURE"
    return "FINAL HOUR / HIGH ACTIVITY"


def _future_setup(frame: pd.DataFrame) -> tuple[str, float | None, str]:
    if frame is None or frame.empty:
        return "OI UNAVAILABLE", None, "NEUTRAL"
    source = frame.copy()
    oi_column = (
        "open_interest"
        if "open_interest" in source.columns
        else "oi"
        if "oi" in source.columns
        else None
    )
    if oi_column is None:
        return "OI UNAVAILABLE", None, "NEUTRAL"
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False).astype(bool)]
    source = source.dropna(subset=["close", oi_column]).tail(4)
    if len(source) < 2:
        return "OI WARMING UP", None, "NEUTRAL"
    first = source.iloc[0]
    last = source.iloc[-1]
    old_oi = float(first[oi_column])
    new_oi = float(last[oi_column])
    oi_change = ((new_oi - old_oi) / abs(old_oi) * 100.0) if old_oi else None
    price_change = float(last["close"]) - float(first["close"])
    oi_rising = oi_change is not None and oi_change > 0.05
    oi_falling = oi_change is not None and oi_change < -0.05
    if price_change > 0 and oi_rising:
        return "LONG BUILD-UP", oi_change, "BUY"
    if price_change < 0 and oi_rising:
        return "SHORT BUILD-UP", oi_change, "SELL"
    if price_change > 0 and oi_falling:
        return "SHORT COVERING", oi_change, "BUY"
    if price_change < 0 and oi_falling:
        return "LONG UNWINDING", oi_change, "SELL"
    return "OI / PRICE MIXED", oi_change, "NEUTRAL"


def _volume_intensity(ratio: float | None) -> float:
    if ratio is None:
        return 25.0
    # 1.0x is normal; 1.8x is a surge and 2.5x is extreme.
    return clamp(20.0 + max(0.0, ratio - 0.8) * 58.0, 10.0, 100.0)


def _direction_label(value: str) -> str:
    upper = str(value or "").upper()
    if "BULL" in upper or "UP" in upper or "ADVANC" in upper:
        return "BUY"
    if "BEAR" in upper or "DOWN" in upper or "DECLIN" in upper:
        return "SELL"
    return "NEUTRAL"


def calculate_big_player_activity(
    *,
    as_of: datetime,
    market_session: MarketSession,
    volume: VolumeBundle,
    future_candles_1m: pd.DataFrame,
    options: OptionIntelligence,
    heavyweights: HeavyweightBundle,
    barrier_map: BarrierMap,
    core: CoreMarketEvidence,
    history: list[dict[str, Any]] | None = None,
) -> BigPlayerActivity:
    """Combine participation evidence without claiming trader identity.

    Scores are deliberately bounded and require cross-module agreement. Persistence
    is calculated from the last three authoritative app snapshots.
    """

    history = list(history or [])
    three = volume.three_minute
    ratio = three.relative_volume if three.status == "READY" else None
    intensity = _volume_intensity(ratio)
    futures_setup, oi_change, futures_direction = _future_setup(future_candles_1m)
    activity_type = (
        futures_setup
        if futures_setup
        in {"LONG BUILD-UP", "SHORT BUILD-UP", "SHORT COVERING", "LONG UNWINDING"}
        else "DIRECTIONAL ACTIVITY"
    )
    participant_explanation, next_confirmation = {
        "LONG BUILD-UP": (
            "Naye buyers futures me long positions bana rahe hain",
            "Options + Top-7 bullish aur resistance ke upar close se fresh buying confirm hogi",
        ),
        "SHORT BUILD-UP": (
            "Naye sellers futures me short positions bana rahe hain",
            "Options + Top-7 bearish aur support ke neeche close se fresh selling confirm hogi",
        ),
        "SHORT COVERING": (
            "Purane sellers apni short positions band kar rahe hain; isliye price upar hai",
            "OI dobara badhe, Options + Top-7 bullish hon aur resistance break ho to fresh long buying maanenge",
        ),
        "LONG UNWINDING": (
            "Purane buyers apni long positions band kar rahe hain; isliye price neeche hai",
            "OI badhe, Options + Top-7 bearish hon aur support break ho to fresh short selling maanenge",
        ),
    }.get(
        activity_type,
        (
            "Buyer/seller participation abhi mixed hai",
            "Agla snapshot, volume, OI aur nearest level reaction dekho",
        ),
    )

    option_gap = float(options.bullish_score) - float(options.bearish_score)
    option_direction = "BUY" if option_gap >= 8 else "SELL" if option_gap <= -8 else "NEUTRAL"
    option_strength = min(100.0, abs(option_gap) * 1.8 + float(options.confidence) * 0.35)
    top7_direction = _direction_label(heavyweights.state)
    top7_strength = min(100.0, abs(float(heavyweights.weighted_move_pct or 0.0)) * 85.0 + 30.0)
    price_direction = str(getattr(three, "price_direction", "NEUTRAL") or "NEUTRAL").upper()
    candle_direction = "BUY" if price_direction == "UP" else "SELL" if price_direction == "DOWN" else "NEUTRAL"

    buy = intensity * 0.22
    sell = intensity * 0.22
    reasons: list[str] = []
    cautions: list[str] = []
    for direction, weight, strength, reason in (
        (candle_direction, 0.20, intensity, f"3m futures volume {ratio:.2f}x with price {price_direction}" if ratio is not None else "Futures volume baseline unavailable"),
        (futures_direction, 0.25, 82.0 if oi_change is not None else 25.0, f"Futures {futures_setup}"),
        (option_direction, 0.20, option_strength, f"ATM option flow {options.market_bias}"),
        (top7_direction, 0.13, top7_strength, f"Top-7 {heavyweights.state}"),
    ):
        if direction == "BUY":
            buy += strength * weight
            reasons.append(reason)
        elif direction == "SELL":
            sell += strength * weight
            reasons.append(reason)

    # Afternoon/closing time raises activity sensitivity, never direction by itself.
    time_window = _time_window(as_of)
    time_bonus = 6.0 if time_window in {"AFTERNOON BUILD-UP", "CLOSING PRESSURE"} else 9.0 if "FINAL HOUR" in time_window else 0.0
    buy += time_bonus
    sell += time_bonus
    buy = clamp(buy, 0.0, 100.0)
    sell = clamp(sell, 0.0, 100.0)
    direction = "BUYING" if buy - sell >= 7 else "SELLING" if sell - buy >= 7 else "MIXED"
    score = max(buy, sell) if direction != "MIXED" else (buy + sell) / 2.0

    near_resistance = barrier_map.nearest_resistance
    near_support = barrier_map.nearest_support
    level_reaction = "NO NEAR LEVEL REACTION"
    absorption = False
    if ratio is not None and ratio >= 1.2 and price_direction == "FLAT":
        absorption = True
        level_reaction = "HIGH ACTIVITY ABSORBED / PRICE FLAT"
    elif direction == "BUYING" and near_resistance is not None and near_resistance.distance_points <= 12:
        level_reaction = "BUYING TESTING RESISTANCE"
    elif direction == "SELLING" and near_support is not None and near_support.distance_points <= 12:
        level_reaction = "SELLING TESTING SUPPORT"

    matching = 0
    recent_history = history[-2:]
    total = min(3, len(recent_history) + 1)
    if direction in {"BUYING", "SELLING"} and score >= 40:
        recent = [
            str(item.get("direction", ""))
            if float(item.get("score", 0.0) or 0.0) >= 40
            else "NORMAL"
            for item in recent_history
        ] + [direction]
        matching = sum(item == direction for item in recent[-3:])
        persistence = "CONFIRMED" if matching >= 2 else "WARMING UP"
    else:
        persistence = "NORMAL" if score < 40 else "MIXED"

    if absorption and score >= 55:
        state = "ABSORPTION"
    elif score >= 90 and matching >= 2:
        state = "EXTREME ACTIVITY"
    elif score >= 75 and matching >= 2:
        state = "VERY STRONG"
    elif score >= 60:
        state = "STRONG"
    elif score >= 40:
        state = "WATCH"
    else:
        state = "NORMAL"

    core_direction = _direction_label(core.market_state)
    opposite = (direction == "BUYING" and core_direction == "SELL") or (direction == "SELLING" and core_direction == "BUY")
    if state == "EXTREME ACTIVITY" and opposite:
        reversal_risk = "DANGER"
    elif score >= 75 and matching >= 2 and opposite:
        reversal_risk = "HIGH"
    elif score >= 60 and opposite:
        reversal_risk = "WATCH"
    else:
        reversal_risk = "NORMAL"

    if not market_session.is_live:
        cautions.append("Market live nahi; result last available data ka hai")
        status = "REFERENCE ONLY"
    elif volume.status == "UNAVAILABLE":
        cautions.append("Futures volume unavailable")
        status = "PARTIAL"
    else:
        status = "READY"
    if options.confidence < 50:
        cautions.append("Option-flow bharosa low hai")
    if activity_type == "SHORT COVERING":
        cautions.append("Price up hai, lekin fresh long buying confirm nahi")
        if option_direction != "BUY" or top7_direction != "BUY":
            cautions.append("Options/Top-7 fresh buying ko confirm nahi kar rahe")
    elif activity_type == "LONG UNWINDING":
        cautions.append("Price down hai, lekin fresh short selling confirm nahi")
        if option_direction != "SELL" or top7_direction != "SELL":
            cautions.append("Options/Top-7 fresh selling ko confirm nahi kar rahe")

    return BigPlayerActivity(
        direction=direction,
        state=state,
        score=round(score, 1),
        buy_score=round(buy, 1),
        sell_score=round(sell, 1),
        confirmation_count=matching,
        confirmation_total=max(1, total),
        persistence=persistence,
        reversal_risk=reversal_risk,
        time_window=time_window,
        futures_volume_ratio=round(float(ratio), 2) if ratio is not None else None,
        futures_oi_change_pct=round(float(oi_change), 2) if oi_change is not None else None,
        futures_setup=futures_setup,
        option_confirmation=options.market_bias,
        top7_confirmation=heavyweights.state,
        level_reaction=level_reaction,
        reasons=tuple(dict.fromkeys(reasons))[:4],
        cautions=tuple(dict.fromkeys(cautions))[:3],
        status=status,
        activity_type=activity_type,
        participant_explanation=participant_explanation,
        next_confirmation=next_confirmation,
    )
