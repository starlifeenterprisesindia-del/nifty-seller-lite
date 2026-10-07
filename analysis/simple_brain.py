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

from datetime import datetime
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
    # Participation must come from recent intraday movement, not a stale/session
    # change fallback.  On weekends/after-hours the broker can return perfectly
    # valid last prices with 0.0% session change; treating those nine flats as
    # 100% neutral was diluting the live Trend block.  Missing recent Top-9 is
    # therefore NO VOTE, exactly like missing option-flow continuity.
    status = str(getattr(heavy, "status", "") or "").upper()
    if status not in {"READY", "CAUTION"}:
        return None
    up_weight = down_weight = flat_weight = 0.0
    used = 0.0
    covered = max(0.0, _num(getattr(heavy, "covered_weight_pct", 0.0)))
    for row in getattr(heavy, "rows", ()) or ():
        move = getattr(row, "change_3m_pct", None)
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
    # Require broad enough recent coverage. A couple of fresh constituents are
    # useful diagnostics but not a 20%-weight participation vote.
    if used <= 0 or (covered > 0 and used / covered < 0.80):
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
        # Missing evidence is not a RANGE vote.  Availability is carried by the
        # confidence field and the block is removed from the scoring denominator.
        return 0.0, 0.0, 0.0, 0.0, ("Participation unavailable",)
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


