from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RsiReversalSetup:
    action: str
    status: str
    zone: str
    confidence: int
    rsi_now: float | None
    rsi_previous: float | None
    barrier_text: str
    big_player_text: str
    structure_text: str
    market_sl_text: str
    money_sl_text: str
    suggested_lots: int
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]


MONEY_STOP_RUPEES = 5_000.0
SELL_PREMIUM_STOP_PCT = 40.0
RSI_TOP = 70.0
RSI_BOTTOM = 30.0
MIN_BARRIER_STRENGTH = 60.0
MAX_BREAK_PRESSURE = 55.0
MIN_BIG_PLAYER_SCORE = 60.0
MIN_BIG_PLAYER_CONFIRMATIONS = 2


def _rsi(snapshot: Any | None) -> float | None:
    if snapshot is None:
        return None
    value = getattr(
        getattr(getattr(snapshot, "indicators", None), "three_minute", None),
        "rsi14",
        None,
    )
    return float(value) if value is not None else None


def _strong_barrier(level: Any | None) -> bool:
    if level is None:
        return False
    raw_strength = getattr(level, "strength", None)
    raw_pressure = getattr(level, "break_pressure", None)
    strength = float(raw_strength) if raw_strength is not None else 0.0
    pressure = float(raw_pressure) if raw_pressure is not None else 100.0
    return (
        strength >= MIN_BARRIER_STRENGTH
        and pressure <= MAX_BREAK_PRESSURE
        and strength - pressure >= 10.0
    )


def _near_barrier(level: Any | None) -> bool:
    if level is None:
        return False
    lower = float(getattr(level, "lower", 0.0) or 0.0)
    upper = float(getattr(level, "upper", lower) or lower)
    raw_distance = getattr(level, "distance_points", None)
    distance = abs(float(raw_distance)) if raw_distance is not None else 9999.0
    return distance <= max(30.0, abs(upper - lower) + 15.0)


def _level_text(level: Any | None, fallback: str) -> str:
    if level is None:
        return f"{fallback} unresolved"
    return (
        f"{float(level.lower):,.0f}–{float(level.upper):,.0f} · "
        f"Strength {float(level.strength):.0f} · "
        f"Break {float(level.break_pressure):.0f}"
    )


def _plan(snapshot: Any, action: str) -> Any | None:
    bundle = getattr(snapshot, "trade_plan", None)
    if bundle is None:
        return None
    return {
        "CE SELL": getattr(bundle, "ce_sell", None),
        "PE SELL": getattr(bundle, "pe_sell", None),
        "IRON CONDOR": getattr(bundle, "iron_condor", None),
    }.get(action)


def _leg_text(legs: tuple[Any, ...] | list[Any]) -> str:
    values = []
    for leg in legs or ():
        strike = getattr(leg, "strike", None)
        option_type = str(getattr(leg, "option_type", ""))
        if strike is not None:
            values.append(f"{float(strike):,.0f} {option_type}".strip())
    return " + ".join(values) or "—"


def _structure(plan: Any | None) -> str:
    if plan is None or not bool(getattr(plan, "available", False)):
        return "Protected strike abhi unresolved"
    short = _leg_text(getattr(plan, "short_legs", ()))
    hedge = _leg_text(getattr(plan, "hedge_legs", ()))
    return f"SELL {short} · HEDGE {hedge}"


