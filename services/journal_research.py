"""Paper-only validation helpers for One Brain and Market Intelligence.

This module is deliberately downstream of the authoritative snapshot.  It never
changes One-Brain/Market-Intelligence scores, never requests broker data and never
places orders.  It only freezes evidence already present in a snapshot so later
paper outcomes can be reviewed without look-ahead.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_direction(value: Any) -> str:
    raw = str(value or "").upper().strip()
    if raw in {"UP", "BULL", "BULLISH"}:
        return "BULLISH"
    if raw in {"DOWN", "BEAR", "BEARISH"}:
        return "BEARISH"
    if raw in {"RANGE", "NEUTRAL", "MIXED", "SIDEWAYS"}:
        return "RANGE" if raw in {"RANGE", "SIDEWAYS"} else "MIXED"
    return raw or "MIXED"


def setup_direction(setup: Any) -> str:
    action = str(setup or "").upper().strip()
    return {
        "CE BUY": "BULLISH",
        "PE SELL": "BULLISH",
        "PE BUY": "BEARISH",
        "CE SELL": "BEARISH",
        "IRON CONDOR": "RANGE",
    }.get(action, "MIXED")


def seller_setup_for_direction(direction: Any) -> str | None:
    direction = normalize_direction(direction)
    if direction == "BULLISH":
        return "PE SELL"
    if direction == "BEARISH":
        return "CE SELL"
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def freeze_market_context(snapshot: Any) -> dict[str, Any]:
    """Freeze only compact decision evidence already attached to the snapshot."""
    metadata = getattr(snapshot, "metadata", {}) or {}
    mie = _mapping(metadata.get("market_intelligence"))
    integrity = _mapping(mie.get("pressure_integrity"))
    institutional = _mapping(mie.get("institutional_window"))
    liquidity = _mapping(mie.get("liquidity"))
    money = _mapping(liquidity.get("money_concentration"))
    simple = _mapping(metadata.get("simple_brain"))
    common = _mapping(metadata.get("common_decision"))
    move_radar = _mapping(mie.get("move_radar"))

    spot = getattr(getattr(snapshot, "levels", None), "current_price", None)
    if spot is None:
        quote = getattr(snapshot, "nifty_quote", {}) or {}
        if isinstance(quote, Mapping):
            spot = quote.get("last_price")

    return {
        "captured_at": getattr(snapshot, "created_at", datetime.now()).isoformat(),
        "spot": _num(spot),
        "ob_direction": normalize_direction(simple.get("direction") or getattr(getattr(snapshot, "decision", None), "market_direction", "")),
        "ob_direction_strength": round(_num(simple.get("direction_strength"), 0.0) or 0.0, 1),
        "ob_entry_readiness": round(_num(simple.get("entry_readiness"), 0.0) or 0.0, 1),
        "ob_final_action": str(simple.get("final_action") or common.get("final_action") or "WAIT").upper(),
        "ob_entry_state": str(simple.get("entry_state") or ""),
        "mi_direction": normalize_direction(institutional.get("direction") or mie.get("direction")),
        "mi_market_state": str(mie.get("market_state") or ""),
        "mi_status": str(mie.get("status") or ""),
        "institutional_window_state": str(institutional.get("state") or "CLOSED").upper(),
        "institutional_window_score": round(_num(institutional.get("opportunity_score"), 0.0) or 0.0, 1),
        "institutional_window_gates": int(_num(institutional.get("gates_ready"), 0.0) or 0),
        "institutional_window_data_safety": str(institutional.get("data_safety_state") or ""),
        "institutional_window_alert_eligible": bool(institutional.get("alert_eligible")),
        "pressure_direction": normalize_direction(integrity.get("direction")),
        "pressure_quality_state": str(integrity.get("quality_state") or "UNVERIFIED").upper(),
        "pressure_quality_score": round(_num(integrity.get("quality_score"), 0.0) or 0.0, 1),
        "pressure_fake_score": round(_num(integrity.get("fake_pressure_score"), 0.0) or 0.0, 1),
        "pressure_real_score": round(_num(integrity.get("real_pressure_score"), 0.0) or 0.0, 1),
        "move_attack_state": str(integrity.get("move_attack_state") or "").upper(),
        "barrier_state": str(integrity.get("barrier_state") or "").upper(),
        "barrier_attack_score": round(_num(integrity.get("barrier_attack_score"), 0.0) or 0.0, 1),
        "flip_state": str(integrity.get("flip_state") or "NONE").upper(),
        "liquidity_hunt_bias": str(liquidity.get("hunt_bias") or "UNCLEAR").upper(),
        "liquidity_path_clearance": round(_num(liquidity.get("path_clearance"), 0.0) or 0.0, 1),
        "liquidity_magnet_bias": str(money.get("bias") or "UNCLEAR").upper(),
        "liquidity_magnet_confidence": round(_num(money.get("confidence"), 0.0) or 0.0, 1),
        "liquidity_upside_score": round(_num(money.get("upside_score"), 0.0) or 0.0, 1),
        "liquidity_downside_score": round(_num(money.get("downside_score"), 0.0) or 0.0, 1),
        "move_radar_state": str(move_radar.get("state") or move_radar.get("radar_state") or ""),
        "mi_one_brain_alignment": str(mie.get("one_brain_alignment") or ""),
    }


def classify_alignment(ob_direction: Any, mi_direction: Any) -> str:
    ob = normalize_direction(ob_direction)
    mi = normalize_direction(mi_direction)
    if ob in {"BULLISH", "BEARISH"} and mi in {"BULLISH", "BEARISH"}:
        return "ALIGNED" if ob == mi else "CONFLICT"
    return "INDEPENDENT"


def market_intelligence_candidate(snapshot: Any) -> dict[str, Any] | None:
    """Return a research-paper candidate without changing any live calculation.

    Priority 1: Institutional Window OPEN/STRONG with all six gates and live-data
    safety.  Priority 2: verified/realized Pressure Integrity with an active barrier
    attack / expansion.  Liquidity Magnet never creates a trade by itself.
    """
    metadata = getattr(snapshot, "metadata", {}) or {}
    mie = _mapping(metadata.get("market_intelligence"))
    if not mie:
        return None
    institutional = _mapping(mie.get("institutional_window"))
    integrity = _mapping(mie.get("pressure_integrity"))

    iw_state = str(institutional.get("state") or "CLOSED").upper()
    iw_direction = normalize_direction(institutional.get("direction") or mie.get("direction"))
    gates = int(_num(institutional.get("gates_ready"), 0.0) or 0)
    alert_eligible = bool(institutional.get("alert_eligible"))
    if iw_state in {"OPEN", "STRONG"} and gates >= 6 and alert_eligible:
        setup = seller_setup_for_direction(iw_direction)
        if setup:
            transition = str(institutional.get("transition") or "UNCHANGED").upper()
            return {
                "source": "MARKET INTELLIGENCE",
                "trigger_type": "INSTITUTIONAL WINDOW",
                "trigger_state": iw_state,
                "trigger_transition": transition,
                "direction": iw_direction,
                "setup": setup,
                "trigger_score": round(_num(institutional.get("opportunity_score"), 0.0) or 0.0, 1),
                "reason": f"Institutional Window {iw_state} · {gates}/6 gates · {transition} · data safety ready",
            }

    quality_state = str(integrity.get("quality_state") or "UNVERIFIED").upper()
    p_direction = normalize_direction(integrity.get("direction") or mie.get("direction"))
    attack_state = str(integrity.get("move_attack_state") or "").upper()
    barrier_attack = _num(integrity.get("barrier_attack_score"), 0.0) or 0.0
    pressure_trigger = bool(
        quality_state in {"VERIFIED", "REALIZED"}
        and p_direction in {"BULLISH", "BEARISH"}
        and (attack_state in {"ATTACK", "BREAK / EXPANSION"} or barrier_attack >= 65.0)
    )
    if pressure_trigger:
        setup = seller_setup_for_direction(p_direction)
        if setup:
            return {
                "source": "MARKET INTELLIGENCE",
                "trigger_type": "PRESSURE INTEGRITY",
                "trigger_state": quality_state,
                "trigger_transition": str(integrity.get("transition") or quality_state).upper(),
                "direction": p_direction,
                "setup": setup,
                "trigger_score": round(_num(integrity.get("quality_score"), 0.0) or 0.0, 1),
                "reason": f"Pressure {quality_state} · {attack_state or 'barrier support'} · attack {barrier_attack:.0f}/100",
            }
    return None


def diagnose_closed_trade(
    entry: Mapping[str, Any],
    *,
    exit_context: Mapping[str, Any],
    current_spot: float | None,
    gross_pnl: float,
    net_pnl: float,
    outcome: str,
    closed_at: datetime,
) -> dict[str, Any]:
    """Evidence-based post-trade attribution; never claims a proven causal mechanism."""
    entry_context = _mapping(entry.get("entry_context"))
    direction = normalize_direction(entry.get("signal_direction") or setup_direction(entry.get("setup")))
    entry_spot = _num(entry.get("entry_spot"))
    raw_move = None if entry_spot is None or current_spot is None else float(current_spot) - entry_spot
    directional_progress = None
    if raw_move is not None and direction in {"BULLISH", "BEARISH"}:
        directional_progress = raw_move if direction == "BULLISH" else -raw_move

    factors: list[str] = []
    if net_pnl > 0:
        classification = "PROFIT"
        if directional_progress is not None and directional_progress >= 5:
            factors.append(f"Direction follow-through: {directional_progress:.1f} pts in expected direction")
        if str(entry_context.get("pressure_quality_state") or "") in {"VERIFIED", "REALIZED"}:
            factors.append(f"Entry pressure was {entry_context.get('pressure_quality_state')}")
        if str(entry_context.get("institutional_window_state") or "") in {"OPEN", "STRONG"}:
            factors.append(f"Institutional Window was {entry_context.get('institutional_window_state')}")
        desired_magnet = "UPSIDE" if direction == "BULLISH" else "DOWNSIDE" if direction == "BEARISH" else ""
        entry_magnet_conf = _num(entry_context.get("liquidity_magnet_confidence"), 0.0) or 0.0
        if (
            desired_magnet
            and str(entry_context.get("liquidity_magnet_bias") or "") == desired_magnet
            and entry_magnet_conf >= 45
        ):
            factors.append(f"Liquidity Magnet aligned {desired_magnet}")
        if not factors:
            factors.append("Protected option structure improved before exit; no single driver is proven")
        summary = "Likely worked because " + "; ".join(factors[:3])
    elif net_pnl < 0:
        classification = "LOSS"
        if gross_pnl > 0:
            factors.append("Gross paper P&L was positive but estimated charges made the net result negative")
        if directional_progress is not None and directional_progress <= -5:
            factors.append(f"Direction failed: {-directional_progress:.1f} pts moved opposite the expected path")
        exit_pressure = str(exit_context.get("pressure_quality_state") or "").upper()
        exit_flip = str(exit_context.get("flip_state") or "").upper()
        if exit_pressure in {"ABSORPTION RISK", "BUILD-UP FAILED"}:
            factors.append(f"Pressure deteriorated to {exit_pressure}")
        if exit_flip in {"FLIP WATCH", "FLIP CONFIRMED"} or exit_pressure in {"FLIP WATCH", "FLIP CONFIRMED"}:
            factors.append(f"Opposite pressure developed: {exit_flip if exit_flip != 'NONE' else exit_pressure}")
        entry_window = str(entry_context.get("institutional_window_state") or "").upper()
        exit_window = str(exit_context.get("institutional_window_state") or "").upper()
        if entry_window in {"OPEN", "STRONG"} and exit_window in {"FORMING", "CLOSED"}:
            factors.append(f"Institutional Window weakened {entry_window} → {exit_window}")
        entry_barrier = str(entry_context.get("barrier_state") or "").upper()
        exit_barrier = str(exit_context.get("barrier_state") or "").upper()
        if entry_barrier in {"UNDER ATTACK", "PRESSING"} and exit_barrier in {"HOLDING STRONG", "REJECTED / FAKE RISK"}:
            factors.append(f"Barrier attack failed; exit barrier state {exit_barrier}")
        desired_magnet = "UPSIDE" if direction == "BULLISH" else "DOWNSIDE" if direction == "BEARISH" else ""
        entry_magnet = str(entry_context.get("liquidity_magnet_bias") or "").upper()
        entry_magnet_conf = _num(entry_context.get("liquidity_magnet_confidence"), 0.0) or 0.0
        if desired_magnet and entry_magnet in {"UPSIDE", "DOWNSIDE"} and entry_magnet != desired_magnet and entry_magnet_conf >= 45:
            factors.append(f"Liquidity conflict existed at entry ({entry_magnet} magnet)")
        if directional_progress is not None and directional_progress > 0 and not factors:
            factors.append("Spot moved broadly as expected but the protected option spread still lost; entry credit/debit, IV or hedge pricing may have been adverse")
        if directional_progress is not None and abs(directional_progress) < 5 and not factors:
            factors.append("No meaningful directional follow-through before the exit condition")
        if not factors:
            factors.append("Multifactor / cause unclear from recorded evidence")
        summary = "Likely loss factors: " + "; ".join(factors[:3])
    else:
        classification = "BREAKEVEN"
        factors.append("Estimated net result was approximately flat")
        summary = factors[0]

    opened_at = None
    try:
        opened_at = datetime.fromisoformat(str(entry.get("opened_at")))
        if opened_at.tzinfo is None and closed_at.tzinfo is not None:
            opened_at = opened_at.replace(tzinfo=closed_at.tzinfo)
    except (TypeError, ValueError):
        pass
    duration = None if opened_at is None else round(max(0.0, (closed_at - opened_at).total_seconds() / 60.0), 1)

    return {
        "result_class": classification,
        "diagnosis_summary": summary,
        "diagnosis_factors": factors[:6],
        "diagnosis_basis": "Recorded evidence attribution — likely explanation, not proven causation",
        "exit_context": dict(exit_context),
        "spot_move_points": None if raw_move is None else round(raw_move, 2),
        "directional_progress_points": None if directional_progress is None else round(directional_progress, 2),
        "duration_minutes": duration,
        "gross_pnl_rupees": round(float(gross_pnl), 2),
        "estimated_net_pnl_rupees": round(float(net_pnl), 2),
        "exit_outcome": str(outcome or ""),
    }
