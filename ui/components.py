from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd
import streamlit as st

from models import MarketLevel, MarketSnapshot, TimeframeIndicators


def render_market_session(snapshot: MarketSnapshot) -> None:
    session = snapshot.market_session
    if session.is_live:
        st.success(f"🟢 {session.label} — {session.message}")
    else:
        st.warning(f"🟡 {session.label} — {session.message}")


def render_header(snapshot: MarketSnapshot) -> None:
    quote = snapshot.nifty_quote
    last_price = quote.get("last_price")
    ohlc = quote.get("ohlc") or {}
    previous_close = ohlc.get("close")
    change_pct = None
    if previous_close not in (None, 0) and last_price is not None:
        change_pct = (
            (float(last_price) - float(previous_close)) / float(previous_close) * 100
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "NIFTY",
        f"{float(last_price):,.2f}" if last_price is not None else "—",
        f"{change_pct:+.2f}%" if change_pct is not None else None,
    )
    c2.metric("Expiry", snapshot.expiry or "Unavailable")
    c3.metric("Snapshot", snapshot.snapshot_id[-8:])
    c4.metric("Created", snapshot.created_at.strftime("%H:%M:%S IST"))


def render_decision(snapshot: MarketSnapshot) -> None:
    item = snapshot.decision
    st.subheader("Final One-Brain Decision")
    st.caption(
        "CE Sell, PE Sell and Iron Condor are independent suitability percentages. "
        "WAIT is a separate uncertainty/risk need, so the four values do not add to 100."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CE Sell", f"{item.ce_sell.score:.1f}%")
    c2.metric("PE Sell", f"{item.pe_sell.score:.1f}%")
    c3.metric("Iron Condor", f"{item.iron_condor.score:.1f}%")
    c4.metric("WAIT Need", f"{item.wait_need.score:.1f}%")

    message = (
        f"FINAL ACTION: {item.final_action} | Execution: {item.execution_status} | "
        f"Decision confidence: {item.decision_confidence:.1f}% | "
        f"Hedge required: {'YES' if item.hedge_required else 'NO'}"
    )
    if item.final_action == "WAIT":
        st.warning(message)
    else:
        st.success(message)

    left, right = st.columns(2)
    with left:
        st.write("**Top reasons**")
        for reason in item.reasons or ("No decisive evidence",):
            st.write(f"• {reason}")
    with right:
        st.write("**Main blocker**")
        st.write(f"• {item.blocker}")

    rows = []
    for strategy in (item.ce_sell, item.pe_sell, item.iron_condor, item.wait_need):
        rows.append(
            {
                "Setup": strategy.name,
                "Score / Need %": strategy.score,
                "Status": strategy.status,
                "Main evidence": " | ".join(strategy.reasons),
                "Cautions": " | ".join(strategy.cautions),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _leg_label(legs: tuple[Any, ...]) -> str:
    if not legs:
        return "—"
    return " + ".join(f"{leg.strike:,.0f} {leg.side}" for leg in legs)


def render_trade_plan(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.trade_plan
    st.subheader("Protected Strike Planner")
    st.caption(
        "This planner does not make a second strategy decision. It converts the final "
        "one-brain action into read-only short-strike and mandatory hedge candidates "
        "from the same option-chain snapshot. Credit and risk are point estimates using "
        "available bid/ask, with LTP only as fallback."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected Setup", bundle.selected_setup)
    c2.metric("Planner Status", bundle.status)
    c3.metric("Expiry", bundle.expiry or "—")
    c4.metric("Spot", f"{bundle.spot:,.2f}" if bundle.spot is not None else "—")

    plans = (bundle.ce_sell, bundle.pe_sell, bundle.iron_condor)
    rows = []
    for plan in plans:
        breakeven = "—"
        if plan.lower_breakeven is not None and plan.upper_breakeven is not None:
            breakeven = f"{plan.lower_breakeven:,.2f} to {plan.upper_breakeven:,.2f}"
        elif plan.lower_breakeven is not None:
            breakeven = f"Lower {plan.lower_breakeven:,.2f}"
        elif plan.upper_breakeven is not None:
            breakeven = f"Upper {plan.upper_breakeven:,.2f}"
        rows.append(
            {
                "Setup": plan.name,
                "Sell leg(s)": _leg_label(plan.short_legs),
                "Hedge leg(s)": _leg_label(plan.hedge_legs),
                "Est. credit pts": plan.estimated_credit_points,
                "Wing width pts": plan.width_points,
                "Est. max risk pts": plan.max_risk_points,
                "Breakeven": breakeven,
                "Quality": plan.quality_score,
                "Status": plan.status,
                "Blocker": plan.blocker,
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    chosen = {
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }.get(bundle.selected_setup)
    if chosen and chosen.available:
        st.write("**Selected-plan evidence**")
        for reason in chosen.reasons or ("No candidate reason available",):
            st.write(f"• {reason}")
    if bundle.blocker != "None":
        st.warning(f"Planner blocker: {bundle.blocker}")


def render_execution_guard(snapshot: MarketSnapshot) -> None:
    item = snapshot.execution_guard
    profile = snapshot.risk_profile
    state = snapshot.discipline_state
    st.subheader("Execution Guard & One-Trade Discipline")
    st.caption(
        "This is not a second strategy brain. It applies signal persistence, fresh-feed, "
        "entry-window, protected-risk budget and one-trade/day rules to the already "
        "selected final action. It never places or exits an order."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entry Readiness", item.readiness)
    c2.metric("Signal Persistence", item.signal_state)
    c3.metric("Risk Budget", f"₹{item.risk_budget_rupees:,.0f}")
    c4.metric("Allowed Lots", str(item.allowed_lots))

    message = (
        f"Setup: {item.selected_setup} | Entry window: {item.entry_window} | "
        f"Compulsory exit: {item.forced_exit_time} | "
        f"One-trade state: {state.last_outcome or 'NOT USED'}"
    )
    if item.readiness == "ENTRY READY":
        st.success(message)
    elif item.readiness == "WATCH":
        st.info(message)
    else:
        st.warning(message)

    risk_rows = [
        {
            "Capital ₹": profile.capital_rupees,
            "Risk %": profile.risk_pct,
            "Risk budget ₹": item.risk_budget_rupees,
            "Lot size": profile.lot_size,
            "Risk / lot ₹": item.risk_per_lot_rupees,
            "Budget lots": item.max_lots_by_budget,
            "Lot cap": item.max_lots_cap,
            "Allowed lots": item.allowed_lots,
            "Target capture pts": item.target_capture_points,
            "Target exit debit pts": item.target_exit_debit_points,
            "Target ₹": item.target_profit_rupees,
            "SL trigger pts": item.stop_loss_points,
            "SL exit debit pts": item.stop_exit_debit_points,
            "SL ₹": item.stop_loss_rupees,
        }
    ]
    st.dataframe(pd.DataFrame(risk_rows), use_container_width=True, hide_index=True)

    low = (
        f"Below {item.spot_invalidation_low:,.2f}"
        if item.spot_invalidation_low is not None
        else "—"
    )
    high = (
        f"Above {item.spot_invalidation_high:,.2f}"
        if item.spot_invalidation_high is not None
        else "—"
    )
    st.caption(
        f"Spot invalidation guide — downside: **{low}** | upside: **{high}**. "
        "Premium-based triggers are estimates; verify broker bid/ask and fills."
    )

    left, right = st.columns(2)
    with left:
        st.write("**Guard evidence**")
        for reason in item.reasons or ("No positive readiness evidence",):
            st.write(f"• {reason}")
    with right:
        st.write("**Guard blockers**")
        for blocker in item.blockers or ("None",):
            st.write(f"• {blocker}")


def render_feed_status(snapshot: MarketSnapshot) -> None:
    rows = []
    for key, status in snapshot.feed_status.items():
        rows.append(
            {
                "Feed": key,
                "Available": "YES" if status.ok else "NO",
                "Use": status.use_state,
                "Age sec": status.age_seconds,
                "Message": status.message,
                "Source": status.source,
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_core_evidence(snapshot: MarketSnapshot) -> None:
    item = snapshot.core_evidence
    st.caption(
        "Core Market Evidence is one input to the final brain. It combines only the "
        "completed-candle price, indicator, level and NIFTY-futures-volume modules."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bullish Evidence Index", f"{item.bullish_score:.1f}/100")
    c2.metric("Bearish Evidence Index", f"{item.bearish_score:.1f}/100")
    c3.metric("Range / Mixed Index", f"{item.range_score:.1f}/100")
    c4.metric("Evidence Confidence", f"{item.confidence:.1f}%")
    st.info(
        f"**Core state:** {item.market_state}  |  **Move stage:** {item.move_stage}  |  "
        f"**Status:** {item.status}"
    )
    left, right = st.columns(2)
    with left:
        st.write("**Main evidence**")
        for reason in item.reasons or ("No consolidated reason available",):
            st.write(f"• {reason}")
    with right:
        st.write("**Current blockers / cautions**")
        if item.blockers:
            for blocker in item.blockers:
                st.write(f"• {blocker}")
        else:
            st.write("• None in the core engine")


def _price_action_row(item: Any) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Structure": item.structure,
        "Current Event": item.event,
        "Move Stage": item.move_stage,
        "Last Swing High": item.last_swing_high,
        "Last Swing Low": item.last_swing_low,
        "Invalidation": item.invalidation_level,
        "ATR 14": item.atr14,
        "Bullish": item.bullish_score,
        "Bearish": item.bearish_score,
        "Range": item.range_score,
        "Confidence": item.confidence,
        "Status": item.status,
    }


def render_price_action(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.price_action
    st.caption(
        f"Cross-timeframe view: **{bundle.combined_state}** — {bundle.relationship} "
        f"(confidence {bundle.confidence:.1f}%)."
    )
    rows = [
        _price_action_row(bundle.three_minute),
        _price_action_row(bundle.fifteen_minute),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Price Action reasons"):
        st.json(
            {
                "3m": asdict(bundle.three_minute),
                "15m": asdict(bundle.fifteen_minute),
                "relationship": bundle.relationship,
            }
        )


def _level_row(level: MarketLevel | None, fallback: str) -> dict[str, Any]:
    if level is None:
        return {
            "Level": fallback,
            "Zone": "—",
            "Midpoint": None,
            "Distance": None,
            "Strength": None,
            "Status": "UNAVAILABLE",
            "Sources": "—",
        }
    return {
        "Level": f"{level.label} {level.side}",
        "Zone": f"{level.lower:,.2f}–{level.upper:,.2f}",
        "Midpoint": level.midpoint,
        "Distance": level.distance_points,
        "Strength": level.strength,
        "Status": level.status,
        "Sources": ", ".join(level.sources),
    }


def render_levels(snapshot: MarketSnapshot) -> None:
    item = snapshot.levels
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Upside Room",
        f"{item.upside_room:.1f} pts" if item.upside_room is not None else "—",
    )
    c2.metric(
        "Downside Room",
        f"{item.downside_room:.1f} pts" if item.downside_room is not None else "—",
    )
    c3.metric(
        "Opening Range High",
        f"{item.opening_range_high:,.2f}"
        if item.opening_range_high is not None
        else "—",
    )
    c4.metric(
        "Opening Range Low",
        f"{item.opening_range_low:,.2f}" if item.opening_range_low is not None else "—",
    )
    st.caption(
        f"Current position: **{item.current_position}** | Zone width: "
        f"{item.zone_width if item.zone_width is not None else '—'} points | Status: {item.status}"
    )
    rows = [
        _level_row(item.immediate_support, "Immediate Support"),
        _level_row(item.strong_support, "Strong Support"),
        _level_row(item.immediate_resistance, "Immediate Resistance"),
        _level_row(item.strong_resistance, "Strong Resistance"),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _volume_row(item: Any) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Current Volume": item.current_volume,
        "Time-Normalized Baseline": item.baseline_volume,
        "Relative Volume": item.relative_volume,
        "Volume State": item.volume_state,
        "Trend": item.volume_trend,
        "Price Candle": item.price_direction,
        "Move Support": item.move_support,
        "Baseline Samples": item.baseline_samples,
        "Confidence": item.confidence,
        "Status": item.status,
    }


def render_volume(snapshot: MarketSnapshot) -> None:
    item = snapshot.volume
    st.caption(
        "Volume uses the nearest NIFTY futures contract, not NIFTY index pseudo-volume. "
        "The baseline compares the same intraday time slot across prior sessions, with a "
        "recent-bar fallback only when necessary."
    )
    st.write(
        f"**Source:** {item.source} | **Overall view:** {item.overall_view} | "
        f"**Confidence:** {item.confidence:.1f}% | **Status:** {item.status}"
    )
    rows = [_volume_row(item.three_minute), _volume_row(item.fifteen_minute)]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_candles(snapshot: MarketSnapshot) -> None:
    tabs = st.tabs(
        [
            "NIFTY 3m",
            "NIFTY 15m",
            "NIFTY 1m",
            "Future 3m Volume",
            "Future 15m Volume",
        ]
    )
    with tabs[0]:
        st.caption(
            "3-minute candles are aggregated from Dhan 1-minute candles at 09:15 IST."
        )
        st.dataframe(
            snapshot.candles_3m.tail(30), use_container_width=True, hide_index=True
        )
    with tabs[1]:
        st.dataframe(
            snapshot.candles_15m.tail(30), use_container_width=True, hide_index=True
        )
    with tabs[2]:
        st.dataframe(
            snapshot.candles_1m.tail(30), use_container_width=True, hide_index=True
        )
    with tabs[3]:
        st.dataframe(
            snapshot.future_candles_3m.tail(30),
            use_container_width=True,
            hide_index=True,
        )
    with tabs[4]:
        st.dataframe(
            snapshot.future_candles_15m.tail(30),
            use_container_width=True,
            hide_index=True,
        )


def render_option_chain(snapshot: MarketSnapshot) -> None:
    if snapshot.option_chain.empty:
        st.warning("Option chain is unavailable in this snapshot.")
        return
    columns = [
        "strike",
        "side",
        "is_atm",
        "last_price",
        "oi",
        "day_oi_change",
        "volume",
        "previous_close_price",
        "day_price_change",
        "implied_volatility",
        "top_bid_price",
        "top_ask_price",
    ]
    available = [col for col in columns if col in snapshot.option_chain.columns]
    st.caption(
        "Raw option-chain fields from the same authoritative snapshot. Derived flow is shown "
        "in the Options Intelligence section above."
    )
    st.dataframe(
        snapshot.option_chain[available], use_container_width=True, hide_index=True
    )


def render_heavyweights(snapshot: MarketSnapshot) -> None:
    if not snapshot.heavyweight_quotes:
        st.info("Top-7 quotes are unavailable in this snapshot.")
        return
    rows: list[dict[str, Any]] = []
    for item in snapshot.heavyweight_quotes:
        ohlc = item.get("ohlc") or {}
        previous_close = ohlc.get("close")
        last = item.get("last_price")
        change_pct = None
        if previous_close not in (None, 0) and last is not None:
            change_pct = (
                (float(last) - float(previous_close)) / float(previous_close) * 100
            )
        rows.append(
            {
                "Symbol": item.get("symbol"),
                "Name": item.get("display_name"),
                "Last": last,
                "Change %": change_pct,
                "Day High": ohlc.get("high"),
                "Day Low": ohlc.get("low"),
                "Volume": item.get("volume"),
                "Security ID": item.get("security_id"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _indicator_row(item: TimeframeIndicators) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Close": item.close,
        "EMA 20": item.ema20,
        "EMA 50": item.ema50,
        "EMA State": item.ema_state,
        "MACD": item.macd,
        "Signal": item.macd_signal,
        "Histogram": item.macd_histogram,
        "MACD State": item.macd_state,
        "RSI 14": item.rsi14,
        "RSI State": item.rsi_state,
        "Status": item.status,
    }


def render_indicators(snapshot: MarketSnapshot) -> None:
    st.caption(
        "EMA/MACD/RSI use completed candles from the same authoritative snapshot. "
        "They remain evidence, not a standalone decision."
    )
    rows = [
        _indicator_row(snapshot.indicators.three_minute),
        _indicator_row(snapshot.indicators.fifteen_minute),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Indicator JSON"):
        st.json(
            {
                "3m": asdict(snapshot.indicators.three_minute),
                "15m": asdict(snapshot.indicators.fifteen_minute),
            }
        )


def render_option_intelligence(snapshot: MarketSnapshot) -> None:
    item = snapshot.option_intelligence
    st.caption(
        "Options Intelligence compares the current ATM±5 chain with bounded same-day "
        "snapshots. These are normalized option-evidence percentages consumed by the final brain."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bullish Option Flow", f"{item.bullish_score:.1f}%")
    c2.metric("Bearish Option Flow", f"{item.bearish_score:.1f}%")
    c3.metric("Mixed / Decay", f"{item.range_score:.1f}%")
    c4.metric("Flow Confidence", f"{item.confidence:.1f}%")
    st.info(
        f"**Option bias:** {item.market_bias} | **Persistence:** {item.persistence} | "
        f"**Basis:** {item.basis} | **Status:** {item.status}"
    )
    left, right = st.columns(2)
    with left:
        st.write("**Main option evidence**")
        for reason in item.reasons or ("No consolidated option reason available",):
            st.write(f"• {reason}")
    with right:
        st.write("**State blockers / cautions**")
        if item.blockers:
            for blocker in item.blockers:
                st.write(f"• {blocker}")
        else:
            st.write("• None")


def render_option_flow_matrix(snapshot: MarketSnapshot) -> None:
    rows = list(snapshot.option_intelligence.flow_rows)
    if not rows:
        st.info("Option flow matrix is unavailable in this snapshot.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_option_windows(snapshot: MarketSnapshot) -> None:
    rows = []
    for item in snapshot.option_intelligence.windows:
        rows.append(
            {
                "Window": item.label,
                "Target sec": item.target_seconds,
                "Actual age sec": item.actual_age_seconds,
                "CE OI Δ": item.ce_oi_delta,
                "PE OI Δ": item.pe_oi_delta,
                "CE Premium Δ": item.ce_premium_delta,
                "PE Premium Δ": item.pe_premium_delta,
                "CE Volume Δ": item.ce_volume_delta,
                "PE Volume Δ": item.pe_volume_delta,
                "Bias": item.bias,
                "Status": item.status,
            }
        )
    st.caption(
        "A 1m/3m/5m window is used only when a historical sample is close enough to "
        "that target. Distant old samples are rejected."
    )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_walls_and_pcr(snapshot: MarketSnapshot) -> None:
    item = snapshot.option_intelligence
    walls = []
    for wall in (item.ce_wall, item.pe_wall):
        walls.append(
            {
                "Side": wall.side,
                "Main Wall Strike": wall.strike,
                "Wall OI": wall.oi,
                "Previous Wall": wall.previous_strike,
                "Migration pts": wall.migration_points,
                "Strongest 3-Strike Cluster": wall.cluster_center,
                "Cluster OI": wall.cluster_oi,
                "Status": wall.status,
            }
        )
    st.dataframe(pd.DataFrame(walls), use_container_width=True, hide_index=True)
    pcr = item.pcr
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Near-ATM OI PCR",
        f"{pcr.near_atm_oi_pcr:.2f}" if pcr.near_atm_oi_pcr is not None else "—",
    )
    c2.metric(
        "Day Addition PCR",
        f"{pcr.day_addition_pcr:.2f}" if pcr.day_addition_pcr is not None else "—",
    )
    c3.metric(
        "Intraday Addition PCR",
        f"{pcr.intraday_addition_pcr:.2f}"
        if pcr.intraday_addition_pcr is not None
        else "—",
    )
    c4.metric(
        "Volume PCR", f"{pcr.volume_pcr:.2f}" if pcr.volume_pcr is not None else "—"
    )
    st.caption(f"PCR context: **{pcr.state}** | Status: {pcr.status}")


def render_heavyweight_intelligence(snapshot: MarketSnapshot) -> None:
    item = snapshot.heavyweights
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Top-7 Covered Weight", f"{item.covered_weight_pct:.2f}%")
    c2.metric(
        "Weighted Top-7 Move",
        f"{item.weighted_move_pct:+.3f}%"
        if item.weighted_move_pct is not None
        else "—",
    )
    c3.metric(
        "Est. Index Contribution",
        f"{item.estimated_index_contribution_pct:+.3f}%"
        if item.estimated_index_contribution_pct is not None
        else "—",
    )
    c4.metric("Breadth", f"{item.advancing}↑ / {item.declining}↓ / {item.unchanged}→")
    st.info(
        f"**Top-7 state:** {item.state} | **Confidence:** {item.confidence:.1f}% | "
        f"**Weight date:** {snapshot.metadata.get('top7_weight_date', '—')} | **Status:** {item.status}"
    )
    rows = [asdict(row) for row in item.rows]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_market_context(snapshot: MarketSnapshot) -> None:
    institutional = snapshot.institutional_context
    event = snapshot.event_risk
    st.caption(
        "FII/DII is background evidence only. Missing values remain missing and never become zero. "
        "Event risk affects the final brain only when medium/high risk is marked verified."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Latest FII ₹ cr",
        f"{institutional.latest_fii_net:,.1f}"
        if institutional.latest_fii_net is not None
        else "Missing",
    )
    c2.metric(
        "Latest DII ₹ cr",
        f"{institutional.latest_dii_net:,.1f}"
        if institutional.latest_dii_net is not None
        else "Missing",
    )
    c3.metric("Institutional State", institutional.state)
    c4.metric("Verified Event Risk", event.level)
    rows = [
        {
            "Window": "5 days",
            "FII net ₹ cr": institutional.fii_5d_net,
            "DII net ₹ cr": institutional.dii_5d_net,
        },
        {
            "Window": "10 days",
            "FII net ₹ cr": institutional.fii_10d_net,
            "DII net ₹ cr": institutional.dii_10d_net,
        },
        {
            "Window": "15 days",
            "FII net ₹ cr": institutional.fii_15d_net,
            "DII net ₹ cr": institutional.dii_15d_net,
        },
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.write(
        f"**Institutional status:** {institutional.status} | "
        f"**Observations:** {institutional.observations} | "
        f"**Confidence:** {institutional.confidence:.1f}%"
    )
    st.write(
        f"**Event status:** {event.status} | **Verified:** {'YES' if event.verified else 'NO'} | "
        f"**Note:** {event.note or 'None'}"
    )


def render_vix_context(snapshot: MarketSnapshot) -> None:
    item = snapshot.vix_context
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "India VIX", f"{item.last_price:.2f}" if item.last_price is not None else "—"
    )
    c2.metric(
        "VIX Change",
        f"{item.change_pct:+.2f}%" if item.change_pct is not None else "—",
    )
    c3.metric("VIX Regime", item.regime)
    c4.metric("Movement", item.movement)
    st.info(
        f"**Seller environment:** {item.seller_environment} | **Status:** {item.status}"
    )
