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
    """Produce one auditable strategy gate without mutating either brain."""
    future = snapshot.metadata.get("future_brain") or {}
    current = str(future.get("current_direction") or "RANGE").upper()
    preferred = str(future.get("preferred_direction") or "WAIT").upper()
    future_gate = str(future.get("final_gate") or "WAIT — FUTURE BRAIN UNAVAILABLE")
    forecast_score = max(
        float(future.get("up_15m") or 0),
        float(future.get("down_15m") or 0),
        float(future.get("range_15m") or 0),
    )
    evaluations, plans = _evaluation_map(snapshot), _plan_map(snapshot)
    allowed = STRATEGIES.get(preferred, ())
    ranked = sorted(
        evaluations,
        key=lambda name: (
            name in allowed,
            float(evaluations[name].score or 0),
            float(getattr(plans[name], "quality_score", 0) or 0),
        ),
        reverse=True,
    )
    candidate = next((name for name in ranked if name in allowed), "WAIT")
    plan = plans.get(candidate)
    evaluation = evaluations.get(candidate)
    blockers: list[str] = []
    if not snapshot.market_session.is_live:
        blockers.append("Market is not live")
    for feed_name in ("quotes", "candles", "option_chain"):
        feed = snapshot.feed_status.get(feed_name)
        if feed is None or getattr(feed, "use_state", "") != "LIVE":
            blockers.append(f"{feed_name} is not confirmed live")
    if preferred not in STRATEGIES or future_gate.startswith("WAIT"):
        blockers.append(future_gate)
    reversal = preferred in {"UP", "DOWN"} and current in {"UP", "DOWN"} and preferred != current
    if reversal and "REVERSAL PAPER TEST" not in future_gate:
        blockers.append("Current/Future disagreement — reversal confirmation pending")
    if candidate == "WAIT" or plan is None or not plan.available:
        blockers.append("Future-compatible protected strike pair unavailable")
    if candidate != "WAIT":
        alignment = _entry_alignment_blocker(
            setup=candidate,
            price_action=snapshot.price_action,
            levels=snapshot.levels,
            volume=snapshot.volume,
            patterns=snapshot.patterns,
            allow_countertrend_15m="REVERSAL PAPER TEST" in future_gate,
        )
        if alignment:
            blockers.append(alignment)
    risk_per_lot = (
        float(getattr(plan, "max_risk_points", 0) or 0)
        * int(snapshot.risk_profile.lot_size or 0)
        if plan else 0.0
    )
    if plan and (risk_per_lot <= 0 or risk_per_lot > float(snapshot.risk_profile.risk_budget_rupees or 0)):
        blockers.append("Risk budget does not allow one protected lot")
    # The Common Gate is presentation/coordination only.  It may announce entry
    # only after the canonical Execution Guard has approved this exact candidate.
    if execution_guard is not None:
        guard_setup = str(getattr(execution_guard, "selected_setup", "WAIT") or "WAIT")
        guard_ready = str(getattr(execution_guard, "readiness", "BLOCKED") or "BLOCKED")
        if guard_setup != candidate:
            blockers.append(
                f"Execution Guard candidate mismatch: {guard_setup} != {candidate}"
            )
        if guard_ready != "ENTRY READY":
            guard_blockers = tuple(getattr(execution_guard, "blockers", ()) or ())
            blockers.append(
                str(guard_blockers[0])
                if guard_blockers
                else f"Execution Guard is {guard_ready}"
            )
    # Preserve order while removing duplicate explanations.
    blockers = list(dict.fromkeys(item for item in blockers if item))
    entry_allowed = not blockers and candidate != "WAIT"
    guidance = build_entry_guidance(plan, entry_ready=entry_allowed, live=snapshot.market_session.is_live)
    current_strength = float(future.get("current_strength") or 0)
    plan_quality = float(getattr(plan, "quality_score", 0) or 0) if plan else 0.0
    history_accuracy = future.get("historical_accuracy_15m")
    history_matches = int(future.get("historical_matches") or 0)
    # A transparent confidence blend, not a profit probability.  Sparse history
    # contributes nothing and a blocked gate is capped below entry territory.
    parts = [(current_strength, .30), (forecast_score, .40), (plan_quality, .15)]
    if history_accuracy is not None and history_matches >= 10:
        parts.append((float(history_accuracy), .15))
    weight = sum(item[1] for item in parts) or 1.0
    confidence = round(sum(value * share for value, share in parts) / weight, 1)
    if not entry_allowed:
        confidence = min(confidence, 54.9)
    return {
        "status": "ENTRY ALLOWED" if entry_allowed else "REFERENCE ONLY" if not snapshot.market_session.is_live else "WAIT",
        "final_action": candidate if entry_allowed else "WAIT",
        "best_strategy": candidate,
        "entry_allowed": entry_allowed,
        "execution_readiness": (
            str(getattr(execution_guard, "readiness", "NOT CHECKED"))
            if execution_guard is not None else "NOT CHECKED"
        ),
        "direction": preferred if preferred in STRATEGIES else "MIXED",
        "current_direction": current,
        "future_gate": future_gate,
        "agreement": current == preferred and current in STRATEGIES,
        "reversal": reversal,
        "trade_confidence": confidence,
        "current_evidence_score": round(current_strength, 1),
        "future_forecast_score": round(forecast_score, 1),
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
            "instruction": guidance.instruction,
        },
        "ranked_strategies": ranked,
        "note": "Trade confidence is an evidence blend, not guaranteed win/profit probability.",
    }
