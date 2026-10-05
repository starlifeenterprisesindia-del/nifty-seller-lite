from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from analysis.strategy_lab import (
    available_plans,
    build_strategy_lab_payload,
    build_strategy_repair_payload,
    what_if_scenario,
)


def _fmt(value: Any, digits: int = 1, prefix: str = "", suffix: str = "") -> str:
    try:
        if value is None:
            return "—"
        return f"{prefix}{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _be_text(values: list[float]) -> str:
    if not values:
        return "—"
    return " / ".join(f"{value:,.0f}" for value in values)


def _comparison_frame(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, item in payload.get("plans", {}).items():
        greeks = item.get("greeks") or {}
        rows.append({
            "Strategy": name,
            "Status": item.get("status"),
            "Quality": item.get("quality_score"),
            "Entry": f"{item.get('entry_type','—')} {_fmt(item.get('entry_points'), 2)}",
            "Max Profit /lot": item.get("max_profit_rupees_per_lot"),
            "Max Loss /lot": item.get("max_loss_rupees_per_lot"),
            "Breakeven": _be_text(item.get("breakevens") or []),
            "Net Delta": greeks.get("delta"),
            "Net Theta": greeks.get("theta"),
            "Liquidity floor": item.get("liquidity_floor"),
        })
    return pd.DataFrame(rows)


def render_phase4_strategy_lab(snapshot: Any) -> None:
    """Display-only Strategy Lab. Caller controls panel open/close."""
    payload = build_strategy_lab_payload(snapshot)
    st.caption(
        "🧪 Phase-4 Strategy Lab · protected plans already built by One Brain · "
        "no broker/API call · no score/threshold change"
    )
    if payload.get("status") != "READY":
        st.info("Strategy Lab abhi unavailable hai — protected option plan/spot data ready nahi hai.")
        return

    summaries = payload["plans"]
    plan_objects = available_plans(snapshot)
    names = list(summaries)
    preferred = payload.get("preferred") if payload.get("preferred") in names else names[0]
    default_index = names.index(preferred)
    selected = st.selectbox(
        "Strategy",
        names,
        index=default_index,
        key="phase4_strategy_lab_selected",
        help="Ye selection sirf analysis display badalta hai; One Brain ka final action nahi.",
    )
    item = summaries[selected]
    plan = plan_objects[selected]
    greeks = item.get("greeks") or {}

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Quality", _fmt(item.get("quality_score"), 0, suffix="/100"))
    c2.metric("Max Profit / lot", _fmt(item.get("max_profit_rupees_per_lot"), 0, prefix="₹"))
    c3.metric("Max Loss / lot", _fmt(item.get("max_loss_rupees_per_lot"), 0, prefix="₹"))
    c4.metric("Breakeven", _be_text(item.get("breakevens") or []))
    c5.metric("Liquidity floor", _fmt(item.get("liquidity_floor"), 0, suffix="/100"))

    st.caption(
        f"{item.get('entry_type')} {_fmt(item.get('entry_points'), 2)} pts · "
        f"Lot size {payload.get('lot_size')} · status {item.get('status')} · "
        f"Spot {_fmt(payload.get('spot'), 2)}"
    )

    tab_payoff, tab_greeks, tab_whatif, tab_compare = st.tabs(
        ["📈 Payoff", "🧬 Combined Greeks", "🎛️ Spot / IV / Time What-If", "⚖️ Compare"]
    )

    with tab_payoff:
        curve = item.get("curve") or []
        if curve:
            chart = pd.DataFrame(curve).set_index("spot")[["pnl_points"]]
            st.line_chart(chart, width="stretch", height=360)
            st.caption(
                "Expiry payoff points. Protected plan ke quoted entry assumptions use hote hain; "
                "brokerage, slippage, taxes aur execution drift included nahi hain."
            )
        legs = pd.DataFrame(item.get("legs") or [])
        if not legs.empty:
            legs = legs.rename(columns={
                "position": "Position", "side": "Side", "strike": "Strike",
                "entry_price": "Entry px", "liquidity": "Liquidity",
            })
            st.dataframe(legs, width="stretch", hide_index=True)

    with tab_greeks:
        g1, g2, g3, g4, g5 = st.columns(5)
        g1.metric("Net Delta", _fmt(greeks.get("delta"), 4))
        g2.metric("Net Gamma", _fmt(greeks.get("gamma"), 6))
        g3.metric("Net Theta/day", _fmt(greeks.get("theta"), 3))
        g4.metric("Net Vega / IV pt", _fmt(greeks.get("vega"), 3))
        g5.metric("Greek coverage", _fmt(greeks.get("coverage_pct"), 0, suffix="%"))
        greek_legs = pd.DataFrame(greeks.get("legs") or [])
        if not greek_legs.empty:
            st.dataframe(greek_legs, width="stretch", hide_index=True)
        st.caption("Combined Greeks current option-chain snapshot se read-only sum hain; execution signal nahi.")

    with tab_whatif:
        w1, w2, w3 = st.columns(3)
        spot_move = w1.slider(
            "Spot move (points)", -300, 300, 0, 10,
            key=f"phase4_spot_move_{selected}",
        )
        iv_move = w2.slider(
            "IV change (percentage points)", -8.0, 8.0, 0.0, 0.5,
            key=f"phase4_iv_move_{selected}",
        )
        minutes = w3.slider(
            "Time forward (minutes)", 0, 360, 30, 15,
            key=f"phase4_time_forward_{selected}",
        )
        scenario = what_if_scenario(
            plan,
            getattr(snapshot, "option_chain", None),
            spot=float(payload["spot"]),
            spot_change_points=float(spot_move),
            iv_change_points=float(iv_move),
            minutes_forward=float(minutes),
            lot_size=int(payload["lot_size"]),
        )
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Scenario spot", _fmt(scenario.get("new_spot"), 2))
        s2.metric("Approx ΔP&L", _fmt(scenario.get("pnl_change_points"), 2, suffix=" pts"))
        s3.metric("Approx ₹ / lot", _fmt(scenario.get("pnl_change_rupees_per_lot"), 0, prefix="₹"))
        s4.metric("Greek coverage", _fmt(scenario.get("coverage_pct"), 0, suffix="%"))
        if scenario.get("caution"):
            st.warning("Large scenario hai — Greek approximation nonlinear repricing se materially differ kar sakti hai.")
        st.caption(scenario.get("note") or "")
        rows = pd.DataFrame(scenario.get("legs") or [])
        if not rows.empty:
            st.dataframe(rows, width="stretch", hide_index=True)

    with tab_compare:
        table = _comparison_frame(payload)
        if not table.empty:
            st.dataframe(table, width="stretch", hide_index=True)
        st.caption(
            "Comparison One Brain ka action/ranking replace nahi karta. Ye payoff, risk, Greeks aur liquidity ko side-by-side dikhata hai."
        )

    st.info(
        "Golden Rule: Strategy Lab on-demand/display-only hai. Is panel ke calculations One Brain, "
        "TradePlan, execution gate, alerts ya broker calls ko modify nahi karte."
    )



def render_phase7_strategy_repair(snapshot: Any) -> None:
    """Advisory-only repair and advanced risk review for an already-open manual trade."""
    payload = build_strategy_repair_payload(snapshot)
    st.caption(
        "🛠️ Phase-7 Strategy Repair + Advanced Risk · open manual trade only · "
        "no broker/API call · no auto order · no One-Brain/SL mutation"
    )
    if payload.get("status") == "IDLE":
        st.info("Open trade record nahi hai — repair panel tab active hoga jab Position Guardian kisi manual/paper protected trade ko monitor kar raha ho.")
        return
    if payload.get("status") != "READY":
        st.warning("Repair intelligence unavailable hai; current snapshot/position data incomplete hai.")
        return

    state = str(payload.get("repair_state") or "HOLD / MONITOR")
    if "EXIT" in state or "VETO" in state or "BLOCKED" in state:
        st.error(f"**{state}**")
    elif "ROLL" in state or "HEDGE" in state or "WATCH" in state:
        st.warning(f"**{state}**")
    else:
        st.success(f"**{state}**")

    cur = payload.get("current") or {}
    align = payload.get("alignment") or {}
    barrier = payload.get("barrier_risk") or {}
    hedge = payload.get("hedge_execution") or {}
    risk = payload.get("current_risk") or {}
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Open strategy", payload.get("action") or "—")
    c2.metric("Current P&L", _fmt(risk.get("current_pnl_rupees"), 0, prefix="₹"))
    c3.metric("Direction alignment", str(align.get("state") or "—"), _fmt(align.get("score"), 2))
    c4.metric("Barrier risk", str(barrier.get("state") or "—"), _fmt(barrier.get("score"), 0, suffix="/100"))
    c5.metric("Hedge execution", str(hedge.get("state") or "—"), str(hedge.get("floor_grade") or ""))

    tabs = st.tabs(["🧭 Repair Gate", "🛡️ Risk Before / After", "🧬 Greeks Before / After", "🔁 Roll / Hedge Detail"])
    with tabs[0]:
        left, right = st.columns(2)
        with left:
            st.write("**Why / supportive evidence**")
            for reason in payload.get("reasons") or ["No extra supportive evidence"]:
                st.write(f"• {reason}")
        with right:
            st.write("**Repair veto / caution**")
            for reason in payload.get("vetoes") or ["None"]:
                st.write(f"• {reason}")
        st.caption(
            f"Guardian: {payload.get('guardian_instruction')} · entry spot {_fmt(cur.get('entry_spot'),2)} · "
            f"current spot {_fmt(cur.get('current_spot'),2)} · target progress {_fmt(cur.get('target_progress_pct'),1,suffix='%')}"
        )

    with tabs[1]:
        repl = payload.get("replacement_risk") or {}
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Original max loss", _fmt(risk.get("original_max_loss_points"), 2, suffix=" pts"))
        r2.metric("Remaining to original worst", _fmt(risk.get("remaining_to_original_worst_rupees"), 0, prefix="₹"))
        r3.metric("Fresh replacement max loss", _fmt(repl.get("candidate_max_loss_rupees"), 0, prefix="₹"))
        r4.metric("Day worst if close + replace", _fmt(repl.get("day_worst_if_replaced_after_close"), 0, prefix="₹"))
        if repl:
            st.caption(repl.get("assumption") or "")
        candidate = payload.get("candidate_summary") or {}
        if candidate:
            st.dataframe(pd.DataFrame([{
                "Candidate": candidate.get("name"),
                "Quality": candidate.get("quality_score"),
                "Max profit / lot ₹": candidate.get("max_profit_rupees_per_lot"),
                "Max loss / lot ₹": candidate.get("max_loss_rupees_per_lot"),
                "Liquidity floor": candidate.get("liquidity_floor"),
                "Breakevens": _be_text(candidate.get("breakevens") or []),
            }]), width="stretch", hide_index=True)

    with tabs[2]:
        before = payload.get("current_greeks") or {}
        after = payload.get("candidate_greeks") or {}
        rows = []
        for greek in ("delta", "gamma", "theta", "vega"):
            rows.append({
                "Greek": greek.upper(),
                "Current open position": before.get(greek),
                "Fresh replacement candidate": after.get(greek),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(
            f"Current Greek coverage {_fmt(before.get('coverage_pct'),0,suffix='%')} · "
            f"candidate coverage {_fmt(after.get('coverage_pct'),0,suffix='%')}. "
            "Candidate column assumes old trade is closed first; this is not a combined live roll fill."
        )

    with tabs[3]:
        roll = payload.get("roll") or {}
        changes = pd.DataFrame(roll.get("changes") or [])
        if not changes.empty:
            changes = changes.rename(columns={
                "side":"Side", "current_short":"Current short", "candidate_short":"Candidate short",
                "shift_points":"Shift pts", "outward":"Farther OTM?",
            })
            st.dataframe(changes, width="stretch", hide_index=True)
        else:
            st.info("Same-strategy outward roll candidate abhi available nahi hai.")
        hedge_rows = pd.DataFrame((payload.get("hedge_execution") or {}).get("rows") or [])
        if not hedge_rows.empty:
            st.write("**Current hedge execution quality**")
            st.dataframe(hedge_rows, width="stretch", hide_index=True)

    st.info(
        "Repair Golden Rule: existing SL/time/spot exit trigger ko repair override nahi karega. "
        "Old position ko close kiye bina fresh candidate ka theoretical risk combine karke executable roll claim nahi kiya jata."
    )
