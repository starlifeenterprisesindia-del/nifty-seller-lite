"""Common decision workspace joining Current Brain and Future Brain.

Current Brain remains an observation of the market now.  This module is the
only place where a Future Brain forecast may become a strategy/strike action.
It never places an order.
"""
from __future__ import annotations

from typing import Any

from analysis.decision import _entry_alignment_blocker
from analysis.entry_guidance import build_entry_guidance


STRATEGIES = {
    "UP": ("PE SELL", "CE BUY"),
    "DOWN": ("CE SELL", "PE BUY"),
    "RANGE": ("IRON CONDOR",),
}


def _plan_map(snapshot: Any) -> dict[str, Any]:
    bundle = snapshot.trade_plan
    return {
        "CE BUY": bundle.ce_buy,
        "PE BUY": bundle.pe_buy,
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }


def _evaluation_map(snapshot: Any) -> dict[str, Any]:
    decision = snapshot.decision
    return {
        "CE BUY": decision.ce_buy,
        "PE BUY": decision.pe_buy,
        "CE SELL": decision.ce_sell,
        "PE SELL": decision.pe_sell,
        "IRON CONDOR": decision.iron_condor,
    }


def build_common_decision(
    snapshot: Any, *, execution_guard: Any | None = None
) -> dict[str, Any]:
    """Produce one simple final gate.

    v2.48 uses ``simple_brain`` as the strategy authority. Future Brain remains an
    advisory risk/next-move view and can no longer convert an ordinary MIXED forecast
    into a second hard WAIT gate. Legacy behaviour is retained only when an older
    snapshot does not contain ``simple_brain``.
    """
    simple = snapshot.metadata.get("simple_brain") or {}
    if not simple:
        # Backward-compatible fallback for stored/legacy snapshots.
        future = snapshot.metadata.get("future_brain") or {}
        current = str(future.get("current_direction") or "RANGE").upper()
        preferred = str(future.get("preferred_direction") or current or "WAIT").upper()
        candidate = next(iter(STRATEGIES.get(preferred, ())), "WAIT")
        simple = {
            "direction": preferred if preferred in STRATEGIES else current,
            "direction_strength": float(future.get("current_strength") or 0.0),
            "entry_readiness": 0.0,
            "entry_state": "LEGACY / WAIT",
            "candidate_action": candidate,
            "final_action": "WAIT",
            "instruction": str(future.get("final_gate") or "Legacy snapshot"),
            "hard_blockers": (),
            "preferred_strategies": STRATEGIES.get(preferred, ()),
            "future_advisory": {},
        }

    direction = str(simple.get("direction") or "MIXED").upper()
    preferred = tuple(simple.get("preferred_strategies") or STRATEGIES.get(direction, ()))
    evaluations, plans = _evaluation_map(snapshot), _plan_map(snapshot)

    # Seller-first order supplied by Simple Brain, then existing fit/plan quality as
    # tie-breakers.  No independent Future-Brain direction filter is applied.
    ranked = sorted(
        evaluations,
        key=lambda name: (
            1 if name in preferred else 0,
            -preferred.index(name) if name in preferred else -99,
            float(evaluations[name].score or 0),
            float(getattr(plans[name], "quality_score", 0) or 0),
        ),
        reverse=True,
    )
    wanted = str(simple.get("candidate_action") or "WAIT").upper()
    candidate = wanted if wanted in evaluations else next((x for x in ranked if x in preferred), "WAIT")
    plan = plans.get(candidate)
    evaluation = evaluations.get(candidate)

    blockers: list[str] = list(simple.get("hard_blockers") or ())
    simple_final = str(simple.get("final_action") or "WAIT").upper()
    if simple_final == "WAIT":
        blockers.append(str(simple.get("instruction") or simple.get("entry_state") or "Entry trigger pending"))
    if candidate == "WAIT" or plan is None or not plan.available:
        blockers.append("Protected strike/hedge pair unavailable")
    elif str(getattr(plan, "status", "")).upper() != "READY":
        blockers.append(str(getattr(plan, "blocker", "Protected plan not ready")))

    risk_per_lot = (
        float(getattr(plan, "max_risk_points", 0) or 0)
        * int(snapshot.risk_profile.lot_size or 0)
        if plan else 0.0
    )
    if plan and (risk_per_lot <= 0 or risk_per_lot > float(snapshot.risk_profile.risk_budget_rupees or 0)):
        blockers.append("Risk budget does not allow one protected lot")

    if execution_guard is not None:
        guard_setup = str(getattr(execution_guard, "selected_setup", "WAIT") or "WAIT")
        guard_ready = str(getattr(execution_guard, "readiness", "BLOCKED") or "BLOCKED")
        if guard_setup != candidate:
            blockers.append(f"Execution candidate mismatch: {guard_setup} != {candidate}")
        if guard_ready != "ENTRY READY":
            guard_blockers = tuple(getattr(execution_guard, "blockers", ()) or ())
            blockers.append(str(guard_blockers[0]) if guard_blockers else f"Execution Guard is {guard_ready}")

    blockers = list(dict.fromkeys(x for x in blockers if x))
    entry_allowed = bool(
        execution_guard is not None
        and not blockers
        and candidate != "WAIT"
        and simple_final == candidate
    )
    guidance = build_entry_guidance(plan, entry_ready=entry_allowed, live=snapshot.market_session.is_live)

    direction_strength = float(simple.get("direction_strength") or 0.0)
    entry_readiness = float(simple.get("entry_readiness") or 0.0)
    plan_quality = float(getattr(plan, "quality_score", 0) or 0) if plan else 0.0
    confidence = round(direction_strength * .50 + entry_readiness * .35 + plan_quality * .15, 1)
    if not entry_allowed:
        confidence = min(confidence, 69.9)

    future = snapshot.metadata.get("future_brain") or {}
    history_accuracy = future.get("historical_accuracy_15m")
    history_matches = int(future.get("historical_matches") or 0)
    return {
        "status": "ENTRY ALLOWED" if entry_allowed else "REFERENCE ONLY" if not snapshot.market_session.is_live else "WAIT",
        "final_action": candidate if entry_allowed else "WAIT",
        "best_strategy": candidate,
        "entry_allowed": entry_allowed,
        "execution_readiness": str(getattr(execution_guard, "readiness", "NOT CHECKED")) if execution_guard is not None else "PROPOSAL",
        "direction": direction,
        "current_direction": direction,
        "regime": str(simple.get("regime") or "TRANSITION"),
        "entry_state": str(simple.get("entry_state") or "WAIT"),
        "trigger": str(simple.get("trigger") or ""),
        "instruction": str(simple.get("instruction") or ""),
        "future_gate": str((simple.get("future_advisory") or {}).get("note") or "Future Brain advisory only"),
        "agreement": True,
        "reversal": False,
        "trade_confidence": confidence,
        "current_evidence_score": round(direction_strength, 1),
        "entry_readiness": round(entry_readiness, 1),
        "future_forecast_score": round(max(float(future.get("up_15m") or 0), float(future.get("down_15m") or 0), float(future.get("range_15m") or 0)), 1),
        "historical_hit_rate": history_accuracy,
        "historical_matches": history_matches,
        "strategy_fit": round(float(getattr(evaluation, "score", 0) or 0), 1),
        "plan_quality": round(plan_quality, 1),
        "risk_per_lot_rupees": round(risk_per_lot, 2) if risk_per_lot else None,
        "blockers": blockers,
        "entry": {
            "current": guidance.current,
            "preferred_zone": guidance.preferred_zone,
            "minimum": guidance.minimum,
            "status": guidance.status,
            "instruction": str(simple.get("instruction") or guidance.instruction),
        },
        "ranked_strategies": ranked,
        "note": "Simple One-Brain: regime → direction → entry → risk → action.",
    }