def _barrier_state(
    snapshot: Any,
    direction: str,
    previous_barrier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    barrier_map = getattr(snapshot, "barrier_map", None)
    pa3 = snapshot.price_action.three_minute
    pa15 = snapshot.price_action.fifteen_minute
    atr = max(6.0, _num(getattr(pa3, "atr14", 0.0), 10.0))
    spot = _num(getattr(barrier_map, "current_price", None), _num(snapshot.nifty_quote.get("last_price")))
    indicators = getattr(snapshot, "indicators", None)
    three_minute_indicators = getattr(indicators, "three_minute", None)
    completed_3m_close = _num(getattr(three_minute_indicators, "close", None), spot)
    snapshot_at = getattr(snapshot, "created_at", None)

    # Preserve the original break trigger while it is still valid.  This prevents
    # a moving nearest-support/resistance calculation from moving the goalpost
    # after the user has already been told which completed 3m close will confirm.
    prior = previous_barrier if isinstance(previous_barrier, dict) else {}
    armed_direction = str(prior.get("armed_direction") or "").upper()
    armed_level = prior.get("armed_level")
    armed_lower = prior.get("armed_lower")
    armed_upper = prior.get("armed_upper")
    armed_at = prior.get("armed_at")
    armed_valid = False
    if armed_direction == direction and armed_level is not None and armed_at:
        try:
            stamp = datetime.fromisoformat(str(armed_at))
            now = snapshot_at
            if now is None:
                raise ValueError("Snapshot time unavailable")
            if stamp.tzinfo is None and getattr(now, "tzinfo", None) is not None:
                stamp = stamp.replace(tzinfo=now.tzinfo)
            age_minutes = (now - stamp).total_seconds() / 60.0
            armed_valid = 0 <= age_minutes <= float(CONFIG.simple_armed_trigger_minutes)
        except (TypeError, ValueError):
            armed_valid = False

    break_pad = max(0.75, atr * 0.08)
    invalidate_pad = max(2.0, atr * 0.35)

    # A frozen/armed trigger must not turn into an immediate entry straight into
    # a newly detected barrier.  The old level can be genuinely broken while a
    # fresh support/resistance zone has already formed only a few points away.
    # That exact pattern produced the 29-Sep 13:39 false CE SELL: the old
    # 22,646 support broke, but the live map already had fresh support around
    # 22,620-22,632.  Re-use the existing barrier map only; no API call, history
    # read or heavy computation is added to the critical path.
    min_break_room = max(
        float(CONFIG.simple_break_room_min_points),
        atr * float(CONFIG.simple_break_room_atr_multiple),
    )

    if armed_valid and direction == "DOWN":
        armed_level_f = _num(armed_level)
        upper_f = _num(armed_upper, armed_level_f)
        if completed_3m_close < armed_level_f - break_pad:
            current_support = getattr(barrier_map, "nearest_support", None) if barrier_map is not None else None
            current_room = None
            if current_support is not None:
                support_upper = _num(getattr(current_support, "upper", None), 0.0)
                if support_upper > 0:
                    current_room = max(0.0, completed_3m_close - support_upper)
                else:
                    distance = getattr(current_support, "distance_points", None)
                    if distance is not None:
                        current_room = max(0.0, _num(distance))
            if current_room is not None and current_room < min_break_room:
                return {
                    "state": "HOLDING / NEAR",
                    "score": 46.0,
                    "trigger": (
                        f"Armed support {armed_level_f:,.0f} break confirmed, "
                        f"par next support bahut paas — room ka wait"
                    ),
                    "next_level": _num(getattr(current_support, "midpoint", None), 0.0) or None,
                    "distance": round(current_room, 2),
                    "required_room": round(min_break_room, 2),
                    "note": (
                        f"Old support {armed_level_f:,.0f} broken; fresh support only "
                        f"{current_room:.1f} pts away (< {min_break_room:.1f})"
                    ),
                    "armed_direction": "DOWN",
                    "armed_level": armed_level_f,
                    "armed_lower": _num(armed_lower, armed_level_f),
                    "armed_upper": upper_f,
                    "armed_at": armed_at,
                    "armed_from_previous": True,
                    "break_confirmed_no_room": True,
                }
            return {
                "state": "BROKEN",
                "score": 94.0,
                "trigger": f"Armed support {armed_level_f:,.0f} ka completed 3m break confirmed",
                "next_level": _num(getattr(current_support, "midpoint", None), 0.0) or None,
                "note": f"Previous armed support {armed_level_f:,.0f} broken; goalpost freeze active",
                "armed_direction": "DOWN",
                "armed_level": armed_level_f,
                "armed_lower": _num(armed_lower, armed_level_f),
                "armed_upper": upper_f,
                "armed_at": armed_at,
                "armed_from_previous": True,
            }
        if completed_3m_close <= upper_f + invalidate_pad:
            return {
                "state": "UNDER ATTACK",
                "score": max(72.0, _num(prior.get("score"), 72.0)),
                "trigger": f"3m close < {armed_level_f:,.0f} par continuation ready",
                "next_level": prior.get("next_level"),
                "note": f"Armed support {armed_level_f:,.0f} retained until break/invalidation",
                "armed_direction": "DOWN",
                "armed_level": armed_level_f,
                "armed_lower": _num(armed_lower, armed_level_f),
                "armed_upper": upper_f,
                "armed_at": armed_at,
                "armed_from_previous": True,
            }

    if armed_valid and direction == "UP":
        armed_level_f = _num(armed_level)
        lower_f = _num(armed_lower, armed_level_f)
        if completed_3m_close > armed_level_f + break_pad:
            current_resistance = getattr(barrier_map, "nearest_resistance", None) if barrier_map is not None else None
            current_room = None
            if current_resistance is not None:
                resistance_lower = _num(getattr(current_resistance, "lower", None), 0.0)
                if resistance_lower > 0:
                    current_room = max(0.0, resistance_lower - completed_3m_close)
                else:
                    distance = getattr(current_resistance, "distance_points", None)
                    if distance is not None:
                        current_room = max(0.0, _num(distance))
            if current_room is not None and current_room < min_break_room:
                return {
                    "state": "HOLDING / NEAR",
                    "score": 46.0,
                    "trigger": (
                        f"Armed resistance {armed_level_f:,.0f} break confirmed, "
                        f"par next resistance bahut paas — room ka wait"
                    ),
                    "next_level": _num(getattr(current_resistance, "midpoint", None), 0.0) or None,
                    "distance": round(current_room, 2),
                    "required_room": round(min_break_room, 2),
                    "note": (
                        f"Old resistance {armed_level_f:,.0f} broken; fresh resistance only "
                        f"{current_room:.1f} pts away (< {min_break_room:.1f})"
                    ),
                    "armed_direction": "UP",
                    "armed_level": armed_level_f,
                    "armed_lower": lower_f,
                    "armed_upper": _num(armed_upper, armed_level_f),
                    "armed_at": armed_at,
                    "armed_from_previous": True,
                    "break_confirmed_no_room": True,
                }
            return {
                "state": "BROKEN",
                "score": 94.0,
                "trigger": f"Armed resistance {armed_level_f:,.0f} ka completed 3m break confirmed",
                "next_level": _num(getattr(current_resistance, "midpoint", None), 0.0) or None,
                "note": f"Previous armed resistance {armed_level_f:,.0f} broken; goalpost freeze active",
                "armed_direction": "UP",
                "armed_level": armed_level_f,
                "armed_lower": lower_f,
                "armed_upper": _num(armed_upper, armed_level_f),
                "armed_at": armed_at,
                "armed_from_previous": True,
            }
        if completed_3m_close >= lower_f - invalidate_pad:
            return {
                "state": "UNDER ATTACK",
                "score": max(72.0, _num(prior.get("score"), 72.0)),
                "trigger": f"3m close > {armed_level_f:,.0f} par continuation ready",
                "next_level": prior.get("next_level"),
                "note": f"Armed resistance {armed_level_f:,.0f} retained until break/invalidation",
                "armed_direction": "UP",
                "armed_level": armed_level_f,
                "armed_lower": lower_f,
                "armed_upper": _num(armed_upper, armed_level_f),
                "armed_at": armed_at,
                "armed_from_previous": True,
            }

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
        broken = "BROKEN" in state or (completed_3m_close < _num(getattr(level, "lower", spot)) - break_pad)
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
        result = {
            "state": label, "score": score, "trigger": trigger,
            "next_level": (_num(next_level.midpoint) if next_level is not None else None),
            "distance": distance, "strength": strength, "break_pressure": pressure,
            "note": f"Support {level.lower:,.0f}-{level.upper:,.0f} · strength {strength:.0f} · break {pressure:.0f}",
        }
        if label == "UNDER ATTACK":
            result.update(
                armed_direction="DOWN",
                armed_level=_num(level.lower),
                armed_lower=_num(level.lower),
                armed_upper=_num(level.upper),
                armed_at=snapshot_at.isoformat() if snapshot_at is not None else None,
            )
        return result

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
        broken = "BROKEN" in state or (completed_3m_close > _num(getattr(level, "upper", spot)) + break_pad)
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
        result = {
            "state": label, "score": score, "trigger": trigger,
            "next_level": (_num(next_level.midpoint) if next_level is not None else None),
            "distance": distance, "strength": strength, "break_pressure": pressure,
            "note": f"Resistance {level.lower:,.0f}-{level.upper:,.0f} · strength {strength:.0f} · break {pressure:.0f}",
        }
        if label == "UNDER ATTACK":
            result.update(
                armed_direction="UP",
                armed_level=_num(level.upper),
                armed_lower=_num(level.lower),
                armed_upper=_num(level.upper),
                armed_at=snapshot_at.isoformat() if snapshot_at is not None else None,
            )
        return result

    return {"state": "RANGE", "score": 60.0, "trigger": "Range dono taraf confirm ho", "next_level": None, "note": "Range entry needs two-sided room"}


def _direction_alignment(score_triplet: tuple[float, float, float], direction: str) -> float:
    if direction == "UP":
        return score_triplet[0]
    if direction == "DOWN":
        return score_triplet[1]
    return score_triplet[2]


def _option_triplet(item: Any) -> tuple[float, float, float, bool]:
    """Current-snapshot option evidence with a low-weight window fallback.

    When the composite flow score is temporarily zero while 1m/3m windows are
    already READY, dropping the whole Options family causes a denominator cliff.
    We therefore reuse only those *current* ready windows as reduced-quality
    evidence.  No prior snapshot/stale option score is carried forward.
    """
    bull = max(0.0, _num(getattr(item, "bullish_score", 0.0)))
    bear = max(0.0, _num(getattr(item, "bearish_score", 0.0)))
    neutral = max(0.0, _num(getattr(item, "range_score", 0.0)))
    if bull + bear + neutral > 0:
        triplet = _normalise_triplet(bull, bear, neutral)
        return triplet[0], triplet[1], triplet[2], False

    signed = 0.0
    weight = 0.0
    neutral_weight = 0.0
    weights = {60: 1.0, 180: 0.75, 300: 0.55}
    for window in getattr(item, "windows", ()) or ():
        if str(getattr(window, "status", "") or "").upper() != "READY":
            continue
        w = weights.get(int(getattr(window, "target_seconds", 0) or 0), 0.35)
        bias = str(getattr(window, "bias", "") or "").upper()
        if bias == "BULLISH":
            signed += w
        elif bias == "BEARISH":
            signed -= w
        else:
            neutral_weight += w
        weight += w
    if weight <= 0:
        return 0.0, 0.0, 0.0, False
    directional = signed / weight
    neutral_share = neutral_weight / weight
    if directional >= 0.30:
        strength = min(68.0, 52.0 + abs(directional) * 16.0)
        return strength, max(10.0, 28.0 - abs(directional) * 10.0), max(12.0, 100.0 - strength - 18.0), True
    if directional <= -0.30:
        strength = min(68.0, 52.0 + abs(directional) * 16.0)
        return max(10.0, 28.0 - abs(directional) * 10.0), strength, max(12.0, 100.0 - strength - 18.0), True
    return 20.0, 20.0, 60.0 + min(10.0, neutral_share * 10.0), True


def _option_quality(snapshot: Any) -> tuple[float, bool, int]:
    """Return smooth *current-snapshot* quality for option-flow evidence.

    READY/WARMING state no longer creates a binary denominator cliff.  Missing
    evidence remains NO VOTE, and no stale option score is carried forward.
    """
    item = getattr(snapshot, "option_intelligence", None)
    if item is None:
        return 0.0, False, 0
    status = str(getattr(item, "status", "") or "").upper()
    confidence = _clamp(_num(getattr(item, "confidence", 0.0)))
    ready_windows = sum(
        str(getattr(window, "status", "") or "").upper() == "READY"
        for window in (getattr(item, "windows", ()) or ())
    )
    score_total = sum(
        max(0.0, _num(getattr(item, name, 0.0)))
        for name in ("bullish_score", "bearish_score", "range_score")
    )
    if status in {"UNAVAILABLE", "MISSING"} or confidence <= 0 or (score_total <= 0 and ready_windows == 0):
        return 0.0, False, ready_windows
    if status == "READY":
        quality = 0.75 + 0.25 * confidence / 100.0
    elif status in {"WARMING UP", "PARTIAL", "CAUTION"}:
        quality = 0.45 + 0.35 * confidence / 100.0
    elif status == "REFERENCE ONLY":
        quality = 0.32 + 0.30 * confidence / 100.0
    else:
        quality = 0.0
    if ready_windows == 0:
        quality *= 0.72
    elif ready_windows == 1:
        quality *= 0.86
    # Window-only fallback is useful but intentionally lower confidence than a
    # fully formed composite score.
    if score_total <= 0 and ready_windows > 0:
        quality *= 0.68
    return round(_clamp(quality, 0.0, 1.0), 3), quality >= 0.25, ready_windows

def _display_stable_value(current: float, previous: Any, *, max_step: float, bypass: bool) -> float:
    """Bound user-facing score chattering without altering raw action calculations."""
    current = float(current)
    try:
        old = float(previous)
    except (TypeError, ValueError):
        return round(current, 1)
    if not isfinite(old) or bypass:
        return round(current, 1)
    delta = current - old
    if abs(delta) <= 2.0:
        return round(current, 1)
    bounded = old + max(-max_step, min(max_step, delta))
    # Keep only a small current contribution: the UI stays responsive while one
    # transient evidence-availability change cannot print a 10-15 point jump.
    blended = bounded * 0.85 + current * 0.15
    return round(_clamp(blended), 1)


def _display_bypass(snapshot: Any, regime: str, barrier: dict[str, Any], direction: str, previous_simple: dict[str, Any] | None) -> bool:
    """True when a real structural event should bypass presentation hysteresis."""
    if not previous_simple:
        return True
    previous_direction = str(previous_simple.get("direction") or "MIXED").upper()
    previous_regime = str(previous_simple.get("regime") or "TRANSITION").upper()
    barrier_state = str(barrier.get("state") or "UNKNOWN").upper()
    event3 = str(getattr(getattr(snapshot.price_action, "three_minute", None), "event", "") or "").upper()
    event15 = str(getattr(getattr(snapshot.price_action, "fifteen_minute", None), "event", "") or "").upper()
    structural = any(token in f"{event3} {event15}" for token in ("BREAKOUT CONFIRMED", "BREAKDOWN CONFIRMED"))
    if barrier_state == "BROKEN" or structural:
        return True
    if previous_direction in {"UP", "DOWN"} and direction in {"UP", "DOWN"} and previous_direction != direction:
        return True
    if previous_regime != str(regime).upper() and any(token in str(regime).upper() for token in ("BREAKOUT", "BREAKDOWN")):
        return True
    return False


def calculate_simple_brain(
    snapshot: Any,
    future: dict[str, Any] | None = None,
    previous_simple: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one simple direction + entry decision from existing canonical evidence."""
    regime, regime_hint = _regime(snapshot)
    core = snapshot.core_evidence
    trend = _normalise_triplet(_num(core.bullish_score), _num(core.bearish_score), _num(core.range_score))
    options_obj = snapshot.option_intelligence
    option_quality, options_available, ready_option_windows = _option_quality(snapshot)
    option_bull, option_bear, option_neutral, option_window_fallback = _option_triplet(options_obj)
    options = (
        _normalise_triplet(option_bull, option_bear, option_neutral)
        if options_available
        else (0.0, 0.0, 0.0)
    )
    part_bull, part_bear, part_neutral, part_conf, part_notes = _participation(snapshot)
    participation = (part_bull, part_bear, part_neutral)

    # Direction is normalized by *available* core blocks.  Optional features do not
    # consume denominator weight and therefore cannot make a valid setup mathematically
    # incapable of reaching an entry threshold.
    participation_quality = _clamp(part_conf / 75.0, 0.0, 1.0) if part_conf > 0 else 0.0
    blocks: list[tuple[str, tuple[float, float, float], float, bool]] = [
        ("Trend", trend, 40.0, _num(getattr(core, "confidence", 0.0)) > 0),
        ("Options", options, 25.0 * option_quality, options_available),
        ("Participation", participation, 20.0 * participation_quality, part_conf > 0),
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
    elif regime_hint in {"UP", "DOWN"}:
        # The old fallback tested max(bull, bear) and could therefore select the
        # *opposite* regime hint with a low score (for example UP 32 vs DOWN 49
        # merely because the 15m context was UP).  Require the hinted side itself
        # to be credible and not materially weaker than the opposite side.
        hinted = bull if regime_hint == "UP" else bear
        opposite = bear if regime_hint == "UP" else bull
        if hinted >= 48 and hinted >= opposite - 5:
            direction = regime_hint
            direction_strength = hinted
        else:
            direction = "MIXED"
            direction_strength = max(bull, bear, neutral)
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

    previous_barrier = (
        ((previous_simple or {}).get("blocks") or {}).get("barrier_entry")
        if isinstance(previous_simple, dict)
        else None
    )
    barrier = _barrier_state(snapshot, direction, previous_barrier)
    option_align = _direction_alignment(options, direction)
    participation_align = _direction_alignment(participation, direction)
    barrier_score = _num(barrier.get("score"), 45.0)
    entry_parts: list[tuple[float, float]] = [(direction_strength, 45.0), (barrier_score, 15.0)]
    if options_available:
        entry_parts.append((option_align, 25.0 * option_quality))
    if part_conf > 0:
        entry_parts.append((participation_align, 15.0 * participation_quality))
    entry_weight = sum(weight for _, weight in entry_parts) or 1.0
    entry_readiness = round(
        _clamp(sum(value * weight for value, weight in entry_parts) / entry_weight),
        1,
    )
    evidence_coverage = round(entry_weight, 1)
    confirmation_blocks = int(option_quality >= 0.55) + int(participation_quality >= 0.55)

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
        if option_quality < 0.72 or participation_quality < 0.55:
            entry_state = "DATA WAIT"
            final_action = "WAIT"
            instruction = "Range trade ke liye Options + Participation confirmation pending"
        elif entry_readiness >= 68 and barrier_state == "RANGE":
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
    elif (
        confirmation_blocks < int(CONFIG.simple_min_confirmation_blocks)
        and barrier_state != "BROKEN"
    ):
        entry_state = "DATA WAIT"
        final_action = "WAIT"
        instruction = "Trend clear hai; Options/Participation me se ek live confirmation pending"
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

    raw_direction_strength = round(direction_strength, 1)
    raw_entry_readiness = round(entry_readiness, 1)
    bypass_stability = _display_bypass(snapshot, regime, barrier, direction, previous_simple)
    previous_display_strength = (previous_simple or {}).get("display_direction_strength", (previous_simple or {}).get("direction_strength"))
    previous_display_entry = (previous_simple or {}).get("display_entry_readiness", (previous_simple or {}).get("entry_readiness"))
    display_direction_strength = _display_stable_value(
        raw_direction_strength, previous_display_strength, max_step=6.0, bypass=bypass_stability
    )
    display_entry_readiness = _display_stable_value(
        raw_entry_readiness, previous_display_entry, max_step=7.0, bypass=bypass_stability or barrier_state == "BROKEN"
    )

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
        "direction_strength": raw_direction_strength,
        "raw_direction_strength": raw_direction_strength,
        "display_direction_strength": display_direction_strength,
        "scores": {"up": bull, "down": bear, "range": neutral},
        "blocks": {
            "trend": {"weight": 40, "bullish": trend[0], "bearish": trend[1], "neutral": trend[2], "available": True},
            "options": {"weight": round(25.0 * option_quality, 2), "bullish": options[0], "bearish": options[1], "neutral": options[2], "confidence": _num(options_obj.confidence), "quality": round(option_quality * 100.0, 1), "available": options_available},
            "participation": {"weight": round(20.0 * participation_quality, 2), "bullish": participation[0], "bearish": participation[1], "neutral": participation[2], "confidence": part_conf, "quality": round(participation_quality * 100.0, 1), "available": part_conf > 0},
            "barrier_entry": {"weight": 15, **barrier},
        },
        "preferred_strategies": preferred,
        "candidate_action": action,
        "entry_readiness": raw_entry_readiness,
        "raw_entry_readiness": raw_entry_readiness,
        "display_entry_readiness": display_entry_readiness,
        "evidence_coverage": evidence_coverage,
        "option_quality": round(option_quality * 100.0, 1),
        "option_window_fallback": bool(option_window_fallback),
        "participation_quality": round(participation_quality * 100.0, 1),
        "confirmation_blocks": confirmation_blocks,
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
