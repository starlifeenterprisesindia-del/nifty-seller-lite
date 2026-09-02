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


def _classify_future_window(
    source: pd.DataFrame, oi_column: str
) -> tuple[str, float | None, str, float]:
    first, last = source.iloc[0], source.iloc[-1]
    old_oi, new_oi = float(first[oi_column]), float(last[oi_column])
    oi_change = ((new_oi - old_oi) / abs(old_oi) * 100.0) if old_oi else None
    price_change = float(last["close"]) - float(first["close"])
    oi_rising = oi_change is not None and oi_change > 0.05
    oi_falling = oi_change is not None and oi_change < -0.05
    if price_change > 0 and oi_rising:
        return "LONG BUILD-UP", oi_change, "BUY", price_change
    if price_change < 0 and oi_rising:
        return "SHORT BUILD-UP", oi_change, "SELL", price_change
    if price_change > 0 and oi_falling:
        return "SHORT COVERING", oi_change, "BUY", price_change
    if price_change < 0 and oi_falling:
        return "LONG UNWINDING", oi_change, "SELL", price_change
    return "OI / PRICE MIXED", oi_change, "NEUTRAL", price_change


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
    source = source.dropna(subset=["close", oi_column])
    if "timestamp" in source:
        source = source.sort_values("timestamp").drop_duplicates("timestamp")
    if len(source) < 2:
        return "OI WARMING UP", None, "NEUTRAL"
    # Fixed current window: do not select the largest historical move as live flow.
    return _classify_future_window(source.tail(4), oi_column)[:3]


