"""Leading next-move forecast and current-vs-future transition gate.

This is deliberately separate from the structural One-Brain decision.  It
estimates continuation/reversal/range paths; it cannot place an order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Any


def _num(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _direction(value: Any) -> str:
    text = str(value or "").upper()
    if "BULL" in text or text == "UP":
        return "UP"
    if "BEAR" in text or text == "DOWN":
        return "DOWN"
    return "RANGE"


def _band(value: float | None, low: float, high: float) -> str:
    if value is None:
        return "NA"
    return "LOW" if value <= low else "HIGH" if value >= high else "MID"


def _normalise(up: float, down: float, sideways: float) -> tuple[float, float, float]:
    values = [max(1.0, up), max(1.0, down), max(1.0, sideways)]
    total = sum(values)
    result = [round(100.0 * value / total, 1) for value in values]
    result[2] = round(100.0 - result[0] - result[1], 1)
    return result[0], result[1], result[2]


def feature_signature(snapshot: Any) -> str:
    """Small, non-identifying regime key used to match historical outcomes."""
    spot = _num(snapshot.nifty_quote.get("last_price"))
    rsi = _num(snapshot.indicators.three_minute.rsi14)
    up_room = _num(snapshot.levels.upside_room)
    down_room = _num(snapshot.levels.downside_room)
    atr = max(8.0, _num(snapshot.price_action.three_minute.atr14) or 18.0)
    if up_room is None or down_room is None:
        room = "NA"
    elif up_room < atr:
        room = "TOP_NEAR"
    elif down_room < atr:
        room = "BOTTOM_NEAR"
    else:
        room = "ROOM"
    hour = snapshot.created_at.hour
    session = "OPEN" if hour < 11 else "MID" if hour < 14 else "LATE"
    momentum = "FLAT"
    frame = snapshot.candles_1m
    if len(frame) >= 4 and "close" in frame:
        change = _num(frame.iloc[-1]["close"])
        old = _num(frame.iloc[-4]["close"])
        if change is not None and old is not None:
            momentum = "UP" if change-old >= 3 else "DOWN" if old-change >= 3 else "FLAT"
    return "|".join((
        _direction(snapshot.decision.market_direction),
        _band(rsi, 35, 65), room, momentum, session,
        _direction(snapshot.option_intelligence.market_bias),
    ))


@dataclass(frozen=True)
class FutureBrainResult:
    current_direction: str
    current_strength: float
    up_5m: float
    down_5m: float
    range_5m: float
    up_15m: float
    down_15m: float
    range_15m: float
    next_direction: str
    transition: str
    final_gate: str
    preferred_direction: str
    confirmation: str
    invalidation: str
    reasons: tuple[str, ...]
    feature_key: str
    historical_matches: int
    historical_status: str
    model_label: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _historical_rates(outcomes: list[dict[str, Any]], key: str, horizon: int) -> tuple[int, tuple[float, float, float] | None]:
    rows = [row for row in outcomes if row.get("feature_key") == key and int(row.get("horizon_minutes") or 0) == horizon and row.get("status") == "OBSERVED"]
    up = down = sideways = 0
    for row in rows:
        move = _num(row.get("spot_change"))
        if move is None:
            continue
        threshold = max(5.0, _num(row.get("move_threshold_points")) or 8.0)
        if move >= threshold:
            up += 1
        elif move <= -threshold:
            down += 1
        else:
            sideways += 1
    count = up + down + sideways
    return count, ((100*up/count, 100*down/count, 100*sideways/count) if count else None)


def calculate_future_brain(snapshot: Any, previous_snapshot: Any | None = None, outcomes: list[dict[str, Any]] | None = None) -> FutureBrainResult:
    """Estimate next paths from leading changes, then challenge Current Brain."""
    current = _direction(snapshot.decision.market_direction)
    current_eval = snapshot.decision.pe_sell if current == "UP" else snapshot.decision.ce_sell if current == "DOWN" else snapshot.decision.iron_condor
    current_strength = round(float(getattr(current_eval, "score", 0.0) or 0.0), 1)
    up = down = 30.0
    sideways = 25.0
    reasons: list[str] = []

    three = snapshot.indicators.three_minute
    rsi = _num(three.rsi14)
    previous_rsi = _num(three.previous_rsi14)
    hist = _num(three.macd_histogram)
    previous_hist = _num(three.previous_macd_histogram)
    if rsi is not None:
        if rsi >= 68:
            down += 13; reasons.append("3m RSI upper zone: downside reversal risk")
        elif rsi <= 32:
            up += 13; reasons.append("3m RSI lower zone: upside reversal risk")
        else:
            sideways += 3
    if rsi is not None and previous_rsi is not None:
        delta = rsi - previous_rsi
        if delta >= 2:
            up += 10; reasons.append("RSI slope rising")
        elif delta <= -2:
            down += 10; reasons.append("RSI slope falling")
    if hist is not None and previous_hist is not None:
        delta = hist - previous_hist
        if delta > 0:
            up += 9; reasons.append("MACD histogram improving")
        elif delta < 0:
            down += 9; reasons.append("MACD histogram weakening")

    frame = snapshot.candles_1m
    if len(frame) >= 6 and "close" in frame:
        last = _num(frame.iloc[-1]["close"])
        old = _num(frame.iloc[-6]["close"])
        if last is not None and old is not None:
            move = last-old
            if move >= 5:
                up += min(14, abs(move)); reasons.append("Recent 1m price momentum up")
            elif move <= -5:
                down += min(14, abs(move)); reasons.append("Recent 1m price momentum down")
            else:
                sideways += 7

    atr = max(8.0, _num(snapshot.price_action.three_minute.atr14) or 18.0)
    up_room, down_room = _num(snapshot.levels.upside_room), _num(snapshot.levels.downside_room)
    if up_room is not None and up_room < atr:
        down += 12; sideways += 5; reasons.append("Resistance/upper room is tight")
    if down_room is not None and down_room < atr:
        up += 12; sideways += 5; reasons.append("Support/lower room is tight")

    option_bias = _direction(snapshot.option_intelligence.market_bias)
    option_weight = min(10.0, max(3.0, float(snapshot.option_intelligence.confidence or 0.0)/10))
    if option_bias == "UP": up += option_weight
    elif option_bias == "DOWN": down += option_weight
    else: sideways += option_weight
    heavy_move = _num(snapshot.heavyweights.recent_3m_move_pct)
    if heavy_move is not None:
        if heavy_move >= .03: up += 7
        elif heavy_move <= -.03: down += 7
        else: sideways += 3
    activity = snapshot.big_player_activity
    if activity is not None and float(activity.score or 0) >= 60:
        if activity.direction == "BUYING": up += 7
        elif activity.direction == "SELLING": down += 7

    # Structural trend is useful at 15m, but deliberately cannot dominate the
    # faster 5m turning-point estimate.
    up5, down5, range5 = _normalise(up, down, sideways)
    if current == "UP": up += 10
    elif current == "DOWN": down += 10
    else: sideways += 10
    up15, down15, range15 = _normalise(up, down, sideways)

    key = feature_signature(snapshot)
    count5, rates5 = _historical_rates(outcomes or [], key, 5)
    count15, rates15 = _historical_rates(outcomes or [], key, 15)
    matches = max(count5, count15)
    # Historical observations only become a bounded calibration layer; they
    # never overpower fresh market evidence.
    if rates5 and count5 >= 10:
        weight = min(.25, count5/200)
        up5, down5, range5 = _normalise(up5*(1-weight)+rates5[0]*weight, down5*(1-weight)+rates5[1]*weight, range5*(1-weight)+rates5[2]*weight)
    if rates15 and count15 >= 10:
        weight = min(.25, count15/200)
        up15, down15, range15 = _normalise(up15*(1-weight)+rates15[0]*weight, down15*(1-weight)+rates15[1]*weight, range15*(1-weight)+rates15[2]*weight)

    paths = {"UP": up15, "DOWN": down15, "RANGE": range15}
    next_direction = max(paths, key=paths.get)
    ordered = sorted(paths.values(), reverse=True)
    weak = paths[next_direction] < 45 or ordered[0]-ordered[1] < 7
    fast = snapshot.price_action.three_minute
    fast_direction = (
        "UP" if float(fast.bullish_score or 0) >= float(fast.bearish_score or 0) + 12
        else "DOWN" if float(fast.bearish_score or 0) >= float(fast.bullish_score or 0) + 12
        else "RANGE"
    )
    if weak:
        transition, gate, preferred = "MIXED / TRANSITION", "WAIT — NO CLEAR FUTURE EDGE", "WAIT"
    elif next_direction == "RANGE":
        transition, gate, preferred = "RANGE / COMPRESSION", "WAIT FOR RANGE CONFIRMATION", "RANGE"
    elif next_direction == current:
        transition, gate, preferred = f"{current} CONTINUATION", f"{current} CONTINUATION WATCH", current
    elif fast_direction == next_direction and paths[next_direction] >= 50:
        transition = f"{current} → {next_direction} REVERSAL CONFIRMED"
        gate, preferred = f"{next_direction} REVERSAL PAPER TEST", next_direction
        reasons.append("3m price action confirms the forecast reversal")
    else:
        transition, gate, preferred = f"{current} → {next_direction} REVERSAL WATCH", "WAIT FOR REVERSAL CONFIRMATION", next_direction

    if next_direction == "UP":
        confirmation = "3m bullish close + rising RSI/MACD + resistance room"
        invalidation = "Latest 3m swing low / support break"
    elif next_direction == "DOWN":
        confirmation = "3m bearish close + falling RSI/MACD + support room"
        invalidation = "Latest 3m swing high / resistance break"
    else:
        confirmation = "Both barriers intact + shrinking momentum"
        invalidation = "3m close outside the active range"
    history_status = "INSUFFICIENT DATA" if matches < 30 else "EARLY ESTIMATE" if matches < 100 else "MODERATE HISTORY" if matches < 300 else "STRONGER HISTORICAL BASE"
    model_label = "FORECAST SCORE" if matches < 100 else "HISTORICALLY CALIBRATED ESTIMATE"
    return FutureBrainResult(current, current_strength, up5, down5, range5, up15, down15, range15, next_direction, transition, gate, preferred, confirmation, invalidation, tuple(dict.fromkeys(reasons))[:5], key, matches, history_status, model_label, "READY" if snapshot.market_session.is_live else "REFERENCE ONLY")
