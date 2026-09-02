from __future__ import annotations

from typing import Any

from models import MarketSnapshot, SetupPlan
from analysis.canonical_forecast import compatible_strategies


def required_live_feed_state(snapshot: MarketSnapshot) -> tuple[bool, str]:
    """Return the screen/report status of the three mandatory live feeds.

    This is presentation-only. It reads the already-built snapshot and never fetches,
    mutates state, or changes the Final One-Brain Decision.
    """

    required = ("quotes", "candles", "option_chain")
    states: list[str] = []
    ok = True
    for key in required:
        item = snapshot.feed_status.get(key)
        live = item is not None and item.ok and item.use_state == "LIVE"
        ok = ok and live
        if not live:
            states.append(f"{key}={item.use_state if item is not None else 'MISSING'}")
    progression = snapshot.feed_status.get("price_progression")
    if progression is not None and not progression.ok:
        ok = False
        states.append(f"price_progression={progression.use_state}")
    expiry_quality = snapshot.feed_status.get("expiry_close_quality")
    if expiry_quality is not None and not expiry_quality.ok:
        ok = False
        states.append(f"expiry_close_quality={expiry_quality.use_state}")
    return ok, "PASS / LIVE" if ok else "BLOCKED — " + "; ".join(states)


def direction_evidence_score(snapshot: MarketSnapshot) -> float:
    """Presentation-only strength of the current directional core evidence.

    This does not create another strategy score. It simply exposes the matching
    bullish/bearish/range component already present in CoreMarketEvidence.
    """

    direction = snapshot.decision.market_direction
    if direction == "BULLISH":
        return float(snapshot.core_evidence.bullish_score)
    if direction == "BEARISH":
        return float(snapshot.core_evidence.bearish_score)
    return float(snapshot.core_evidence.range_score)


def unified_direction_line(snapshot: MarketSnapshot) -> str:
    """Explain canonical direction, fit and strongest weighted inputs only."""
    decision = snapshot.decision
    direction = str(decision.market_direction or "MIXED").upper()
    core_state = str(snapshot.core_evidence.market_state or "").upper()
    core_is_mixed = "MIXED" in core_state or "NO CLEAR" in core_state
    if core_is_mixed:
        label = "Market direction abhi mixed hai"
        names = ("CE BUY", "PE BUY", "CE SELL", "PE SELL", "IRON CONDOR")
        score = max(
            float(decision.ce_buy.score),
            float(decision.pe_buy.score),
            float(decision.ce_sell.score),
            float(decision.pe_sell.score),
            float(decision.iron_condor.score),
        )
    elif direction == "BULLISH":
        label = "Market upar ja sakta hai"
        names = ("PE SELL", "CE BUY")
        score = max(float(decision.pe_sell.score), float(decision.ce_buy.score))
    elif direction == "BEARISH":
        label = "Market neeche ja sakta hai"
        names = ("CE SELL", "PE BUY")
        score = max(float(decision.ce_sell.score), float(decision.pe_buy.score))
    elif direction == "RANGE":
        label = "Market range mein reh sakta hai"
        names = ("IRON CONDOR",)
        score = float(decision.iron_condor.score)
    else:
        label = "Market direction abhi mixed hai"
        names = ("CE SELL", "PE SELL", "IRON CONDOR")
        score = max(
            float(decision.ce_sell.score),
            float(decision.pe_sell.score),
            float(decision.iron_condor.score),
        )

    audit_name = max(
        names,
        key=lambda name: float(getattr(decision, name.lower().replace(" ", "_"), decision.iron_condor).score),
    )
    audit = dict(getattr(decision, "score_audit", {}).get(audit_name, {}))
    excluded = {"Base total", "Net adjustments / caps / rounding", "Final fit"}
    strongest = sorted(
        ((key, float(value)) for key, value in audit.items() if key not in excluded and float(value) > 0),
        key=lambda item: item[1],
        reverse=True,
    )[:3]
    why = ", ".join(f"{name} {points:.1f} pts" for name, points in strongest)
    if not why:
        why = "usable evidence abhi limited/mixed hai"
    lean_note = (
        f" Combined model ka current lean {direction} hai, lekin core clear nahi."
        if core_is_mixed and direction in {"BULLISH", "BEARISH"}
        else ""
    )
    live_note = "" if snapshot.market_session.is_live else "Last available data: "
    return (
        f"{live_note}{label} — Unified fit {score:.1f}/100.{lean_note} Karan: {why}. "
        "Yeh conditional evidence fit hai, profit probability nahi; entry gate alag hai."
    )