def _recent_volume_ratio(frame: pd.DataFrame) -> float | None:
    if frame is None or frame.empty or "volume" not in frame.columns:
        return None
    source = frame.copy()
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False).astype(bool)]
    values = pd.to_numeric(source["volume"], errors="coerce").dropna().tail(16)
    if len(values) < 6:
        return None
    baseline = float(values.iloc[:-1].tail(10).median())
    return float(values.iloc[-1]) / baseline if baseline > 0 else None


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
    observation_key: str = "",
    spot_candles_1m: pd.DataFrame | None = None,
) -> BigPlayerActivity:
    """Combine participation evidence without claiming trader identity.

    Scores are deliberately bounded and require cross-module agreement. Persistence
    is calculated from the last three authoritative app snapshots.
    """

    if future_candles_1m is not None and not future_candles_1m.empty and "timestamp" in future_candles_1m:
        timestamps = pd.to_datetime(future_candles_1m.timestamp)
        future_candles_1m = future_candles_1m.loc[timestamps.dt.date.eq(as_of.date())].copy()

    def recent_observation(item: dict[str, Any]) -> bool:
        stamp = item.get("captured_at")
        if not stamp:
            return True
        try:
            age = (as_of - datetime.fromisoformat(str(stamp))).total_seconds()
            return 0 <= age <= 180
        except (TypeError, ValueError):
            return False

    history = [
        item
        for item in list(history or [])
        if recent_observation(item) and (
            not observation_key
            or (
                bool(str(item.get("observation_key", "")))
                and str(item.get("observation_key", "")) != str(observation_key)
            )
        )
    ]
    volume_windows = (volume.three_minute,)
    ratios = [
        float(item.relative_volume)
        for item in volume_windows
        if item.status == "READY" and item.relative_volume is not None
    ]
    one_minute_ratio = _recent_volume_ratio(future_candles_1m)
    if one_minute_ratio is not None:
        ratios.append(one_minute_ratio)
    ratio = ratios[0] if ratios else None
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
            "Options + Top-9 bullish aur resistance ke upar close se fresh buying confirm hogi",
        ),
        "SHORT BUILD-UP": (
            "Naye sellers futures me short positions bana rahe hain",
            "Options + Top-9 bearish aur support ke neeche close se fresh selling confirm hogi",
        ),
        "SHORT COVERING": (
            "Purane sellers apni short positions band kar rahe hain; isliye price upar hai",
            "OI dobara badhe, Options + Top-9 bullish hon aur resistance break ho to fresh long buying maanenge",
        ),
        "LONG UNWINDING": (
            "Purane buyers apni long positions band kar rahe hain; isliye price neeche hai",
            "OI badhe, Options + Top-9 bearish hon aur support break ho to fresh short selling maanenge",
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
    top_move = getattr(heavyweights, "recent_15m_move_pct", None)
    top7_direction = "BUY" if top_move is not None and top_move > 0.03 else "SELL" if top_move is not None and top_move < -0.03 else "NEUTRAL"
    top7_strength = min(100.0, abs(float(top_move or 0.0)) * 85.0 + 30.0) if top_move is not None else 0.0
    strongest_volume = volume.three_minute
    price_direction = str(
        getattr(strongest_volume, "price_direction", "NEUTRAL") or "NEUTRAL"
    ).upper()
    candle_direction = "BUY" if price_direction == "UP" else "SELL" if price_direction == "DOWN" else "NEUTRAL"

    buy = intensity * 0.20
    sell = intensity * 0.20
    reasons: list[str] = []
    cautions: list[str] = []
    for direction, weight, strength, reason in (
        (candle_direction, 0.25, intensity, f"3m volume {ratio:.2f}x with price {price_direction}" if ratio is not None else "Futures volume baseline unavailable"),
        (futures_direction, 0.28, 82.0 if oi_change is not None else 25.0, f"Futures {futures_setup}"),
        (option_direction, 0.21, option_strength, f"ATM option flow {options.market_bias}"),
        # Top-9 is useful context, but it cannot represent the remaining NIFTY stocks.
        (top7_direction, 0.07, top7_strength, f"Top-9 recent {getattr(heavyweights, 'recent_state', 'WARMING UP')}"),
    ):
        if direction == "BUY":
            buy += strength * weight
            reasons.append(reason)
        elif direction == "SELL":
            sell += strength * weight
            reasons.append(reason)

    # Afternoon/closing time raises activity sensitivity, never direction by itself.
    time_window = _time_window(as_of)
    time_bonus = 0.0  # time is context, not evidence of buying/selling strength
    buy += time_bonus
    sell += time_bonus
    buy = clamp(buy, 0.0, 100.0)
    sell = clamp(sell, 0.0, 100.0)
    direction = "BUYING" if buy - sell >= 7 else "SELLING" if sell - buy >= 7 else "MIXED"
    score = max(buy, sell) if direction != "MIXED" else (buy + sell) / 2.0

    completed = future_candles_1m.copy() if future_candles_1m is not None else pd.DataFrame()
    if not completed.empty and "is_complete" in completed.columns:
        completed = completed[completed["is_complete"].fillna(False).astype(bool)]
    completed = completed.dropna(subset=["close"]).tail(16) if not completed.empty else completed
    completed = completed.tail(4)
    current_spot = float(completed.iloc[-1]["close"]) if not completed.empty else None
    move_points = (
        abs(float(completed.iloc[-1]["close"]) - float(completed.iloc[0]["close"]))
        if len(completed) >= 2
        else 0.0
    )
    ranges = (completed["high"] - completed["low"]) if not completed.empty and {"high", "low"}.issubset(completed.columns) else pd.Series(dtype=float)
    required_move = max(4.0, float(ranges.median()) * 0.5) if not ranges.empty else 4.0

    spot_completed = spot_candles_1m.copy() if spot_candles_1m is not None else pd.DataFrame()
    if not spot_completed.empty and "is_complete" in spot_completed.columns:
        spot_completed = spot_completed[spot_completed["is_complete"].fillna(False).astype(bool)]
    spot_completed = spot_completed.dropna(subset=["close"]).tail(16) if not spot_completed.empty else spot_completed
    shock_3m = (
        float(spot_completed.iloc[-1]["close"]) - float(spot_completed.iloc[-4]["close"])
        if len(spot_completed) >= 4 else 0.0
    )
    shock_15m = (
        float(spot_completed.iloc[-1]["close"]) - float(spot_completed.iloc[0]["close"])
        if len(spot_completed) >= 16 else 0.0
    )
    shock_points = shock_15m if abs(shock_15m) >= abs(shock_3m) else shock_3m
    price_shock_state = (
        "PRICE SHOCK UP" if shock_points >= 30.0 or shock_3m >= 15.0
        else "PRICE SHOCK DOWN" if shock_points <= -30.0 or shock_3m <= -15.0
        else "NONE"
    )
    directional_support = sum(
        (
            futures_direction == ("BUY" if direction == "BUYING" else "SELL"),
            option_direction == ("BUY" if direction == "BUYING" else "SELL"),
            top7_direction == ("BUY" if direction == "BUYING" else "SELL"),
        )
    ) if direction in {"BUYING", "SELLING"} else 0
    large_activity = bool(
        direction in {"BUYING", "SELLING"}
        and (
            (ratio is not None and ratio >= 2.0)
            or (ratio is not None and ratio >= 1.5 and move_points >= required_move)
            or (move_points >= required_move and directional_support >= 2)
        )
    )

    previous_direction = ""
    previous_spot = None
    for item in reversed(history):
        if str(item.get("direction", "")) in {"BUYING", "SELLING"}:
            previous_direction = str(item.get("direction"))
            try:
                previous_spot = float(item.get("spot"))
            except (TypeError, ValueError):
                previous_spot = None
            break
    direction_change_blocked = bool(
        previous_direction
        and direction in {"BUYING", "SELLING"}
        and direction != previous_direction
        and previous_spot is not None
        and current_spot is not None
        and abs(current_spot - previous_spot) < required_move
    )
    if direction_change_blocked:
        direction = previous_direction
        score = buy if direction == "BUYING" else sell
        large_activity = False

    near_resistance = barrier_map.nearest_resistance
    near_support = barrier_map.nearest_support
    level_reaction = "NO NEAR LEVEL REACTION"
    absorption = False
    if ratio is not None and ratio >= 1.2 and price_direction == "FLAT":
        absorption = True
        level_reaction = "HIGH ACTIVITY / LIMITED PRICE RESPONSE (absorption unconfirmed)"
    elif direction == "BUYING" and near_resistance is not None and near_resistance.distance_points <= 12:
        level_reaction = "BUYING TESTING RESISTANCE"
    elif direction == "SELLING" and near_support is not None and near_support.distance_points <= 12:
        level_reaction = "SELLING TESTING SUPPORT"

    matching = 0
    recent_history = history[-1:]
    total = min(2, len(recent_history) + 1)
    if direction in {"BUYING", "SELLING"} and score >= 40:
        recent = [
            str(item.get("direction", ""))
            if float(item.get("score", 0.0) or 0.0) >= 40
            else "NORMAL"
            for item in recent_history
        ] + [direction]
        matching = sum(item == direction for item in recent[-2:])
        persistence = "CONFIRMED" if matching >= 2 else "WARMING UP"
    else:
        persistence = "NORMAL" if score < 40 else "SMALL MOVE"

    if direction_change_blocked:
        state = "FADING"
    elif not large_activity and score >= 40:
        state = "WATCH"
    elif absorption and score >= 55:
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

    if direction_change_blocked:
        move_state = "CHHOTA ULTA MOVE — PURANI DIRECTION ABHI KAYAM"
    elif not large_activity and score >= 40:
        move_state = "CHHOTA MOVE — BADI HALCHAL NAHI"
    elif matching >= 2:
        move_state = "PERSISTENT 2/2 — strength and price response separate"
    elif matching == 1:
        move_state = "FIRST OBSERVATION 1/2"
    else:
        move_state = "ABHI SAAF NAHI"

    if not market_session.is_live:
        cautions.append("Market live nahi; result last available data ka hai")
        status = "REFERENCE ONLY"
    elif volume.status != "READY" or futures_setup in {"OI UNAVAILABLE", "OI WARMING UP"}:
        cautions.append("Futures volume unavailable")
        status = "PARTIAL"
    else:
        status = "READY"
    if options.confidence < 50:
        cautions.append("Option-flow bharosa low hai")
    if activity_type == "SHORT COVERING":
        cautions.append("Price up hai, lekin fresh long buying confirm nahi")
        if option_direction != "BUY" or top7_direction != "BUY":
            cautions.append("Options/Top-9 fresh buying ko confirm nahi kar rahe")
    elif activity_type == "LONG UNWINDING":
        cautions.append("Price down hai, lekin fresh short selling confirm nahi")
        if option_direction != "SELL" or top7_direction != "SELL":
            cautions.append("Options/Top-9 fresh selling ko confirm nahi kar rahe")

    signed_move = float(completed.iloc[-1]["close"]) - float(completed.iloc[0]["close"]) if len(completed) >= 2 else None
    response = "UNCONFIRMED"
    if signed_move is not None and direction in {"BUYING", "SELLING"}:
        aligned_move = signed_move if direction == "BUYING" else -signed_move
        response = "FOLLOW-THROUGH" if aligned_move >= required_move else "OPPOSITE RESPONSE" if aligned_move <= -required_move else "PRICE HOLDING / STALLED"
    participant_explanation = "Price + OI inference: " + futures_setup + "; trader identity/intent unconfirmed"
    return BigPlayerActivity(
        direction=direction,
        state=state,
        score=round(score, 1),
        buy_score=round(buy, 1),
        sell_score=round(sell, 1),
        confirmation_count=matching,
        # Confirmation is a two-observation gate.  Showing 1/1 on the first
        # observation looked complete even though every consumer correctly
        # requires two matching observations before treating it as confirmed.
        confirmation_total=2,
        persistence=persistence,
        reversal_risk=reversal_risk,
        time_window=time_window,
        futures_volume_ratio=round(float(ratio), 2) if ratio is not None else None,
        futures_oi_change_pct=round(float(oi_change), 2) if oi_change is not None else None,
        futures_setup=futures_setup,
        option_confirmation=options.market_bias,
        top7_confirmation=getattr(heavyweights, "recent_state", "WARMING UP"),
        level_reaction=level_reaction,
        reasons=tuple(["Current flow: latest 3 completed 1m intervals; not trader identity"] + list(dict.fromkeys(reasons)))[:5],
        cautions=tuple(dict.fromkeys(cautions))[:3],
        status=status,
        activity_type=activity_type,
        participant_explanation=participant_explanation,
        next_confirmation=next_confirmation,
        move_state=move_state,
        move_points=round(float(move_points), 1),
        required_move_points=required_move,
        price_shock_state=price_shock_state,
        price_shock_points=round(abs(float(shock_points)), 1) if price_shock_state != "NONE" else None,
        price_response=response,
        futures_price_change_points=signed_move,
    )