def _suggested_lots(snapshot: Any, plan: Any | None) -> int:
    if plan is None or not bool(getattr(plan, "available", False)):
        return 0
    credit = float(getattr(plan, "estimated_credit_points", 0.0) or 0.0)
    lot_size = int(getattr(getattr(snapshot, "risk_profile", None), "lot_size", 0) or 0)
    if credit <= 0 or lot_size <= 0:
        return 0
    estimated_stop_per_lot = credit * SELL_PREMIUM_STOP_PCT / 100.0 * lot_size
    if estimated_stop_per_lot <= 0:
        return 0
    return max(0, int(MONEY_STOP_RUPEES // estimated_stop_per_lot))


def _market_sl(action: str, resistance: Any | None, support: Any | None) -> str:
    if action == "CE SELL" and resistance is not None:
        return f"NIFTY {float(resistance.upper) + 20:,.0f} ke upar 3m close"
    if action == "PE SELL" and support is not None:
        return f"NIFTY {float(support.lower) - 20:,.0f} ke neeche 3m close"
    if action == "IRON CONDOR" and resistance is not None and support is not None:
        return (
            f"CE side {float(resistance.upper) + 20:,.0f} upar / "
            f"PE side {float(support.lower) - 20:,.0f} neeche 3m close"
        )
    return "Barrier invalidation unavailable"


def evaluate_rsi_reversal_setup(snapshot: Any, previous_snapshot: Any | None) -> RsiReversalSetup:
    """Independent RSI reversal advisory; it never changes the One-Brain decision."""

    rsi_now = _rsi(snapshot)
    rsi_previous = _rsi(previous_snapshot)
    barrier_map = getattr(snapshot, "barrier_map", None)
    resistance = getattr(barrier_map, "nearest_resistance", None)
    support = getattr(barrier_map, "nearest_support", None)
    big_player = getattr(snapshot, "big_player_activity", None)

    top_seen = rsi_now is not None and rsi_now >= RSI_TOP
    bottom_seen = rsi_now is not None and rsi_now <= RSI_BOTTOM
    top_turn = (
        rsi_now is not None
        and rsi_previous is not None
        and rsi_previous >= RSI_TOP
        and rsi_now < rsi_previous
    )
    bottom_turn = (
        rsi_now is not None
        and rsi_previous is not None
        and rsi_previous <= RSI_BOTTOM
        and rsi_now > rsi_previous
    )
    zone = "TOP" if top_seen or top_turn else "BOTTOM" if bottom_seen or bottom_turn else "NORMAL"

    resistance_ready = _strong_barrier(resistance) and _near_barrier(resistance)
    support_ready = _strong_barrier(support) and _near_barrier(support)
    both_barriers = _strong_barrier(resistance) and _strong_barrier(support)

    bp_direction = str(getattr(big_player, "direction", "MIXED") or "MIXED").upper()
    bp_score = float(getattr(big_player, "score", 0.0) or 0.0)
    bp_confirmations = int(getattr(big_player, "confirmation_count", 0) or 0)
    bp_ready = (
        str(getattr(big_player, "status", "")).upper() == "READY"
        and bp_score >= MIN_BIG_PLAYER_SCORE
        and bp_confirmations >= MIN_BIG_PLAYER_CONFIRMATIONS
    )
    selling_confirmed = bp_ready and bp_direction == "SELLING"
    buying_confirmed = bp_ready and bp_direction == "BUYING"
    mixed_flow = not bp_ready or bp_direction == "MIXED"

    reasons: list[str] = []
    cautions: list[str] = []
    action = "WAIT"
    confidence = 0

    if rsi_now is None:
        cautions.append("3m RSI unavailable")
    elif zone == "TOP":
        reasons.append(f"3m RSI top zone {rsi_now:.1f}")
        confidence += 30
        if top_turn:
            reasons.append(f"RSI {rsi_previous:.1f} se neeche muda")
            confidence += 20
        else:
            cautions.append("RSI abhi top se neeche mudna baaki hai")
        if resistance_ready:
            reasons.append("Upper Barrier + OI majboot aur paas hai")
            confidence += 25
        else:
            cautions.append("Upper Barrier + OI entry-ready nahi")
        if selling_confirmed:
            reasons.append("Big Player selling confirmed")
            confidence += 25
        elif buying_confirmed:
            cautions.append("Big Player buying CE Sell ke opposite hai")
        else:
            cautions.append("Big Player direction abhi confirm nahi")

        if top_turn and resistance_ready and selling_confirmed:
            action = "CE SELL"
        elif top_turn and both_barriers and mixed_flow:
            action = "IRON CONDOR"
            confidence = min(confidence, 69)
    elif zone == "BOTTOM":
        reasons.append(f"3m RSI bottom zone {rsi_now:.1f}")
        confidence += 30
        if bottom_turn:
            reasons.append(f"RSI {rsi_previous:.1f} se upar muda")
            confidence += 20
        else:
            cautions.append("RSI abhi bottom se upar mudna baaki hai")
        if support_ready:
            reasons.append("Lower Barrier + OI majboot aur paas hai")
            confidence += 25
        else:
            cautions.append("Lower Barrier + OI entry-ready nahi")
        if buying_confirmed:
            reasons.append("Big Player buying confirmed")
            confidence += 25
        elif selling_confirmed:
            cautions.append("Big Player selling PE Sell ke opposite hai")
        else:
            cautions.append("Big Player direction abhi confirm nahi")

        if bottom_turn and support_ready and buying_confirmed:
            action = "PE SELL"
        elif bottom_turn and both_barriers and mixed_flow:
            action = "IRON CONDOR"
            confidence = min(confidence, 69)
    else:
        cautions.append("RSI top 70+ ya bottom 30- zone mein nahi")

    market_live = bool(getattr(getattr(snapshot, "market_session", None), "is_live", False))
    plan = _plan(snapshot, action)
    if action != "WAIT" and (plan is None or not bool(getattr(plan, "available", False))):
        cautions.append(f"{action} ka protected strike/hedge available nahi")
        action = "WAIT"
        plan = None
    if not market_live:
        status = "REFERENCE ONLY"
    elif action == "WAIT":
        status = "WAIT"
    else:
        status = "ENTRY READY"

    if not market_live and action != "WAIT":
        cautions.append("Market live nahi hai; fresh entry nahi")

    confidence = max(0, min(100, confidence))
    lots = _suggested_lots(snapshot, plan)
    if action != "WAIT" and lots <= 0:
        cautions.append("₹5,000 SL budget mein 1 lot bhi safely fit nahi")
        action = "WAIT"
        status = "WAIT" if market_live else "REFERENCE ONLY"
        plan = None

    bp_text = (
        f"{bp_direction} {bp_score:.0f}/100 · {bp_confirmations}/"
        f"{int(getattr(big_player, 'confirmation_total', 0) or 0)}"
        if big_player is not None
        else "Unavailable"
    )
    active_level = resistance if zone == "TOP" else support if zone == "BOTTOM" else None
    return RsiReversalSetup(
        action=action,
        status=status,
        zone=zone,
        confidence=confidence,
        rsi_now=rsi_now,
        rsi_previous=rsi_previous,
        barrier_text=_level_text(active_level, "Barrier"),
        big_player_text=bp_text,
        structure_text=_structure(plan),
        market_sl_text=_market_sl(action, resistance, support),
        money_sl_text="₹5,000 total hard SL · hedge included",
        suggested_lots=lots if action != "WAIT" else 0,
        reasons=tuple(reasons),
        cautions=tuple(cautions),
    )