def brain_hinglish_line(snapshot: MarketSnapshot) -> str:
    """Explain the existing canonical decision in simple Hinglish.

    No score or strategy is recalculated here; all facts come from MarketSnapshot.
    """

    decision = snapshot.decision
    direction = decision.market_direction
    reasons: list[str] = []

    pa = snapshot.price_action.combined_state.upper()
    option_bias = snapshot.option_intelligence.market_bias.upper()
    heavy = snapshot.heavyweights.state.upper()
    inst = snapshot.institutional_context.state.upper()

    if direction == "BULLISH":
        if "BULL" in pa:
            reasons.append("Price Action bullish hai")
        if "BULL" in option_bias:
            reasons.append("Options/OI flow upar ki taraf support kar raha hai")
        if "BULL" in heavy or snapshot.heavyweights.advancing > snapshot.heavyweights.declining:
            reasons.append("Top-9 heavy stocks positive hain")
        if "SUPPORT" in inst or "FII BUYING" in inst or "FUTURES LONG" in inst:
            reasons.append("FII/DII background support de raha hai")
        base = (
            "Market upar ja sakta hai"
            if snapshot.market_session.is_live
            else "Last available data ke hisaab se market direction UP tha"
        )
        barrier = snapshot.barrier_map.nearest_resistance
        barrier_text = (
            f" Lekin {barrier.lower:,.0f}–{barrier.upper:,.0f} ke paas resistance hai."
            if barrier is not None and barrier.distance_points <= 100
            else ""
        )
    elif direction == "BEARISH":
        if "BEAR" in pa:
            reasons.append("Price Action bearish hai")
        if "BEAR" in option_bias:
            reasons.append("Options/OI flow neeche ki taraf pressure dikha raha hai")
        if "BEAR" in heavy or snapshot.heavyweights.declining > snapshot.heavyweights.advancing:
            reasons.append("Top-9 heavy stocks weak hain")
        if "PRESSURE" in inst or "FII SELLING" in inst or "FUTURES SHORT" in inst:
            short_pct = snapshot.institutional_context.latest_fii_futures_short_pct
            if short_pct is not None and short_pct >= 55:
                reasons.append(f"FII futures me {short_pct:.1f}% short position hai")
            else:
                reasons.append("FII side se pressure hai")
        base = (
            "Market neeche ja sakta hai"
            if snapshot.market_session.is_live
            else "Last available data ke hisaab se market direction DOWN tha"
        )
        barrier = snapshot.barrier_map.nearest_support
        barrier_text = (
            f" Lekin {barrier.lower:,.0f}–{barrier.upper:,.0f} ke paas support hai."
            if barrier is not None and barrier.distance_points <= 100
            else ""
        )
    else:
        base = (
            "Abhi market ka direction clear nahi hai"
            if snapshot.market_session.is_live
            else "Last available data me market ka direction clear nahi tha"
        )
        if "MIXED" in pa or "CONFLICT" in snapshot.price_action.relationship.upper():
            reasons.append("3m aur 15m Price Action ek jaisa signal nahi de rahe")
        if "MIXED" in option_bias or "RANGE" in option_bias:
            reasons.append("Options/OI flow mixed hai")
        barrier_text = ""

    news = snapshot.news_context
    if news.status == "READY" and news.risk_level in {"HIGH", "MEDIUM"}:
        if news.bias == "BEARISH":
            reasons.append("recent news me bearish risk hai")
        elif news.bias == "BULLISH":
            reasons.append("recent news supportive hai")
        else:
            reasons.append(f"recent news risk {news.risk_level.lower()} hai")
    elif news.status == "OLD":
        reasons.append("news purani hai isliye usko low weight diya gaya hai")

    if not reasons:
        reasons.append("available signals abhi mixed/limited hain")
    explanation = base + " kyunki " + ", ".join(reasons[:4]) + "." + barrier_text
    if decision.final_action in {"CE BUY", "PE BUY"}:
        explanation += " Brain ko directional momentum option buying ke liye seller setup se zyada suitable laga."
    elif decision.final_action in {"CE SELL WITH HEDGE", "PE SELL WITH HEDGE"}:
        explanation += " Brain ko directional move ke against protected premium selling zyada suitable lagi."
    elif decision.final_action == "IRON CONDOR WITH HEDGE":
        explanation += " Brain ko balanced range ke liye protected Iron Condor zyada suitable laga."
    if decision.final_action == "WAIT":
        if snapshot.market_session.is_live:
            explanation += " Isliye fresh entry ke liye abhi WAIT better hai jab tak confirmation strong na ho."
        else:
            explanation += " Market live nahi hai, isliye yeh reference-only reading hai; fresh entry permitted nahi hai."
    elif snapshot.execution_guard.readiness != "ENTRY READY":
        explanation += f" Setup {decision.final_action} hai, lekin entry abhi {snapshot.execution_guard.readiness} hai."
    return explanation


def _plan_by_name(snapshot: MarketSnapshot, name: str) -> SetupPlan | None:
    return {
        "CE BUY": snapshot.trade_plan.ce_buy,
        "PE BUY": snapshot.trade_plan.pe_buy,
        "CE SELL": snapshot.trade_plan.ce_sell,
        "PE SELL": snapshot.trade_plan.pe_sell,
        "IRON CONDOR": snapshot.trade_plan.iron_condor,
    }.get(name)


