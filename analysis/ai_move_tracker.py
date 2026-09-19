"""Lightweight outcome tracker for the canonical Simple One-Brain prediction.

This module deliberately does *not* calculate indicators, option flow, Top-9 or a
second market view.  It freezes the latest canonical UP/DOWN thesis and compares
only subsequent NIFTY prices with that thesis.  The goal is observable feedback,
not another decision engine.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from config import CONFIG


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if isfinite(out) else default


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _level_payload(level: Any, label: str) -> dict[str, Any] | None:
    if level is None:
        return None
    lower = _num(getattr(level, "lower", None))
    upper = _num(getattr(level, "upper", None))
    midpoint = _num(getattr(level, "midpoint", None))
    if lower is None or upper is None:
        return None
    return {
        "label": str(getattr(level, "label", "") or label),
        "side": str(getattr(level, "side", "") or ("RESISTANCE" if label.startswith("R") else "SUPPORT")),
        "lower": lower,
        "upper": upper,
        "midpoint": midpoint if midpoint is not None else (lower + upper) / 2.0,
    }


def _pick_barrier(snapshot: Any, direction: str, spot: float) -> dict[str, Any] | None:
    barrier_map = getattr(snapshot, "barrier_map", None)
    if barrier_map is None:
        return None
    if direction == "UP":
        candidates = (
            (getattr(barrier_map, "nearest_resistance", None), "R1"),
            (getattr(barrier_map, "next_resistance", None), "R2"),
        )
        for level, label in candidates:
            item = _level_payload(level, label)
            if item and item["upper"] >= spot - 1.0:
                return item
    else:
        candidates = (
            (getattr(barrier_map, "nearest_support", None), "S1"),
            (getattr(barrier_map, "next_support", None), "S2"),
        )
        for level, label in candidates:
            item = _level_payload(level, label)
            if item and item["lower"] <= spot + 1.0:
                return item
    return None


def _invalidation(snapshot: Any, direction: str, spot: float) -> float | None:
    pa = getattr(snapshot, "price_action", None)
    candidates: list[float] = []
    if pa is not None:
        for item in (getattr(pa, "fifteen_minute", None), getattr(pa, "three_minute", None)):
            value = _num(getattr(item, "invalidation_level", None)) if item is not None else None
            if value is not None:
                candidates.append(value)
    if direction == "UP":
        valid = [x for x in candidates if x < spot]
        return max(valid) if valid else None
    valid = [x for x in candidates if x > spot]
    return min(valid) if valid else None


def new_prediction(snapshot: Any) -> dict[str, Any] | None:
    """Freeze one canonical thesis. Returns None for non-actionable/missing direction."""
    if not bool(getattr(getattr(snapshot, "market_session", None), "is_live", False)):
        return None
    simple = (getattr(snapshot, "metadata", {}) or {}).get("simple_brain") or {}
    direction = str(simple.get("direction") or "").upper()
    strength = _num(simple.get("direction_strength"), 0.0) or 0.0
    if direction not in {"UP", "DOWN"} or strength < float(CONFIG.simple_direction_min_strength):
        return None
    spot = _num(getattr(getattr(snapshot, "barrier_map", None), "current_price", None))
    if spot is None:
        spot = _num((getattr(snapshot, "nifty_quote", {}) or {}).get("last_price"))
    if spot is None:
        return None
    created_at = getattr(snapshot, "created_at", None)
    if not isinstance(created_at, datetime):
        return None
    # New predictions are allowed only in the same clean journal window.
    local_t = created_at.timetz().replace(tzinfo=None)
    if not (CONFIG.simple_decision_journal_start <= local_t <= CONFIG.simple_decision_journal_end):
        return None
    pa3 = getattr(getattr(snapshot, "price_action", None), "three_minute", None)
    atr = max(6.0, _num(getattr(pa3, "atr14", None), 10.0) or 10.0)
    barrier = _pick_barrier(snapshot, direction, spot)
    if barrier:
        # The barrier is a *watch point*, not the final target.  Closing the tracker
        # merely because price touched R1/S1 would hide the most useful question:
        # did a confirmed break actually continue?  Keep the first objective beyond
        # the locked barrier while remaining bounded by current 3m ATR.
        if direction == "UP":
            target = max(spot + max(15.0, 1.25 * atr), barrier["upper"] + max(8.0, 0.75 * atr))
        else:
            target = min(spot - max(15.0, 1.25 * atr), barrier["lower"] - max(8.0, 0.75 * atr))
    else:
        target = spot + max(15.0, 1.25 * atr) * (1 if direction == "UP" else -1)
    return {
        "prediction_id": f"{getattr(snapshot, 'snapshot_id', '')}:{direction}:{created_at.isoformat()}",
        "snapshot_id": str(getattr(snapshot, "snapshot_id", "")),
        "started_at": created_at.isoformat(),
        "direction": direction,
        "confidence": round(strength, 1),
        "start_price": round(spot, 2),
        "current_price": round(spot, 2),
        "move_points": 0.0,
        "mfe_points": 0.0,
        "mae_points": 0.0,
        "atr": round(atr, 2),
        "barrier": barrier,
        "barrier_confirmed": False,
        "barrier_confirmed_at": None,
        "target_price": round(target, 2),
        "invalidation_price": _invalidation(snapshot, direction, spot),
        "status": "STALLED",
        "closed": False,
        "last_checked_at": created_at.isoformat(),
        "checkpoints": {},
    }


def _elapsed_minutes(state: dict[str, Any], at: datetime) -> float:
    try:
        started = datetime.fromisoformat(str(state.get("started_at")))
        if started.tzinfo is None and at.tzinfo is not None:
            started = started.replace(tzinfo=at.tzinfo)
        return max(0.0, (at - started).total_seconds() / 60.0)
    except (TypeError, ValueError):
        return 0.0


def apply_completed_3m_close(
    state: dict[str, Any], *, completed_close: float | None, observed_at: datetime | None = None
) -> dict[str, Any]:
    """Confirm a locked barrier only from an already-computed completed 3m close.

    This does not fetch or calculate candles.  The normal full snapshot already has
    the completed 3m close; the tracker simply records whether that close cleared
    the barrier it locked when the thesis started.
    """
    out = dict(state)
    barrier = out.get("barrier") if isinstance(out.get("barrier"), dict) else None
    close = _num(completed_close)
    if out.get("barrier_confirmed") or not barrier or close is None:
        return out
    direction = str(out.get("direction") or "")
    confirmed = (direction == "UP" and close > float(barrier["upper"])) or (
        direction == "DOWN" and close < float(barrier["lower"])
    )
    if confirmed:
        out["barrier_confirmed"] = True
        out["barrier_confirmed_at"] = _iso(observed_at) if observed_at is not None else ""
    return out


def barrier_status(state: dict[str, Any], price: float) -> tuple[str, str]:
    barrier = state.get("barrier") if isinstance(state.get("barrier"), dict) else None
    if not barrier:
        return "CLEAR", "Paas locked barrier nahi"
    if bool(state.get("barrier_confirmed")):
        return "BROKEN", "Completed 3m break confirmed — continuation support stronger"
    direction = str(state.get("direction"))
    lower, upper = float(barrier["lower"]), float(barrier["upper"])
    atr = max(6.0, float(state.get("atr") or 10.0))
    near = max(5.0, atr * 0.6)
    if lower <= price <= upper:
        return "TESTING", "3m confirmed break ka wait"
    if direction == "UP":
        if price > upper:
            return "BREAK PENDING", "Price zone ke upar; 3m close confirm ho to continuation stronger"
        distance = lower - price
    else:
        if price < lower:
            return "BREAK PENDING", "Price zone ke neeche; 3m close confirm ho to continuation stronger"
        distance = price - upper
    if distance <= near:
        return "NEAR", "Barrier paas hai — reaction/break par dhyan"
    return "AHEAD", "Barrier aage hai"


def update_prediction(state: dict[str, Any], *, current_price: float, at: datetime) -> dict[str, Any]:
    """Update with one price observation only. No market-analysis computation occurs."""
    out = dict(state)
    if out.get("closed"):
        return out
    start = float(out["start_price"])
    price = float(current_price)
    sign = 1.0 if out.get("direction") == "UP" else -1.0
    signed_move = (price - start) * sign
    favorable = max(0.0, signed_move)
    adverse = max(0.0, -signed_move)
    out["current_price"] = round(price, 2)
    out["move_points"] = round(signed_move, 2)
    out["mfe_points"] = round(max(float(out.get("mfe_points") or 0.0), favorable), 2)
    out["mae_points"] = round(max(float(out.get("mae_points") or 0.0), adverse), 2)
    out["last_checked_at"] = at.isoformat()
    elapsed = _elapsed_minutes(out, at)
    out["elapsed_minutes"] = round(elapsed, 1)

    checkpoints = dict(out.get("checkpoints") or {})
    for horizon in (5, 15, 30):
        key = f"{horizon}m"
        if key not in checkpoints and elapsed >= horizon:
            checkpoints[key] = round(signed_move, 2)
    out["checkpoints"] = checkpoints

    invalidation = _num(out.get("invalidation_price"))
    target = _num(out.get("target_price"))
    if invalidation is not None and ((sign > 0 and price <= invalidation) or (sign < 0 and price >= invalidation)):
        status = "INVALIDATED"
        out["closed"] = True
    elif target is not None and ((sign > 0 and price >= target) or (sign < 0 and price <= target)):
        status = "TARGET MET"
        out["closed"] = True
    else:
        atr = max(6.0, float(out.get("atr") or 10.0))
        on_track = max(3.0, atr * 0.30)
        weak = max(5.0, atr * 0.45)
        if signed_move >= on_track:
            status = "ON TRACK"
        elif signed_move <= -weak:
            status = "WEAKENING"
        elif elapsed >= 9.0 and abs(signed_move) < max(3.0, atr * 0.25):
            status = "STALLED"
        elif signed_move > 0:
            status = "ON TRACK"
        elif signed_move < 0:
            status = "WEAKENING"
        else:
            status = "STALLED"
        if elapsed >= float(CONFIG.ai_move_tracker_max_minutes):
            out["closed"] = True
    out["status"] = status
    bstate, bnote = barrier_status(out, price)
    out["barrier_status"] = bstate
    out["barrier_note"] = bnote
    return out
