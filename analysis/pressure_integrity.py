"""Pressure-integrity / fake-pressure filter for One Brain Market Intelligence.

Shadow-only, zero-network quality layer.  It never changes One Brain or raw Market
Intelligence directional scores.  Its job is to answer a different question:

    "A pressure build-up is visible — how trustworthy / actionable is that pressure?"

Latency rule: move-risk warnings remain fast.  Persistence, W/M and strong-candle
patterns are *supportive* confirmation only and are never mandatory for an early
warning.  Missing supportive evidence is NO VOTE, never a negative vote.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True)
class PressureIntegrity:
    direction: str
    move_risk_state: str
    quality_state: str
    quality_score: float
    real_pressure_score: float
    fake_pressure_score: float
    move_attack_state: str
    price_response_score: float | None
    price_response_state: str
    pressure_efficiency: float | None
    barrier_state: str
    barrier_attack_score: float
    barrier_distance_points: float | None
    barrier_break_pressure: float | None
    live_candle_force_score: float | None
    live_candle_force_state: str
    family_confirmations: int
    family_oppositions: int
    supportive_pattern_score: float
    supportive_signals: tuple[str, ...]
    flip_state: str
    anchor_spot: float | None
    progress_points: float
    best_progress_points: float
    realized_move: bool
    realized_threshold_points: float
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _spot(snapshot: Any | None) -> float | None:
    if snapshot is None:
        return None
    quote = getattr(snapshot, "nifty_quote", {}) or {}
    if isinstance(quote, Mapping):
        return _num(quote.get("last_price"))
    return _num(getattr(quote, "last_price", None))


def _completed(frame: pd.DataFrame | None, tail: int = 12) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    source = frame.copy()
    if "is_complete" in source.columns:
        source = source[source["is_complete"].fillna(False).astype(bool)]
    if "timestamp" in source.columns:
        source = source.sort_values("timestamp").drop_duplicates("timestamp")
    return source.tail(tail)


def _one_minute_scale(snapshot: Any) -> float:
    source = _completed(getattr(snapshot, "candles_1m", None), tail=12)
    if source.empty or not {"high", "low"}.issubset(source.columns):
        return 6.0
    high = pd.to_numeric(source["high"], errors="coerce")
    low = pd.to_numeric(source["low"], errors="coerce")
    ranges = (high - low).dropna()
    ranges = ranges[ranges > 0]
    if ranges.empty:
        return 6.0
    return max(3.0, float(ranges.median()))


def _expert_support(experts: Mapping[str, Any], direction: str) -> tuple[int, int, float, tuple[str, ...]]:
    """Independent-family confirmation; trend is context, not a fast-pressure veto."""
    if direction not in {"BULLISH", "BEARISH"}:
        return 0, 0, 50.0, ()
    names = (
        "Structure", "Futures", "Options Flow", "Barriers / Walls",
        "Heavyweight Breadth", "Momentum Acceleration",
    )
    confirms = 0
    opposes = 0
    notes: list[str] = []
    effective_edges: list[float] = []
    sign = 1.0 if direction == "BULLISH" else -1.0
    for name in names:
        expert = experts.get(name)
        if expert is None or not bool(getattr(expert, "available", False)):
            continue
        bull = _num(getattr(expert, "bullish", None), 0.0) or 0.0
        bear = _num(getattr(expert, "bearish", None), 0.0) or 0.0
        reliability = _clamp((_num(getattr(expert, "reliability", None), 0.0) or 0.0) * 100.0) / 100.0
        freshness = _clamp((_num(getattr(expert, "freshness", None), 0.0) or 0.0) * 100.0) / 100.0
        effective = (bull - bear) * sign * max(0.25, reliability) * max(0.25, freshness)
        effective_edges.append(effective)
        if effective >= 7.0:
            confirms += 1
            notes.append(f"{name} confirms")
        elif effective <= -7.0:
            opposes += 1
            notes.append(f"{name} opposes")
    if not effective_edges:
        return confirms, opposes, 50.0, tuple(notes)
    mean_edge = sum(effective_edges) / len(effective_edges)
    family_score = _clamp(50.0 + mean_edge * 0.65 + confirms * 4.0 - opposes * 5.0)
    return confirms, opposes, family_score, tuple(notes[:5])


def _barrier_context(snapshot: Any, direction: str, scale: float) -> tuple[str, float, float | None, float | None, float | None]:
    barrier_map = getattr(snapshot, "barrier_map", None)
    if barrier_map is None or direction not in {"BULLISH", "BEARISH"}:
        return "NO DIRECTIONAL BARRIER", 50.0, None, None, None
    level = getattr(barrier_map, "nearest_resistance", None) if direction == "BULLISH" else getattr(barrier_map, "nearest_support", None)
    if level is None:
        return "NO NEAR BARRIER", 50.0, None, None, None
    distance = _num(getattr(level, "distance_points", None))
    break_pressure = _num(getattr(level, "break_pressure", None), 50.0) or 50.0
    strength = _num(getattr(level, "strength", None), 50.0) or 50.0
    if distance is None:
        proximity = 50.0
    else:
        proximity = _clamp(100.0 - distance / max(10.0, scale * 2.2) * 100.0)
    attack = _clamp(break_pressure * 0.62 + proximity * 0.38)
    near = distance is not None and distance <= max(9.0, scale * 1.15)
    if near and attack >= 65:
        state = "UNDER ATTACK"
    elif distance is not None and distance <= max(16.0, scale * 1.9) and attack >= 50:
        state = "PRESSING"
    elif strength >= 70 and break_pressure < 52:
        state = "HOLDING STRONG"
    else:
        state = "AHEAD"
    return state, attack, distance, break_pressure, strength


def _live_candle_force(snapshot: Any, direction: str, scale: float) -> tuple[float | None, str]:
    """Use already-computed live market speed; no need to wait for a candle close."""
    speed = getattr(getattr(snapshot, "barrier_map", None), "market_speed", None)
    if speed is None or direction not in {"BULLISH", "BEARISH"}:
        return None, "UNCONFIRMED"
    move = _num(getattr(speed, "move_1m_points", None))
    if move is None:
        return None, "UNCONFIRMED"
    signed = move if direction == "BULLISH" else -move
    normalized = signed / max(3.0, scale)
    score = _clamp(50.0 + normalized * 42.0)
    if score >= 72:
        state = "STRONG SUPPORT"
    elif score >= 58:
        state = "SUPPORTIVE"
    elif score <= 32:
        state = "OPPOSING"
    else:
        state = "NEUTRAL"
    return round(score, 1), state


def _price_response(snapshot: Any, previous_snapshot: Any | None, direction: str, scale: float) -> tuple[float | None, str, float | None, float]:
    current = _spot(snapshot)
    previous = _spot(previous_snapshot)
    if current is None or previous is None or direction not in {"BULLISH", "BEARISH"}:
        return None, "UNCONFIRMED", None, 0.0
    raw = current - previous
    signed = raw if direction == "BULLISH" else -raw
    score = _clamp(50.0 + signed / max(3.0, scale) * 45.0)
    efficiency = _clamp(max(0.0, signed) / max(3.0, scale) * 100.0)
    if score >= 68:
        state = "FOLLOW-THROUGH"
    elif score >= 45:
        state = "STALLED / EARLY"
    else:
        state = "OPPOSING"
    return round(score, 1), state, round(efficiency, 1), raw


def _pattern_support(snapshot: Any, direction: str) -> tuple[float, tuple[str, ...], int]:
    """W/M and candles are supportive only: absence never reduces pressure quality."""
    patterns = getattr(snapshot, "patterns", None)
    if patterns is None or direction not in {"BULLISH", "BEARISH"}:
        return 50.0, (), 0
    signals = (
        getattr(patterns, "wm_3m", None),
        getattr(patterns, "candle_3m", None),
        getattr(patterns, "candle_5m", None),
        getattr(patterns, "candle_15m", None),
    )
    bonus = 0.0
    oppose = 0.0
    support_count = 0
    notes: list[str] = []
    for item in signals:
        if item is None or str(getattr(item, "status", "")).upper() not in {"READY", "CAUTION"}:
            continue
        item_direction = str(getattr(item, "direction", "NEUTRAL") or "NEUTRAL").upper()
        name = str(getattr(item, "name", "") or "")
        if not name or name in {"NO VALID W/M", "NO IMPORTANT CANDLE"} or item_direction == "NEUTRAL":
            continue
        confidence = _num(getattr(item, "confidence", None), 0.0) or 0.0
        stage = str(getattr(item, "stage", "") or "").upper()
        amount = min(8.0, confidence / 100.0 * (7.0 if "W/M" in str(getattr(item, "family", "")) else 6.0))
        if stage == "FORMING":
            amount *= 0.65
        if item_direction == direction:
            bonus += amount
            support_count += 1
            notes.append(f"{name} supports")
        elif item_direction in {"BULLISH", "BEARISH"}:
            oppose += min(6.0, amount)
            notes.append(f"{name} opposite")
    # Cap the entire pattern family so it can never become a required/gating vote.
    score = _clamp(50.0 + min(10.0, bonus) - min(8.0, oppose))
    return round(score, 1), tuple(notes[:4]), support_count


def _move_risk(expansion: float, velocity: float | None) -> str:
    v = float(velocity or 0.0)
    if expansion >= 78 or (expansion >= 66 and v >= 12):
        return "HIGH"
    if expansion >= 58 or (expansion >= 50 and v >= 12):
        return "BUILDING"
    if expansion >= 46 or v >= 9:
        return "WATCH"
    return "NORMAL"


def calculate_pressure_integrity(
    snapshot: Any,
    previous_snapshot: Any | None,
    *,
    direction: str,
    expansion_pressure: float,
    pressure_velocity: float | None,
    persistence: int,
    coverage: float,
    conflict: str,
    experts: Mapping[str, Any],
    breakout_quality: float,
    reversal_quality: float,
    structure_event: str,
    previous_integrity: Mapping[str, Any] | None = None,
) -> PressureIntegrity:
    direction = str(direction or "MIXED").upper()
    expansion = _clamp(expansion_pressure)
    scale = _one_minute_scale(snapshot)
    current_spot = _spot(snapshot)
    previous_spot = _spot(previous_snapshot)
    previous_integrity = previous_integrity or {}

    confirms, opposes, family_score, family_notes = _expert_support(experts, direction)
    barrier_state, barrier_attack, barrier_distance, barrier_break, barrier_strength = _barrier_context(snapshot, direction, scale)
    candle_force, candle_state = _live_candle_force(snapshot, direction, scale)
    price_score, price_state, efficiency, raw_move = _price_response(snapshot, previous_snapshot, direction, scale)
    pattern_score, pattern_notes, pattern_support_count = _pattern_support(snapshot, direction)

    price_component = 50.0 if price_score is None else price_score
    candle_component = 50.0 if candle_force is None else candle_force
    quality = (
        family_score * 0.34
        + price_component * 0.24
        + barrier_attack * 0.18
        + candle_component * 0.14
        + pattern_score * 0.10
    )
    if conflict == "HIGH":
        quality -= 9.0
    elif conflict == "MEDIUM":
        quality -= 3.0
    if coverage < 55:
        quality -= 5.0
    if persistence >= 2:
        quality += 5.0
    elif persistence >= 1:
        quality += 2.0
    # Fast path: a sharp acceleration plus independent/barrier/live-price support can
    # upgrade immediately.  No W/M/candle or multi-snapshot wait is required.
    if (pressure_velocity or 0.0) >= 12 and (confirms >= 2 or barrier_attack >= 64 or candle_component >= 68):
        quality += 4.0
    quality = _clamp(quality)

    prior_direction = str(previous_integrity.get("direction") or "MIXED").upper()
    prior_quality_state = str(previous_integrity.get("quality_state") or "UNVERIFIED").upper()
    prior_anchor = _num(previous_integrity.get("anchor_spot"))
    prior_best = _num(previous_integrity.get("best_progress_points"), 0.0) or 0.0
    prior_realized = bool(previous_integrity.get("realized_move"))
    prior_expansion = None
    if previous_snapshot is not None:
        prev_mie = (getattr(previous_snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
        prior_expansion = _num(prev_mie.get("expansion_pressure"))

    active_direction = direction in {"BULLISH", "BEARISH"}
    same_wave = active_direction and prior_direction == direction and prior_anchor is not None
    if same_wave:
        anchor_spot = prior_anchor
    elif active_direction:
        anchor_spot = previous_spot if previous_spot is not None else current_spot
    else:
        anchor_spot = None

    progress = 0.0
    if anchor_spot is not None and current_spot is not None and active_direction:
        progress = (current_spot - anchor_spot) if direction == "BULLISH" else (anchor_spot - current_spot)
    best_progress = max(0.0, prior_best if same_wave else 0.0, progress)
    realized_threshold = max(6.0, scale * 0.80)
    realized = active_direction and best_progress >= realized_threshold

    established = bool(
        (prior_expansion is not None and prior_expansion >= 58)
        or persistence >= 1
        or prior_quality_state in {"BUILDING", "VERIFIED", "REALIZED", "EXHAUSTING"}
    )
    barrier_holding = barrier_strength is not None and barrier_strength >= 70 and (barrier_break or 50) < 55
    absorption = bool(
        active_direction and expansion >= 60 and established and not realized
        and (price_component <= 35 or candle_component <= 32)
        and (barrier_holding or barrier_state == "HOLDING STRONG" or opposes >= 2)
    )

    flip_state = "NONE"
    direction_flip = active_direction and prior_direction in {"BULLISH", "BEARISH"} and prior_direction != direction
    if direction_flip and ((prior_expansion or 0.0) >= 55 or prior_quality_state in {"BUILDING", "VERIFIED", "REALIZED", "EXHAUSTING"}):
        if quality >= 66 and confirms >= 2 and price_component >= 56:
            flip_state = "FLIP CONFIRMED"
        else:
            flip_state = "FLIP WATCH"

    collapsed = prior_expansion is not None and prior_expansion >= 68 and expansion <= prior_expansion - 18
    if flip_state == "FLIP CONFIRMED":
        quality_state = "FLIP CONFIRMED"
    elif flip_state == "FLIP WATCH":
        quality_state = "FLIP WATCH"
    elif absorption:
        quality_state = "ABSORPTION RISK"
    elif collapsed and not prior_realized and not realized:
        quality_state = "BUILD-UP FAILED"
    elif collapsed and (prior_realized or realized):
        quality_state = "EXHAUSTING"
    elif realized and quality >= 62:
        quality_state = "REALIZED"
    elif quality >= 70 and confirms >= 2 and (price_component >= 55 or barrier_attack >= 65 or candle_component >= 65):
        quality_state = "VERIFIED"
    elif quality >= 56 and active_direction:
        quality_state = "BUILDING"
    else:
        quality_state = "UNVERIFIED"

    # Fake score rises only after pressure has had a chance to respond.  A first fast
    # warning is intentionally not called fake just because price confirmation is early.
    fake = 50.0 - quality * 0.42 + opposes * 8.0
    if established and expansion >= 58 and price_component <= 40:
        fake += 14.0
    if absorption:
        fake += 18.0
    if realized:
        fake -= 24.0
    if quality_state == "BUILD-UP FAILED":
        fake += 20.0
    fake = _clamp(fake)
    real = _clamp(100.0 - fake + (8.0 if realized else 0.0))

    if quality_state in {"ABSORPTION RISK", "BUILD-UP FAILED"}:
        attack_state = "REJECTED / FAKE RISK"
    elif quality_state in {"FLIP WATCH", "FLIP CONFIRMED"}:
        attack_state = quality_state
    elif ("BREAKOUT" in str(structure_event).upper() or breakout_quality >= 68) and expansion >= 58:
        attack_state = "BREAK / EXPANSION"
    elif active_direction and barrier_state == "UNDER ATTACK" and expansion >= 52:
        attack_state = "ATTACK"
    elif _move_risk(expansion, pressure_velocity) in {"HIGH", "BUILDING"}:
        attack_state = "BUILDING"
    elif _move_risk(expansion, pressure_velocity) == "WATCH":
        attack_state = "WATCH"
    else:
        attack_state = "NORMAL"

    reasons: list[str] = []
    reasons.append(f"Independent families {confirms} confirm / {opposes} oppose")
    if price_score is not None:
        reasons.append(f"Price response {price_state} {price_score:.0f}/100")
    if barrier_distance is not None:
        reasons.append(f"Barrier {barrier_state} · {barrier_distance:.1f} pts · attack {barrier_attack:.0f}/100")
    if candle_force is not None and candle_state != "NEUTRAL":
        reasons.append(f"Live candle force {candle_state} {candle_force:.0f}/100")
    reasons.extend(pattern_notes)
    if realized:
        reasons.append(f"Pressure already realized {best_progress:.1f} pts from wave anchor")
    if flip_state != "NONE":
        reasons.append(("Prior impulse realized; " if prior_realized else "Prior pressure not realized; ") + flip_state.lower())

    supportive = list(pattern_notes)
    if candle_state in {"STRONG SUPPORT", "SUPPORTIVE"}:
        supportive.append("Live candle force supports")
    if barrier_state in {"UNDER ATTACK", "PRESSING"}:
        supportive.append(f"Barrier {barrier_state.lower()}")

    cautions = [
        "W/M and candle patterns are supportive only; absence never blocks an early warning",
        "Pressure quality is an evidence score, not a win probability",
    ]
    if quality_state in {"UNVERIFIED", "FLIP WATCH"}:
        cautions.append("Direction is precautionary until price/evidence confirms")

    return PressureIntegrity(
        direction=direction,
        move_risk_state=_move_risk(expansion, pressure_velocity),
        quality_state=quality_state,
        quality_score=round(quality, 1),
        real_pressure_score=round(real, 1),
        fake_pressure_score=round(fake, 1),
        move_attack_state=attack_state,
        price_response_score=price_score,
        price_response_state=price_state,
        pressure_efficiency=efficiency,
        barrier_state=barrier_state,
        barrier_attack_score=round(barrier_attack, 1),
        barrier_distance_points=round(barrier_distance, 1) if barrier_distance is not None else None,
        barrier_break_pressure=round(barrier_break, 1) if barrier_break is not None else None,
        live_candle_force_score=candle_force,
        live_candle_force_state=candle_state,
        family_confirmations=int(confirms),
        family_oppositions=int(opposes),
        supportive_pattern_score=pattern_score,
        supportive_signals=tuple(dict.fromkeys(supportive))[:5],
        flip_state=flip_state,
        anchor_spot=round(anchor_spot, 2) if anchor_spot is not None else None,
        progress_points=round(progress, 2),
        best_progress_points=round(best_progress, 2),
        realized_move=bool(realized),
        realized_threshold_points=round(realized_threshold, 2),
        reasons=tuple(dict.fromkeys(reasons))[:7],
        cautions=tuple(cautions[:3]),
    )
