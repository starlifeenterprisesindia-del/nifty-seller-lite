from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from analysis.strategy_lab import available_plans, build_strategy_lab_payload, what_if_scenario


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
            st.line_chart(chart, use_container_width=True, height=360)
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
            st.dataframe(legs, use_container_width=True, hide_index=True)

    with tab_greeks:
        g1, g2, g3, g4, g5 = st.columns(5)
        g1.metric("Net Delta", _fmt(greeks.get("delta"), 4))
        g2.metric("Net Gamma", _fmt(greeks.get("gamma"), 6))
        g3.metric("Net Theta/day", _fmt(greeks.get("theta"), 3))
        g4.metric("Net Vega / IV pt", _fmt(greeks.get("vega"), 3))
        g5.metric("Greek coverage", _fmt(greeks.get("coverage_pct"), 0, suffix="%"))
        greek_legs = pd.DataFrame(greeks.get("legs") or [])
        if not greek_legs.empty:
            st.dataframe(greek_legs, use_container_width=True, hide_index=True)
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
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with tab_compare:
        table = _comparison_frame(payload)
        if not table.empty:
            st.dataframe(table, use_container_width=True, hide_index=True)
        st.caption(
            "Comparison One Brain ka action/ranking replace nahi karta. Ye payoff, risk, Greeks aur liquidity ko side-by-side dikhata hai."
        )

    st.info(
        "Golden Rule: Strategy Lab on-demand/display-only hai. Is panel ke calculations One Brain, "
        "TradePlan, execution gate, alerts ya broker calls ko modify nahi karte."
    )
