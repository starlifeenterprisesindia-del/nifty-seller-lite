"""Simple One-Brain decision layer.

The app already has rich raw evidence.  This module deliberately *reduces* that
information to four practical blocks instead of creating another collection of
independent gates:

1. Trend / regime (15m + 3m price action + 15m indicators)
2. Options flow (1m/3m/5m blended by option_intelligence)
3. Participation (NIFTY futures volume + Top-9; Big Player is confirmation only)
4. Barrier / entry state (hold -> attack -> broken / room)

VIX, FII/DII, news, Greeks, W/M and special candles remain useful context, risk,
or strike-quality inputs, but they are not separate direction votes here.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from config import CONFIG


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _normalise_triplet(bull: float, bear: float, neutral: float) -> tuple[float, float, float]:
    values = [max(0.0, bull), max(0.0, bear), max(0.0, neutral)]
    total = sum(values)
    if total <= 0:
        return 0.0, 0.0, 100.0
    result = [round(v / total * 100.0, 1) for v in values]
    result[2] = round(100.0 - result[0] - result[1], 1)
    return result[0], result[1], result[2]


def _event_direction(text: str) -> str:
    text = str(text or "").upper()
    if "BREAKDOWN CONFIRMED" in text or "BEARISH CONTINUATION" in text:
        return "DOWN"
    if "BREAKOUT CONFIRMED" in text or "BULLISH CONTINUATION" in text:
        return "UP"
    if "RESISTANCE REJECTION" in text:
        return "DOWN"
    if "SUPPORT HOLD" in text or "SUPPORT REJECTION" in text:
        return "UP"
    return "MIXED"


def _structure_direction(text: str) -> str:
    text = str(text or "").upper()
    if "BEARISH" in text and "BULLISH" not in text:
        return "DOWN"
    if "BULLISH" in text and "BEARISH" not in text:
        return "UP"
    return "RANGE" if "RANGE" in text else "MIXED"


def _regime(snapshot: Any) -> tuple[str, str]:
    pa15 = snapshot.price_action.fifteen_minute
    pa3 = snapshot.price_action.three_minute
    event15 = str(pa15.event or "").upper()
    event3 = str(pa3.event or "").upper()
    s15 = _structure_direction(pa15.structure)
    s3 = _structure_direction(pa3.structure)

    if "BREAKDOWN CONFIRMED" in event15:
        if s3 == "DOWN" or _event_direction(event3) == "DOWN":
            return "BREAKDOWN CONTINUATION", "DOWN"
        return "BREAKDOWN STARTING", "DOWN"
    if "BREAKOUT CONFIRMED" in event15:
        if s3 == "UP" or _event_direction(event3) == "UP":
            return "BREAKOUT CONTINUATION", "UP"
        return "BREAKOUT STARTING", "UP"
    if s15 == s3 == "DOWN":
        if "RECOVERY" in event3:
            return "PULLBACK IN DOWNTREND", "DOWN"
        return "DOWNTREND", "DOWN"
    if s15 == s3 == "UP":
        if "PULLBACK" in event3:
            return "PULLBACK IN UPTREND", "UP"
        return "UPTREND", "UP"
    if s15 == "DOWN" and s3 == "UP":
        return "PULLBACK IN DOWNTREND", "DOWN"
    if s15 == "UP" and s3 == "DOWN":
        return "PULLBACK IN UPTREND", "UP"
    if s15 == "RANGE" and s3 == "RANGE":
        return "RANGE / COMPRESSION", "RANGE"
    return "TRANSITION", "MIXED"


def _volume_triplet(snapshot: Any) -> tuple[float, float, float] | None:
    volume = getattr(snapshot, "volume", None)
    if volume is None:
        return None
    bull = bear = neutral = 0.0
    weight = 0.0
    for item, share in ((getattr(volume, "three_minute", None), 0.6), (getattr(volume, "fifteen_minute", None), 0.4)):
        if item is None or str(getattr(item, "status", "")).upper() != "READY":
            continue
        confidence = _clamp(_num(getattr(item, "confidence", 0.0)))
        text = f"{getattr(item, 'move_support', '')} {getattr(item, 'price_direction', '')}".upper()
        if "BEARISH" in text or "DOWN" in text:
            bear += confidence * share
        elif "BULLISH" in text or "UP" in text:
            bull += confidence * share
        else:
            neutral += confidence * share
        weight += share
    if weight <= 0:
        return None
    return _normalise_triplet(bull / weight, bear / weight, neutral / weight)


def _top9_triplet(snapshot: Any) -> tuple[float, float, float] | None:
    heavy = getattr(snapshot, "heavyweights", None)
    if heavy is None:
        return None
    up_weight = down_weight = flat_weight = 0.0
    used = 0.0
    for row in getattr(heavy, "rows", ()) or ():
        # Prefer the live/recent 3m move.  Fall back to current-session change when
        # the broker omitted an intraday timestamp for a constituent quote.
        move = getattr(row, "change_3m_pct", None)
        if move is None:
            move = getattr(row, "change_pct", None)
        if move is None:
            continue
        w = max(0.0, _num(getattr(row, "official_weight_pct", 0.0)))
        if w <= 0:
            continue
        used += w
        if float(move) > 0.03:
            up_weight += w
        elif float(move) < -0.03:
            down_weight += w
        else:
            flat_weight += w
    if used <= 0:
        return None
    return _normalise_triplet(up_weight, down_weight, flat_weight)


def _participation(snapshot: Any) -> tuple[float, float, float, float, tuple[str, ...]]:
    sources: list[tuple[tuple[float, float, float], float]] = []
    notes: list[str] = []
    volume = _volume_triplet(snapshot)
    if volume is not None:
        sources.append((volume, 0.55))
        notes.append(f"Futures volume B/D/N {volume[0]:.0f}/{volume[1]:.0f}/{volume[2]:.0f}")
    top9 = _top9_triplet(snapshot)
    if top9 is not None:
        sources.append((top9, 0.45))
        notes.append(f"Top-9 B/D/N {top9[0]:.0f}/{top9[1]:.0f}/{top9[2]:.0f}")
    if not sources:
        return 0.0, 0.0, 100.0, 0.0, ("Participation unavailable",)
    total_w = sum(w for _, w in sources)
    bull = sum(v[0] * w for v, w in sources) / total_w
    bear = sum(v[1] * w for v, w in sources) / total_w
    neutral = sum(v[2] * w for v, w in sources) / total_w

    # Big Player is a confirmation *inside* participation, never a fifth vote.
    activity = getattr(snapshot, "big_player_activity", None)
    if activity is not None and _num(getattr(activity, "score", 0.0)) >= 60:
        strength = min(20.0, (_num(activity.score) - 50.0) * 0.4)
        if str(getattr(activity, "direction", "")).upper() == "BUYING":
            bull += strength
            notes.append(f"Big Player BUYING {_num(activity.score):.0f}/100 confirms")
        elif str(getattr(activity, "direction", "")).upper() == "SELLING":
            bear += strength
            notes.append(f"Big Player SELLING {_num(activity.score):.0f}/100 confirms")
    bull, bear, neutral = _normalise_triplet(bull, bear, neutral)
    confidence = min(100.0, 55.0 + 20.0 * len(sources))
    return bull, bear, neutral, confidence, tuple(notes[:3])


def _barrier_state(snapshot: Any, direction: str) -> dict[str, Any]:
    barrier_map = getattr(snapshot, "barrier_map", None)
    pa3 = snapshot.price_action.three_minute
    pa15 = snapshot.price_action.fifteen_minute
    atr = max(6.0, _num(getattr(pa3, "atr14", 0.0), 10.0))
    spot = _num(getattr(barrier_map, "current_price", None), _num(snapshot.nifty_quote.get("last_price")))

    if barrier_map is None or str(getattr(barrier_map, "status", "")).upper() not in {"READY", "REFERENCE ONLY"}:
        return {"state": "UNKNOWN", "score": 45.0, "trigger": "Barrier data ka wait", "next_level": None, "note": "Barrier unavailable"}

    if direction == "DOWN":
        level = getattr(barrier_map, "nearest_support", None)
        next_level = getattr(barrier_map, "next_support", None)
        event_confirmed = "BREAKDOWN CONFIRMED" in str(getattr(pa15, "event", "")).upper()
        fast_bear = _event_direction(getattr(pa3, "event", "")) == "DOWN" or _structure_direction(getattr(pa3, "structure", "")) == "DOWN"
        if level is None:
            return {"state": "OPEN ROOM", "score": 78.0, "trigger": "Bearish continuation / pullback", "next_level": None, "note": "Nearest support unresolved"}
        distance = max(0.0, _num(getattr(level, "distance_points", 0.0)))
        strength = _num(getattr(level, "strength", 50.0))
        pressure = _num(getattr(level, "break_pressure", 50.0))
        state = str(getattr(level, "state", "")).upper()
        broken = "BROKEN" in state or (spot < _num(getattr(level, "lower", spot)) - max(0.75, atr * 0.08))
        attack = pressure >= strength - 6 and (event_confirmed or fast_bear)
        if broken:
            score, label = 92.0, "BROKEN"
            trigger = f"Support {_num(level.lower):,.0f} ke neeche break confirmed"
        elif attack:
            score, label = 72.0, "UNDER ATTACK"
            trigger = f"3m close < {_num(level.lower):,.0f} par continuation ready"
        elif distance <= max(8.0, atr * 0.9):
            score, label = 38.0, "HOLDING / NEAR"
            trigger = f"Support {_num(level.lower):,.0f} break ya pullback ka wait"
        else:
            score, label = 82.0, "OPEN ROOM"
            trigger = "Bearish continuation; nearest support tak room"
        return {
            "state": label, "score": score, "trigger": trigger,
            "next_level": (_num(next_level.midpoint) if next_level is not None else None),
            "distance": distance, "strength": strength, "break_pressure": pressure,
            "note": f"Support {level.lower:,.0f}-{level.upper:,.0f} · strength {strength:.0f} · break {pressure:.0f}",
        }

    if direction == "UP":
        level = getattr(barrier_map, "nearest_resistance", None)
        next_level = getattr(barrier_map, "next_resistance", None)
        event_confirmed = "BREAKOUT CONFIRMED" in str(getattr(pa15, "event", "")).upper()
        fast_bull = _event_direction(getattr(pa3, "event", "")) == "UP" or _structure_direction(getattr(pa3, "structure", "")) == "UP"
        if level is None:
            return {"state": "OPEN ROOM", "score": 78.0, "trigger": "Bullish continuation / pullback", "next_level": None, "note": "Nearest resistance unresolved"}
        distance = max(0.0, _num(getattr(level, "distance_points", 0.0)))
        strength = _num(getattr(level, "strength", 50.0))
        pressure = _num(getattr(level, "break_pressure", 50.0))
        state = str(getattr(level, "state", "")).upper()
        broken = "BROKEN" in state or (spot > _num(getattr(level, "upper", spot)) + max(0.75, atr * 0.08))
        attack = pressure >= strength - 6 and (event_confirmed or fast_bull)
        if broken:
            score, label = 92.0, "BROKEN"
            trigger = f"Resistance {_num(level.upper):,.0f} ke upar break confirmed"
        elif attack:
            score, label = 72.0, "UNDER ATTACK"
            trigger = f"3m close > {_num(level.upper):,.0f} par continuation ready"
        elif distance <= max(8.0, atr * 0.9):
            score, label = 38.0, "HOLDING / NEAR"
            trigger = f"Resistance {_num(level.upper):,.0f} break ya pullback ka wait"
        else:
            score, label = 82.0, "OPEN ROOM"
            trigger = "Bullish continuation; nearest resistance tak room"
        return {
            "state": label, "score": score, "trigger": trigger,
            "next_level": (_num(next_level.midpoint) if next_level is not None else None),
            "distance": distance, "strength": strength, "break_pressure": pressure,
            "note": f"Resistance {level.lower:,.0f}-{level.upper:,.0f} · strength {strength:.0f} · break {pressure:.0f}",
        }

    return {"state": "RANGE", "score": 60.0, "trigger": "Range dono taraf confirm ho", "next_level": None, "note": "Range entry needs two-sided room"}


def _direction_alignment(score_triplet: tuple[float, float, float], direction: str) -> float:
    if direction == "UP":
        return score_triplet[0]
    if direction == "DOWN":
        return score_triplet[1]
    return score_triplet[2]


def calculate_simple_brain(snapshot: Any, future: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return one simple direction + entry decision from existing canonical evidence."""
    regime, regime_hint = _regime(snapshot)
    core = snapshot.core_evidence
    trend = _normalise_triplet(_num(core.bullish_score), _num(core.bearish_score), _num(core.range_score))
    options_obj = snapshot.option_intelligence
    options = _normalise_triplet(_num(options_obj.bullish_score), _num(options_obj.bearish_score), _num(options_obj.range_score))
    part_bull, part_bear, part_neutral, part_conf, part_notes = _participation(snapshot)
    participation = (part_bull, part_bear, part_neutral)

    # Direction is normalized by *available* core blocks.  Optional features do not
    # consume denominator weight and therefore cannot make a valid setup mathematically
    # incapable of reaching an entry threshold.
    blocks: list[tuple[str, tuple[float, float, float], float, bool]] = [
        ("Trend", trend, 40.0, _num(getattr(core, "confidence", 0.0)) > 0),
        ("Options", options, 25.0, str(getattr(options_obj, "status", "")).upper() not in {"UNAVAILABLE"}),
        ("Participation", participation, 20.0, part_conf > 0),
    ]
    available = sum(weight for _, _, weight, ready in blocks if ready) or 1.0
    bull = sum(scores[0] * weight for _, scores, weight, ready in blocks if ready) / available
    bear = sum(scores[1] * weight for _, scores, weight, ready in blocks if ready) / available
    neutral = sum(scores[2] * weight for _, scores, weight, ready in blocks if ready) / available

    # A confirmed 15m break is regime evidence, not a separate indicator.  It nudges
    # the same trend block so a RANGE swing label cannot erase an actual break event.
    if regime_hint == "DOWN" and "BREAKDOWN" in regime:
        bear += 8.0
        neutral = max(0.0, neutral - 5.0)
    elif regime_hint == "UP" and "BREAKOUT" in regime:
        bull += 8.0
        neutral = max(0.0, neutral - 5.0)
    bull, bear, neutral = _normalise_triplet(bull, bear, neutral)

    if bull >= 55 and bull >= bear + 8:
        direction = "UP"
        direction_strength = bull
    elif bear >= 55 and bear >= bull + 8:
        direction = "DOWN"
        direction_strength = bear
    elif neutral >= 52 and neutral >= max(bull, bear) - 3:
        direction = "RANGE"
        direction_strength = neutral
    elif regime_hint in {"UP", "DOWN"} and max(bull, bear) >= 48:
        direction = regime_hint
        direction_strength = bull if direction == "UP" else bear
    else:
        direction = "MIXED"
        direction_strength = max(bull, bear, neutral)

    # Avoid a regime label contradicting a clearly stronger fresh composite.
    if direction in {"UP", "DOWN"}:
        if direction == "UP" and regime_hint == "UP":
            pass
        elif direction == "DOWN" and regime_hint == "DOWN":
            pass
        elif regime == "TRANSITION":
            regime = f"{direction} TRANSITION"

    barrier = _barrier_state(snapshot, direction)
    option_align = _direction_alignment(options, direction)
    participation_align = _direction_alignment(participation, direction)
    barrier_score = _num(barrier.get("score"), 45.0)
    entry_readiness = round(_clamp(
        direction_strength * 0.45
        + option_align * 0.25
        + participation_align * 0.15
        + barrier_score * 0.15
    ), 1)

    # RSI is a chase-risk modifier only.  It never flips direction by itself.
    rsi = _num(getattr(snapshot.indicators.three_minute, "rsi14", None), -1.0)
    risk_notes: list[str] = []
    if direction == "DOWN" and 0 <= rsi <= 32:
        risk_notes.append(f"3m RSI {rsi:.1f} oversold — bounce/chase risk; pullback ya confirmed break better")
        entry_readiness = max(0.0, entry_readiness - 4.0)
    if direction == "UP" and rsi >= 68:
        risk_notes.append(f"3m RSI {rsi:.1f} overbought — pullback/chase risk")
        entry_readiness = max(0.0, entry_readiness - 4.0)

    future = future or {}
    future_next = str(future.get("next_direction") or "MIXED").upper()
    future_score = max(_num(future.get("up_15m")), _num(future.get("down_15m")), _num(future.get("range_15m")))
    if future_next in {"UP", "DOWN"} and direction in {"UP", "DOWN"} and future_next != direction and future_score >= 55:
        risk_notes.append(f"Future Brain opposite {future_next} risk {future_score:.0f}% — warning only, hard veto nahi")
        entry_readiness = max(0.0, entry_readiness - 6.0)

    preferred = {
        "DOWN": ("CE SELL", "PE BUY"),
        "UP": ("PE SELL", "CE BUY"),
        "RANGE": ("IRON CONDOR",),
    }.get(direction, ())
    action = preferred[0] if preferred else "WAIT"

    hard_blockers: list[str] = []
    if not snapshot.market_session.is_live:
        hard_blockers.append("Market is not live")
    for feed_name in ("quotes", "candles", "option_chain"):
        feed = snapshot.feed_status.get(feed_name)
        if feed is None or str(getattr(feed, "use_state", "")).upper() != "LIVE":
            hard_blockers.append(f"{feed_name} not confirmed live")
    progression = snapshot.feed_status.get("price_progression")
    if progression is not None and not bool(getattr(progression, "ok", True)):
        hard_blockers.append("NIFTY price series not progressing")

    barrier_state = str(barrier.get("state") or "UNKNOWN")
    if hard_blockers:
        entry_state = "DATA WAIT"
        final_action = "WAIT"
        instruction = hard_blockers[0]
    elif direction not in {"UP", "DOWN", "RANGE"} or direction_strength < CONFIG.simple_direction_min_strength:
        entry_state = "NO CLEAR EDGE"
        final_action = "WAIT"
        instruction = "Direction clear nahi — no trade"
    elif direction == "RANGE":
        if entry_readiness >= 68 and barrier_state == "RANGE":
            entry_state = "READY"
            final_action = action
            instruction = "Range balanced ho to protected Iron Condor"
        else:
            entry_state = "WAIT FOR RANGE"
            final_action = "WAIT"
            instruction = "Dono taraf room confirm hone ka wait"
    elif barrier_state == "HOLDING / NEAR":
        entry_state = "WAIT FOR BREAK / PULLBACK"
        final_action = "WAIT"
        instruction = str(barrier.get("trigger") or "Nearest barrier ka wait")
    elif barrier_state == "UNDER ATTACK":
        # Arm the setup before the break, but do not print TAKE NOW while the
        # displayed trigger itself still says a 3m close beyond the barrier is needed.
        entry_state = "READY / BREAK TRIGGER"
        final_action = "WAIT"
        instruction = str(barrier.get("trigger") or "Break trigger armed")
    elif entry_readiness >= CONFIG.simple_entry_ready_score:
        entry_state = "TAKE NOW" if barrier_state in {"BROKEN", "OPEN ROOM"} else "READY"
        final_action = action
        instruction = str(barrier.get("trigger") or f"{action} protected setup ready")
    elif entry_readiness >= CONFIG.simple_entry_watch_score:
        entry_state = "READY / CONFIRM"
        final_action = "WAIT"
        instruction = str(barrier.get("trigger") or "Ek clean trigger ka wait")
    else:
        entry_state = "WAIT CONFIRMATION"
        final_action = "WAIT"
        instruction = "Direction hai, entry alignment abhi weak hai"

    reasons = [
        f"Trend B/D/N {trend[0]:.0f}/{trend[1]:.0f}/{trend[2]:.0f}",
        f"Options B/D/N {options[0]:.0f}/{options[1]:.0f}/{options[2]:.0f}",
        f"Participation B/D/N {participation[0]:.0f}/{participation[1]:.0f}/{participation[2]:.0f}",
        str(barrier.get("note") or "Barrier context unavailable"),
    ]
    return {
        "engine": "SIMPLE_ONE_BRAIN_V1",
        "regime": regime,
        "direction": direction,
        "direction_strength": round(direction_strength, 1),
        "scores": {"up": bull, "down": bear, "range": neutral},
        "blocks": {
            "trend": {"weight": 40, "bullish": trend[0], "bearish": trend[1], "neutral": trend[2]},
            "options": {"weight": 25, "bullish": options[0], "bearish": options[1], "neutral": options[2], "confidence": _num(options_obj.confidence)},
            "participation": {"weight": 20, "bullish": participation[0], "bearish": participation[1], "neutral": participation[2], "confidence": part_conf},
            "barrier_entry": {"weight": 15, **barrier},
        },
        "preferred_strategies": preferred,
        "candidate_action": action,
        "entry_readiness": round(entry_readiness, 1),
        "entry_state": entry_state,
        "final_action": final_action,
        "trigger": str(barrier.get("trigger") or instruction),
        "instruction": instruction,
        "next_level": barrier.get("next_level"),
        "risk_notes": tuple(risk_notes[:3]),
        "reasons": tuple(reasons[:4]),
        "participation_notes": part_notes,
        "hard_blockers": tuple(dict.fromkeys(hard_blockers)),
        "future_advisory": {
            "next_direction": future_next,
            "score": round(future_score, 1),
            "note": "Future Brain advisory only; Current Simple Brain ko ordinary MIXED state me block nahi karta.",
        },
        "status": "REFERENCE ONLY" if not snapshot.market_session.is_live else "READY",
    }