def best_existing_candidate(snapshot: MarketSnapshot) -> tuple[str, float, SetupPlan | None, bool]:
    """Expose the selected setup or highest same-brain reference candidate."""

    decision = snapshot.decision
    selected = decision.final_action.replace(" WITH HEDGE", "")
    scores = {
        "CE BUY": float(decision.ce_buy.score),
        "PE BUY": float(decision.pe_buy.score),
        "CE SELL": float(decision.ce_sell.score),
        "PE SELL": float(decision.pe_sell.score),
        "IRON CONDOR": float(decision.iron_condor.score),
    }
    if selected in scores:
        return selected, scores[selected], _plan_by_name(snapshot, selected), True
    allowed = compatible_strategies(decision.market_direction)
    eligible = {name: score for name, score in scores.items() if name in allowed}
    name = max(eligible or scores, key=(eligible or scores).get)
    return name, scores[name], _plan_by_name(snapshot, name), False


def plan_leg_text(legs: tuple[Any, ...]) -> str:
    if not legs:
        return "—"
    return " + ".join(f"{leg.strike:,.0f} {leg.side}" for leg in legs)


def snapshot_change_items(
    snapshot: MarketSnapshot,
    previous: MarketSnapshot | None,
) -> list[tuple[str, str, str | None]]:
    """Return compact read-only deltas for the UI.

    The third tuple element is a Streamlit metric delta string. Missing comparable
    values are intentionally omitted rather than invented.
    """

    if previous is None or previous.snapshot_id == snapshot.snapshot_id:
        return []
    if previous.created_at.date() != snapshot.created_at.date():
        return []

    items: list[tuple[str, str, str | None]] = []
    items.append(
        (
            "Entry Readiness",
            f"{snapshot.decision.decision_confidence:.0f}/100",
            f"{snapshot.decision.decision_confidence - previous.decision.decision_confidence:+.0f}",
        )
    )
    items.append(
        (
            "Market Speed",
            f"{snapshot.barrier_map.market_speed.score:.0f}/100",
            f"{snapshot.barrier_map.market_speed.score - previous.barrier_map.market_speed.score:+.0f}",
        )
    )

    current_r = snapshot.barrier_map.nearest_resistance
    prior_r = previous.barrier_map.nearest_resistance
    if current_r is not None and prior_r is not None:
        items.append(
            (
                "R1 bachne ki taakat",
                f"{current_r.strength:.0f}/100",
                f"{current_r.strength - prior_r.strength:+.0f}",
            )
        )
        items.append(
            (
                "R1 tootne ka pressure",
                f"{current_r.break_pressure:.0f}/100",
                f"{current_r.break_pressure - prior_r.break_pressure:+.0f}",
            )
        )
    else:
        items.append(
            (
                "Option Bullish Flow",
                f"{snapshot.option_intelligence.bullish_score:.0f}/100",
                f"{snapshot.option_intelligence.bullish_score - previous.option_intelligence.bullish_score:+.0f}",
            )
        )
    return items[:4]


def snapshot_change_hinglish(snapshot: MarketSnapshot, previous: MarketSnapshot | None) -> str:
    if previous is None or previous.created_at.date() != snapshot.created_at.date():
        return "Pehla comparable snapshot hai — badlav ka comparison next fresh snapshot se shuru hoga."

    notes: list[str] = []
    core_delta = snapshot.core_evidence.bullish_score - previous.core_evidence.bullish_score
    option_delta = snapshot.option_intelligence.bullish_score - previous.option_intelligence.bullish_score
    if core_delta >= 5 or option_delta >= 5:
        notes.append("buyers ka evidence stronger hua hai")
    elif core_delta <= -5 or option_delta <= -5:
        notes.append("buyers ka evidence weak hua hai")

    current_r = snapshot.barrier_map.nearest_resistance
    prior_r = previous.barrier_map.nearest_resistance
    if current_r is not None and prior_r is not None:
        strength_delta = current_r.strength - prior_r.strength
        pressure_delta = current_r.break_pressure - prior_r.break_pressure
        if strength_delta <= -5:
            notes.append("nearest resistance weak hua hai")
        elif strength_delta >= 5:
            notes.append("nearest resistance strong hua hai")
        if pressure_delta >= 7:
            notes.append("upar break pressure badha hai")
        elif pressure_delta <= -7:
            notes.append("upar break pressure kam hua hai")

    speed_delta = snapshot.barrier_map.market_speed.score - previous.barrier_map.market_speed.score
    if speed_delta >= 10:
        notes.append("market speed tez hui hai")
    elif speed_delta <= -10:
        notes.append("market speed calm hui hai")

    if not notes:
        return "Pichhle snapshot se koi bada structural badlav nahi; setup broadly same hai."
    return "Pichhle snapshot se " + ", ".join(notes[:4]) + "."
