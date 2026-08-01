from __future__ import annotations

from dataclasses import asdict
from html import escape
from typing import Any

import pandas as pd
import streamlit as st

from analysis.evidence_matrix import build_compact_evidence_matrix
from analysis.spot_premium_calculator import calculate_spot_premium_range
from analysis.presentation_safety import (
    candidate_invalidation_text,
    display_main_blocker,
    market_rukh_display,
    normalized_news_display,
    safe_brain_hinglish_line,
)
from models import MarketLevel, MarketSnapshot, TimeframeIndicators
from services.summary_presenter import (
    best_existing_candidate,
    plan_leg_text,
    required_live_feed_state,
    snapshot_change_hinglish,
    snapshot_change_items,
)


def _barrier_sources(level: Any | None) -> str:
    if level is None or not level.sources:
        return "Data unavailable"
    return " + ".join(str(item) for item in level.sources[:4])


def _barrier_level_html(level: Any | None, *, css_class: str, fallback_label: str) -> str:
    if level is None:
        return (
            f'<div class="bm-level {css_class} muted">'
            f'<div class="bm-tag">{escape(fallback_label)}</div>'
            '<div class="bm-zone">Unresolved</div></div>'
        )
    state = escape(str(level.state))
    sources = escape(_barrier_sources(level))
    strength_width = max(0.0, min(100.0, float(level.strength)))
    pressure_width = max(0.0, min(100.0, float(level.break_pressure)))
    return (
        f'<div class="bm-level {css_class}">'
        f'<div class="bm-level-head"><span class="bm-tag">{escape(level.label)} · {escape(level.side.title())}</span>'
        f'<span class="bm-state">{state}</span></div>'
        f'<div class="bm-zone">{level.lower:,.0f}–{level.upper:,.0f}</div>'
        f'<div class="bm-bars">'
        f'<div><span>Strength</span><b>{level.strength:.0f}/100</b><div class="bm-track"><div class="bm-fill" style="width:{strength_width:.0f}%"></div></div></div>'
        f'<div><span>Break Pressure</span><b>{level.break_pressure:.0f}/100</b><div class="bm-track"><div class="bm-fill pressure" style="width:{pressure_width:.0f}%"></div></div></div>'
        f'</div>'
        f'<div class="bm-small">Distance {level.distance_points:,.0f} pts · Kyun: {sources}</div>'
        f'</div>'
    )


def render_barrier_map(snapshot: MarketSnapshot) -> None:
    item = snapshot.barrier_map
    st.subheader("🧭 Live Barrier + Range Map")
    st.caption(
        "Yeh top live road-map Support/Resistance, OI flow, price structure, volume, Top-7, "
        "market speed aur India VIX ko ek hi view me dikhata hai. Strength aur Break Pressure "
        "evidence scores hain — guaranteed probability nahi."
    )

    range_item = item.trading_range
    speed = item.market_speed
    range_text = (
        f"{range_item.lower:,.0f}–{range_item.upper:,.0f}"
        if range_item.lower is not None and range_item.upper is not None
        else "Unresolved"
    )
    remaining_move = (
        f"±{item.vix_expected_remaining_move_points:,.0f} pts"
        if item.vix_expected_remaining_move_points is not None
        else "—"
    )
    st.caption(
        f"Detail map: Range {range_text} | Confidence {range_item.confidence:.0f}/100 | "
        f"Speed {speed.state} {speed.score:.0f}/100 {speed.direction} | VIX remaining move {remaining_move}."
    )

    if speed.state == "DANGER":
        st.error(
            f"🚨 FAST MARKET DANGER — speed {speed.score:.0f}/100 ({speed.direction}). "
            "Fresh option selling me extra caution; barrier break fast ho sakta hai."
        )
    elif speed.state == "FAST":
        st.warning(
            f"⚡ Market FAST hai — speed {speed.score:.0f}/100 ({speed.direction}). "
            "Nearest barrier ki Break Pressure ko priority se dekho."
        )

    r2 = _barrier_level_html(item.next_resistance, css_class="res secondary", fallback_label="R2")
    r1 = _barrier_level_html(item.nearest_resistance, css_class="res primary", fallback_label="R1")
    s1 = _barrier_level_html(item.nearest_support, css_class="sup primary", fallback_label="S1")
    s2 = _barrier_level_html(item.next_support, css_class="sup secondary", fallback_label="S2")
    spot = f"{item.current_price:,.2f}" if item.current_price is not None else "—"
    range_state = escape(range_item.state)
    bias = escape(range_item.breakout_bias)
    range_pos = f"{range_item.position_pct:.0f}%" if range_item.position_pct is not None else "—"
    vix_daily = f"±{item.vix_expected_daily_move_points:,.0f} pts" if item.vix_expected_daily_move_points is not None else "—"
    vix_5 = f"{speed.vix_change_5m_pct:+.1f}%" if speed.vix_change_5m_pct is not None else "warming"
    vix_15 = f"{speed.vix_change_15m_pct:+.1f}%" if speed.vix_change_15m_pct is not None else "warming"
    volume_text = f"{speed.volume_ratio:.2f}x" if speed.volume_ratio is not None else "warming"

    barrier_html = (
        '<style>'
        '.bm-wrap{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;background:rgba(127,127,127,.035);margin:6px 0 10px}'
        '.bm-level{border-radius:12px;padding:10px 12px;margin:7px 0;border-left:6px solid}'
        '.bm-level.res{border-left-color:#d9534f;background:rgba(217,83,79,.08)}'
        '.bm-level.sup{border-left-color:#2e9d63;background:rgba(46,157,99,.08)}'
        '.bm-level.secondary{opacity:.84}'
        '.bm-level.primary{box-shadow:0 0 0 1px rgba(127,127,127,.10) inset}'
        '.bm-level.muted{opacity:.55}'
        '.bm-level-head{display:flex;justify-content:space-between;gap:12px;align-items:center}'
        '.bm-tag{font-size:.78rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase}'
        '.bm-state{font-size:.75rem;font-weight:700;opacity:.8}'
        '.bm-zone{font-size:1.45rem;font-weight:800;line-height:1.2;margin:3px 0 7px}'
        '.bm-bars{display:grid;grid-template-columns:1fr 1fr;gap:14px;font-size:.78rem}'
        '.bm-bars>div>span{opacity:.75;margin-right:6px}'
        '.bm-bars b{float:right}'
        '.bm-track{height:6px;border-radius:99px;background:rgba(127,127,127,.18);overflow:hidden;margin-top:4px}'
        '.bm-fill{height:100%;background:#6b7280;border-radius:99px}'
        '.bm-fill.pressure{background:#f59e0b}'
        '.bm-small{font-size:.78rem;opacity:.78;margin-top:7px}'
        '.bm-spot{margin:10px 0;padding:13px;border-radius:12px;text-align:center;border:2px solid rgba(80,130,255,.45);background:rgba(80,130,255,.08)}'
        '.bm-spot .price{font-size:1.55rem;font-weight:900}'
        '.bm-road{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px;font-size:.82rem}'
        '.bm-road>div{padding:8px;border-radius:9px;background:rgba(127,127,127,.07)}'
        '@media (max-width:900px){.bm-bars,.bm-road{grid-template-columns:1fr}}'
        '</style>'
        '<div class="bm-wrap">'
        + r2 + r1
        + f'<div class="bm-spot"><div>NIFTY CURRENT</div><div class="price">{spot}</div>'
          f'<div>{range_state} · Position {range_pos} · Break Bias {bias}</div></div>'
        + s1 + s2
        + '<div class="bm-road">'
          f'<div><b>India VIX Risk</b><br>{escape(item.vix_risk)} · Daily move {vix_daily}</div>'
          f'<div><b>VIX Speed</b><br>5m {vix_5} · 15m {vix_15}</div>'
          f'<div><b>Option Shock</b><br>{speed.option_shock_score:.0f}/100 · Volume {volume_text}</div>'
          '</div></div>'
    )
    # st.html is the correct Streamlit primitive for raw HTML/CSS. Using st.markdown
    # can make indented nested HTML appear as literal code on some deployments/themes.
    if hasattr(st, "html"):
        st.html(barrier_html)
    else:
        st.markdown(barrier_html, unsafe_allow_html=True)

    st.info("🧠 **Barrier Brain:** " + item.summary)
    if range_item.explanation:
        st.caption(range_item.explanation)
    if speed.reasons:
        st.caption("Speed reasons: " + " | ".join(speed.reasons))


def _calculator_contract_row(snapshot: MarketSnapshot, side: str, strike: float) -> pd.Series | None:
    frame = snapshot.option_chain
    if frame.empty or not {"side", "strike"}.issubset(frame.columns):
        return None
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    if rows.empty:
        return None
    rows["strike"] = pd.to_numeric(rows["strike"], errors="coerce")
    rows = rows.dropna(subset=["strike"])
    if rows.empty:
        return None
    idx = (rows["strike"] - float(strike)).abs().idxmin()
    return rows.loc[idx]



def _cell_float(row: pd.Series | None, name: str) -> float:
    if row is None:
        return 0.0
    value = pd.to_numeric(pd.Series([row.get(name)]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else 0.0


def _inr(value: float) -> str:
    sign = "-" if value < 0 else ""
    number = abs(float(value))
    whole, dot, fraction = f"{number:.2f}".partition(".")
    if len(whole) > 3:
        tail = whole[-3:]
        head = whole[:-3]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        whole = ",".join(reversed(groups)) + "," + tail
    return f"{sign}₹{whole}.{fraction}" if dot else f"{sign}₹{whole}"


def render_spot_premium_calculator(snapshot: MarketSnapshot) -> None:
    with st.expander("🧮 Spot-to-Premium Calculator — Range + IV + Time Value", expanded=False):
        st.caption(
            "Apni lower/upper NIFTY range aur expected IV change khud bharo. Calculator live option chain, "
            "Delta, Gamma, Theta, Vega, Dhan-chain IV aur bid-ask se probable premium/P&L zone banata hai. "
            "Intrinsic Value + Time Value breakdown aur 15/30/60-minute sideways decay bhi dikhata hai. "
            "Yeh read-only utility hai; Main AI decision ko touch nahi karta."
        )

        option_frame = snapshot.option_chain
        chain_state = snapshot.feed_status.get("option_chain")
        feed_state = str(getattr(chain_state, "use_state", "UNAVAILABLE") or "UNAVAILABLE").upper()
        live_spot = float(snapshot.nifty_quote.get("last_price") or 0.0)
        if option_frame.empty or live_spot <= 0:
            st.warning("Option chain ya NIFTY price available nahi hai. Fresh snapshot ke baad calculator use karo.")
            return

        top1, top2, top3 = st.columns(3)
        with top1:
            side = st.selectbox("Option type", ["CE", "PE"], key="spc_side")
        with top2:
            position = st.selectbox("Position", ["SELL", "BUY"], key="spc_position")
        side_rows = option_frame[option_frame["side"].astype(str).str.upper().eq(side)].copy()
        side_rows["strike"] = pd.to_numeric(side_rows["strike"], errors="coerce")
        strikes = sorted(float(value) for value in side_rows["strike"].dropna().unique())
        if not strikes:
            st.warning(f"{side} strikes option chain me available nahi hain.")
            return
        default_strike = min(strikes, key=lambda value: abs(value - live_spot))
        with top3:
            strike_key = f"spc_strike_{side}"
            if strike_key in st.session_state and st.session_state[strike_key] not in strikes:
                st.session_state.pop(strike_key, None)
            strike = st.selectbox(
                "Strike",
                strikes,
                index=strikes.index(default_strike),
                format_func=lambda value: f"{value:,.0f} {side}",
                key=strike_key,
            )

        row = _calculator_contract_row(snapshot, side, strike)
        chain_price = _cell_float(row, "last_price")
        bid = _cell_float(row, "top_bid_price")
        ask = _cell_float(row, "top_ask_price")
        delta = _cell_float(row, "delta")
        gamma = _cell_float(row, "gamma")
        theta = _cell_float(row, "theta")
        vega = _cell_float(row, "vega")
        iv = _cell_float(row, "implied_volatility")
        current_oi = _cell_float(row, "oi")
        day_oi_change = _cell_float(row, "day_oi_change")
        volume = _cell_float(row, "volume")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Chain premium", f"₹{chain_price:,.2f}" if chain_price > 0 else "—")
        m2.metric("Bid / Ask", f"₹{bid:,.2f} / ₹{ask:,.2f}" if bid or ask else "—")
        m3.metric("Delta / Gamma", f"{delta:.3f} / {gamma:.5f}" if delta or gamma else "—")
        m4.metric("Dhan IV / Theta / Vega", f"{iv:.2f} / {theta:.2f} / {vega:.2f}" if iv or theta or vega else "—")

        f1, f2, f3 = st.columns(3)
        f1.metric("Selected strike OI", f"{current_oi:,.0f}" if current_oi else "—")
        f2.metric("Day OI change", f"{day_oi_change:+,.0f}" if day_oi_change else "0")
        f3.metric("Volume", f"{volume:,.0f}" if volume else "—")
        st.caption(
            "Demand/flow context: OI, OI change aur volume market participation dikhate hain. "
            "Inka exact rupee effect alag se claim nahi kiya jata; live option-chain smile final price proxy hai."
        )

        contract_key = f"{side}_{int(round(strike))}"
        p1, p2, p3 = st.columns(3)
        with p1:
            use_live_spot = st.checkbox(
                "Current NIFTY live snapshot se lo",
                value=True,
                key="spc_use_live_spot",
            )
            if use_live_spot:
                current_spot = live_spot
                st.caption(f"Current NIFTY: {current_spot:,.2f}")
            else:
                current_spot = st.number_input(
                    "Manual current NIFTY",
                    min_value=1.0,
                    value=float(live_spot),
                    step=1.0,
                    key="spc_manual_current_spot",
                )
        with p2:
            use_chain_price = st.checkbox(
                "Current premium live chain se lo",
                value=True,
                key=f"spc_use_chain_{side}_{int(round(strike))}",
            )
            if use_chain_price:
                current_premium = chain_price
                st.caption(f"Current premium: ₹{current_premium:,.2f}")
            else:
                current_premium = st.number_input(
                    "Current option premium",
                    min_value=0.05,
                    value=float(max(chain_price, 0.05)),
                    step=0.05,
                    key=f"spc_manual_current_{contract_key}",
                )
        with p3:
            entry_premium = st.number_input(
                "Tumhari entry premium",
                min_value=0.05,
                value=float(max(chain_price, 0.05)),
                step=0.05,
                key=f"spc_entry_{contract_key}",
                help="BUY ki purchase price ya SELL ki sell price.",
            )

        default_lower = max(1.0, round((current_spot - 50.0) / 5.0) * 5.0)
        default_upper = round((current_spot + 50.0) / 5.0) * 5.0
        r1, r2, r3, r4 = st.columns(4)
        with r1:
            lower_spot = st.number_input(
                "Lower NIFTY range",
                min_value=1.0,
                value=float(default_lower),
                step=5.0,
                key="spc_lower",
                help="Apna support/target level khud bharo.",
            )
        with r2:
            upper_spot = st.number_input(
                "Upper NIFTY range",
                min_value=1.0,
                value=float(default_upper),
                step=5.0,
                key="spc_upper",
                help="Apna resistance/target level khud bharo.",
            )
        with r3:
            target_minutes = st.number_input(
                "Range tak expected time (minutes)",
                min_value=0,
                max_value=1440,
                value=15,
                step=5,
                key="spc_target_minutes",
                help="0 immediate move; zyada time par Theta adjust hoga.",
            )
        with r4:
            iv_change_points = st.number_input(
                "Expected IV change (points)",
                min_value=-20.0,
                max_value=20.0,
                value=0.0,
                step=0.5,
                key="spc_iv_change_points",
                help="Example: Dhan IV 10 se 12 expected ho to +2.0; 10 se 8 ho to -2.0.",
            )
            if iv > 0:
                st.caption(f"Target IV: {iv + float(iv_change_points):.2f}")
            else:
                st.caption("Valid Dhan IV/Vega na ho to IV effect apply nahi hoga.")

        q1, q2 = st.columns(2)
        with q1:
            lots = st.number_input(
                "Lots",
                min_value=1,
                max_value=100,
                value=1,
                step=1,
                key="spc_lots",
            )
        with q2:
            calculator_lot_size = st.number_input(
                "Lot size",
                min_value=1,
                max_value=500,
                value=int(snapshot.risk_profile.lot_size),
                step=1,
                key="spc_lot_size",
            )

        signature = (
            snapshot.snapshot_id,
            side,
            position,
            float(strike),
            float(current_spot),
            float(current_premium),
            float(entry_premium),
            float(lower_spot),
            float(upper_spot),
            int(target_minutes),
            float(iv_change_points),
            int(calculator_lot_size),
            int(lots),
            feed_state,
        )
        calculate = st.button("Calculate Premium Range", type="primary", width="stretch")
        result = None
        if calculate:
            try:
                result = calculate_spot_premium_range(
                    option_chain=option_frame,
                    side=side,
                    position=position,
                    strike=float(strike),
                    current_spot=float(current_spot),
                    current_premium=float(current_premium),
                    entry_premium=float(entry_premium),
                    lower_spot=float(lower_spot),
                    upper_spot=float(upper_spot),
                    target_minutes=int(target_minutes),
                    iv_change_points=float(iv_change_points),
                    lot_size=int(calculator_lot_size),
                    lots=int(lots),
                    feed_state=feed_state,
                )
                st.session_state.spc_result = result
                st.session_state.spc_signature = signature
            except Exception as exc:
                st.error(f"Calculator input check karo: {exc}")
        elif st.session_state.get("spc_signature") == signature:
            result = st.session_state.get("spc_result")

        if result is None:
            st.info("Range, time aur IV scenario bharne ke baad **Calculate Premium Range** dabao.")
            return

        if result.status == "LIVE ESTIMATE":
            st.success(f"LIVE option-chain estimate · Reliability {result.overall_reliability:.0f}/100")
        else:
            st.warning(
                f"REFERENCE ONLY · Reliability {result.overall_reliability:.0f}/100 — broker premium verify karo."
            )

        st.write("**Premium Breakdown — Abhi**")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Current premium", f"₹{result.current_premium:,.2f}")
        b2.metric("Intrinsic Value", f"₹{result.current_intrinsic_value:,.2f}")
        b3.metric("Time Value", f"₹{result.current_time_value:,.2f}")
        b4.metric("Time Value share", f"{result.current_time_value_share_pct:.1f}%")
        st.caption("Premium = Intrinsic Value + Time Value. Time Value me remaining time, IV aur demand/liquidity ka effect hota hai.")

        table_rows = []
        for estimate in (result.lower, result.current, result.upper):
            table_rows.append(
                {
                    "NIFTY scenario": f"{estimate.label} · {estimate.target_spot:,.0f}",
                    "Estimated premium": f"₹{estimate.best_price:,.2f}",
                    "Probable zone": f"₹{estimate.low_price:,.2f}–₹{estimate.high_price:,.2f}",
                    "Intrinsic": f"₹{estimate.intrinsic_value:,.2f}",
                    "Time Value": f"₹{estimate.time_value:,.2f}",
                    "Exit action": estimate.exit_action,
                    "P&L / qty": _inr(estimate.pnl_per_quantity),
                    f"P&L ({result.lots} lot)": _inr(estimate.total_pnl),
                    "Result": estimate.outcome,
                    "Reliability": f"{estimate.reliability:.0f}/100",
                }
            )
        st.dataframe(table_rows, width="stretch", hide_index=True)

        st.write("**Premium Kyun Badlega? — Contribution Breakdown**")
        driver_rows = []
        for estimate in (result.lower, result.upper):
            driver_rows.append(
                {
                    "Scenario": f"NIFTY {estimate.target_spot:,.0f}",
                    "Spot effect (Delta+Gamma)": _inr(estimate.spot_move_effect),
                    "Time effect (Theta)": _inr(estimate.theta_effect),
                    "IV effect (Vega)": _inr(estimate.iv_effect),
                    "Live chain/smile adjustment": _inr(estimate.chain_smile_effect),
                    "Final premium": f"₹{estimate.best_price:,.2f}",
                }
            )
        st.dataframe(driver_rows, width="stretch", hide_index=True)
        st.caption(
            "Live chain/smile adjustment actual same-expiry option prices ka residual hai. "
            "Isme market demand, skew, liquidity aur model difference ka combined proxy aa sakta hai; "
            "yeh OI/volume ka exact rupee attribution nahi hai."
        )

        st.write("**Sideways Time Decay — NIFTY aur IV same rahe to**")
        decay_rows = []
        for decay in result.decay_scenarios:
            decay_rows.append(
                {
                    "After": f"{decay.minutes} min",
                    "Estimated premium": f"₹{decay.estimated_premium:,.2f}",
                    "Premium change": _inr(decay.premium_change),
                    "Remaining Time Value": f"₹{decay.remaining_time_value:,.2f}",
                    "P&L / qty": _inr(decay.pnl_per_quantity),
                    f"P&L ({result.lots} lot)": _inr(decay.total_pnl),
                    "Result": decay.outcome,
                    "Reliability": f"{decay.reliability:.0f}/100",
                }
            )
        st.dataframe(decay_rows, width="stretch", hide_index=True)
        st.caption("Sideways table Theta-only hai: spot aur IV same maane gaye hain. Real market me IV/bid-ask badalne se result alag ho sakta hai.")

        st.info("🧠 **Calculator Samajh:** " + result.summary)
        target_iv_text = f"{result.target_iv:.2f}" if result.target_iv is not None else "—"
        st.caption(
            f"Model inputs: Current {result.current_spot:,.2f} | {result.strike:,.0f} {result.side} "
            f"{result.position} | Current premium ₹{result.current_premium:,.2f} | Entry ₹{result.entry_premium:,.2f} | "
            f"Time {result.target_minutes} min | Delta {result.current_delta if result.current_delta is not None else '—'} | "
            f"Theta {result.current_theta if result.current_theta is not None else '—'} | "
            f"Vega {result.current_vega if result.current_vega is not None else '—'} | "
            f"Dhan IV {result.current_iv if result.current_iv is not None else '—'} → Target IV {target_iv_text}"
        )
        st.caption(
            "IV/Greeks source note: calculator Dhan option-chain values use karta hai. "
            "HDFC Sky/Zerodha/Angel ka IV model, interest rate, timestamp ya rounding alag ho sakta hai; "
            "exact IV parity expected nahi hai. Premium/bid-ask ko final verification mano."
        )
        for warning in result.warnings:
            st.caption("• " + warning)

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


def render_evidence_matrix(snapshot: MarketSnapshot) -> None:
    st.subheader("All Features — Compact Evidence")
    st.caption(
        "8 compact rows same snapshot ki evidence dikhati hain. W/M aur Special Candle "
        "bounded confirmation ke roop mein Final One-Brain mein shamil hain; koi row "
        "alag BUY/SELL/WAIT action nahi deti."
    )
    rows = build_compact_evidence_matrix(snapshot)
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        row_height=38,
        column_config={
            "Module": st.column_config.TextColumn("Module", width="medium"),
            "Bullish %": st.column_config.ProgressColumn(
                "Bullish", min_value=0, max_value=100, format="%.0f%%", width="small"
            ),
            "Bearish %": st.column_config.ProgressColumn(
                "Bearish", min_value=0, max_value=100, format="%.0f%%", width="small"
            ),
            "Neutral %": st.column_config.ProgressColumn(
                "Neutral", min_value=0, max_value=100, format="%.0f%%", width="small"
            ),
            "Confidence %": st.column_config.ProgressColumn(
                "Confidence", min_value=0, max_value=100, format="%.0f%%", width="small"
            ),
            "Result": st.column_config.TextColumn("Current result", width="large"),
        },
    )



def render_pre_touch_barriers(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.pre_touch_barriers
    st.subheader("Pre-Touch Support / Resistance — Early Warning")
    st.caption(
        "Price ke level touch karke wapas aane ka wait nahi. Yeh existing structure + "
        "Previous Day/Opening Range + CE/PE OI wall ko mila kar pehle se probable zone dikhata hai."
    )
    if bundle.status != "READY":
        st.info("Pre-touch S/R abhi available nahi hai.")
        return

    left, right = st.columns(2)
    with left:
        support = bundle.support
        if support is None:
            st.info("Neeche probable support resolve nahi hua.")
        else:
            st.metric(
                "Probable Support",
                f"{support.lower:,.0f}–{support.upper:,.0f}",
                f"{support.distance_points:.0f} pts door",
            )
            st.caption(
                f"Strength {support.strength:.0f}% | {support.proximity} | "
                + " + ".join(support.sources[:4])
            )
            st.info(support.message)
    with right:
        resistance = bundle.resistance
        if resistance is None:
            st.info("Upar probable resistance resolve nahi hua.")
        else:
            st.metric(
                "Probable Resistance",
                f"{resistance.lower:,.0f}–{resistance.upper:,.0f}",
                f"{resistance.distance_points:.0f} pts door",
            )
            st.caption(
                f"Strength {resistance.strength:.0f}% | {resistance.proximity} | "
                + " + ".join(resistance.sources[:4])
            )
            st.warning(resistance.message)


def _level_summary(level: Any | None, *, fallback: str) -> str:
    if level is None:
        return f"{fallback}: unresolved"
    return (
        f"{level.lower:,.0f}–{level.upper:,.0f} | Strength {level.strength:.0f}/100 | "
        f"Break {level.break_pressure:.0f}/100 | {level.state}"
    )


def _compact_cards_html(cards: list[tuple[str, str, str]]) -> str:
    """Render small responsive cards that stay two-across on narrow phones."""

    blocks = []
    for label, value, note in cards:
        blocks.append(
            '<div class="mai-card">'
            f'<div class="mai-label">{escape(label)}</div>'
            f'<div class="mai-value">{escape(value)}</div>'
            f'<div class="mai-note">{escape(note)}</div>'
            '</div>'
        )
    return (
        '<style>'
        '.mai-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:4px 0 12px}'
        '.mai-card{min-width:0;border:1px solid rgba(127,127,127,.24);border-radius:12px;padding:10px 11px;background:rgba(127,127,127,.045)}'
        '.mai-label{font-size:.76rem;opacity:.72;margin-bottom:4px}'
        '.mai-value{font-size:1.18rem;font-weight:800;line-height:1.15;overflow-wrap:anywhere}'
        '.mai-note{font-size:.70rem;opacity:.68;margin-top:4px;line-height:1.25}'
        '@media (max-width:760px){.mai-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.mai-value{font-size:1.03rem}.mai-card{padding:9px}}'
        '</style><div class="mai-grid">' + ''.join(blocks) + '</div>'
    )


def _render_compact_cards(cards: list[tuple[str, str, str]]) -> None:
    html = _compact_cards_html(cards)
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)


def render_main_ai_market_view(
    snapshot: MarketSnapshot, previous_snapshot: MarketSnapshot | None = None
) -> None:
    """Top-screen summary of the existing canonical MarketSnapshot.

    No strategy is recalculated here. It only condenses the Final One-Brain, Barrier
    Map, execution guard and background contexts into a phone-friendly view.
    """

    decision = snapshot.decision
    barrier = snapshot.barrier_map
    speed = barrier.market_speed
    feed_ok, _feed_text = required_live_feed_state(snapshot)
    direction, direction_score, direction_note = market_rukh_display(snapshot)
    spot = snapshot.nifty_quote.get("last_price")

    st.subheader("🧠 Main AI — Market View")
    st.caption(
        "Ek hi One-Brain ka compact view: pehle decision, phir reason, phir proof. "
        "Neeche ke detailed sections audit/evidence ke liye hain."
    )

    with st.container(border=True):
        _render_compact_cards(
            [
                ("NIFTY", f"{float(spot):,.2f}" if spot is not None else "—", "Current / last available"),
                ("Market Rukh", direction, f"Evidence {direction_score:.0f}/100 · {direction_note}"),
                ("Final Action", decision.final_action, "One-Brain final action"),
                ("Entry Readiness", f"{decision.decision_confidence:.0f}/100", snapshot.execution_guard.readiness),
                ("Market Danger", f"{speed.state} {speed.score:.0f}/100", f"Speed direction {speed.direction}"),
                ("Data Status", "LIVE" if feed_ok else "REFERENCE ONLY", "Fresh entry only when required feeds are LIVE"),
            ]
        )

        st.info("🧠 **AI samajh:** " + safe_brain_hinglish_line(snapshot))

        # Keep technical feed names out of the main user screen. Full feed diagnostics
        # remain available in the detailed evidence/PDF audit.
        if feed_ok and snapshot.execution_guard.readiness == "ENTRY READY":
            st.success("Data Status: LIVE PASS — required feeds fresh hain aur entry guard ready hai.")
        elif snapshot.market_session.is_live:
            st.warning("Data Status: LIVE session hai, lekin entry ke required checks abhi complete nahi hain.")
        else:
            st.warning("Data Status: REFERENCE ONLY — market live nahi hai; fresh entry permitted nahi hai.")

        left, right = st.columns(2)
        with left:
            st.markdown("**🎯 Abhi ka Plan**")
            name, score, plan, is_selected = best_existing_candidate(snapshot)
            if decision.final_action == "WAIT":
                st.write("**Action:** WAIT — fresh entry abhi nahi.")
                candidate_prefix = "Best reference candidate"
            else:
                st.write(f"**Action:** {decision.final_action}")
                candidate_prefix = "Selected protected setup"

            if plan is not None and plan.available:
                st.write(
                    f"**{candidate_prefix}:** {name} | Strategy Suitability {score:.1f}/100 | "
                    f"Strike + Hedge Quality {plan.quality_score:.1f}/100"
                )
                st.write(
                    f"Sell: **{plan_leg_text(plan.short_legs)}**  →  Hedge: **{plan_leg_text(plan.hedge_legs)}**"
                )
                if not is_selected:
                    st.caption(
                        "Final Action WAIT hai; yeh sirf reference candidate hai, entry signal nahi."
                    )
                invalidation = candidate_invalidation_text(snapshot, name)
                if invalidation:
                    st.caption("Invalidation: " + invalidation)
            else:
                st.write("Protected strike candidate abhi reliable tarah resolve nahi hua.")
            st.caption(f"Main blocker: {display_main_blocker(snapshot)}")

        with right:
            st.markdown("**🧭 Aage ka Road Map**")
            st.write("🔴 **Next Resistance:** " + _level_summary(barrier.nearest_resistance, fallback="R1"))
            st.write("🟢 **Next Support:** " + _level_summary(barrier.nearest_support, fallback="S1"))
            if barrier.trading_range.breakout_bias == "UPSIDE RISK" and barrier.next_resistance is not None:
                st.caption(
                    f"R1 tootkar accept hua to next resistance {barrier.next_resistance.lower:,.0f}–"
                    f"{barrier.next_resistance.upper:,.0f} hai (Strength {barrier.next_resistance.strength:.0f}/100)."
                )
            elif barrier.trading_range.breakout_bias == "DOWNSIDE RISK" and barrier.next_support is not None:
                st.caption(
                    f"S1 tootkar accept hua to next support {barrier.next_support.lower:,.0f}–"
                    f"{barrier.next_support.upper:,.0f} hai (Strength {barrier.next_support.strength:.0f}/100)."
                )
            else:
                st.caption("Dono taraf ke next barriers Live Barrier Map me track ho rahe hain.")

        range_item = barrier.trading_range
        range_text = (
            f"{range_item.lower:,.0f}–{range_item.upper:,.0f}"
            if range_item.lower is not None and range_item.upper is not None
            else "Unresolved"
        )
        vix_daily = (
            f"±{barrier.vix_expected_daily_move_points:,.0f} pts"
            if barrier.vix_expected_daily_move_points is not None
            else "—"
        )
        vix_speed_bits = []
        if speed.vix_change_5m_pct is not None:
            vix_speed_bits.append(f"5m {speed.vix_change_5m_pct:+.1f}%")
        if speed.vix_change_15m_pct is not None:
            vix_speed_bits.append(f"15m {speed.vix_change_15m_pct:+.1f}%")
        vix_note = " · ".join(vix_speed_bits) if vix_speed_bits else "VIX speed warming"

        inst = snapshot.institutional_context
        if inst.observations <= 0:
            inst_text = "MISSING"
            inst_note = "Data feed nahi kiya / unavailable"
        elif "FII SELLING / DII ABSORPTION" in inst.state:
            inst_text, inst_note = "FII SELL / DII BUY", f"{inst.observations}/15 sessions"
        elif "FII BUYING / DII SELLING" in inst.state:
            inst_text, inst_note = "FII BUY / DII SELL", f"{inst.observations}/15 sessions"
        elif "NET INSTITUTIONAL SUPPORT" in inst.state:
            inst_text, inst_note = "NET SUPPORT", f"{inst.observations}/15 sessions"
        elif "NET INSTITUTIONAL PRESSURE" in inst.state:
            inst_text, inst_note = "NET PRESSURE", f"{inst.observations}/15 sessions"
        else:
            inst_text, inst_note = "MIXED", f"{inst.observations}/15 sessions"
        if inst.fii_futures_bias not in {"UNAVAILABLE", "BALANCED"}:
            inst_text += f" | FUT {inst.fii_futures_bias}"

        news = snapshot.news_context
        news_display = normalized_news_display(news)
        if news_display.status == "READY":
            news_text = f"{news_display.bias} / {news_display.risk}"
        else:
            news_text = news_display.status
        news_note = news_display.note

        _render_compact_cards(
            [
                ("Probable Range", range_text, f"Confidence {range_item.confidence:.0f}/100"),
                ("India VIX", f"{barrier.vix_risk} | {vix_daily}", vix_note),
                ("FII/DII", inst_text, inst_note),
                ("News", news_text, news_note),
            ]
        )

        st.markdown("**🔄 Last Snapshot Se Kya Badla**")
        changes = snapshot_change_items(snapshot, previous_snapshot)
        if changes:
            cards = [
                (label, value, f"Badlav {delta}" if delta else "No comparable delta")
                for label, value, delta in changes
            ]
            _render_compact_cards(cards)
        st.caption(snapshot_change_hinglish(snapshot, previous_snapshot))


def render_compact_protected_setup(snapshot: MarketSnapshot) -> None:
    """Show one compact protected setup row without duplicating the full planner."""

    st.subheader("Best Protected Setup — One-Brain Reference")
    st.caption(
        "Same Final One-Brain scores aur same option-chain snapshot. WAIT ho to candidate sirf reference hai; "
        "full planner neeche detail expander me rahega."
    )
    name, score, plan, is_selected = best_existing_candidate(snapshot)
    if plan is None or not plan.available:
        st.info("Protected CE/PE/Condor candidate abhi available nahi hai.")
        return

    row = {
        "Setup": name,
        "Strategy suitability /100": score,
        "Sell": plan_leg_text(plan.short_legs),
        "Buy hedge": plan_leg_text(plan.hedge_legs),
        "Credit pts": plan.estimated_credit_points,
        "Max risk pts": plan.max_risk_points,
        "Strike + hedge quality /100": plan.quality_score,
        "Status": plan.status if is_selected else "REFERENCE ONLY",
    }
    st.dataframe(pd.DataFrame([row]), width="stretch", hide_index=True)
    if snapshot.decision.final_action == "WAIT":
        st.info("Final Action WAIT hai — yeh strike pair sirf reference hai, entry signal nahi.")
    invalidation = candidate_invalidation_text(snapshot, name)
    if invalidation:
        st.caption("Invalidation: " + invalidation)


def render_best_protected_sells(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.trade_plan
    st.subheader("Best CE / PE Sell + Hedge")
    st.caption(
        "Yeh second brain nahi hai. Final One-Brain ke same option-chain snapshot se best short strike "
        "aur uske liye liquid farther-OTM hedge choose hota hai. Har fresh snapshot par update hota hai."
    )

    rows = []
    score_map = {
        "CE SELL": snapshot.decision.ce_sell.score,
        "PE SELL": snapshot.decision.pe_sell.score,
    }
    for plan in (bundle.ce_sell, bundle.pe_sell):
        short = plan.short_legs[0] if plan.short_legs else None
        hedge = plan.hedge_legs[0] if plan.hedge_legs else None
        rows.append(
            {
                "Setup": plan.name,
                "Brain score %": score_map.get(plan.name, 0.0),
                "Sell": f"{short.strike:,.0f} {short.side}" if short else "—",
                "Buy hedge": f"{hedge.strike:,.0f} {hedge.side}" if hedge else "—",
                "Credit pts": plan.estimated_credit_points,
                "Max risk pts": plan.max_risk_points,
                "Strike + hedge quality /100": plan.quality_score,
                "Status": plan.status,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    selected = snapshot.decision.final_action.replace(" WITH HEDGE", "")
    selected_plan = {"CE SELL": bundle.ce_sell, "PE SELL": bundle.pe_sell}.get(selected)
    if snapshot.decision.final_action == "WAIT":
        st.info("Brain ka final action WAIT hai — CE/PE strikes sirf reference hain, entry signal nahi.")
    elif selected_plan and selected_plan.available:
        short = selected_plan.short_legs[0]
        hedge = selected_plan.hedge_legs[0]
        st.success(
            f"Brain Pick: {selected} WITH HEDGE — SELL {short.strike:,.0f} {short.side} "
            f"+ BUY {hedge.strike:,.0f} {hedge.side} hedge | "
            f"Quality {selected_plan.quality_score:.0f}% | Est. credit {selected_plan.estimated_credit_points:.2f} pts"
        )
    else:
        st.warning("Brain ne directional setup choose kiya hai, lekin safe hedge wala valid spread abhi resolve nahi hua.")


def render_news_context(snapshot: MarketSnapshot) -> None:
    news = snapshot.news_context
    st.caption(
        "News article ki publication time se freshness judge hoti hai. 90–180 min old news low weight hai; "
        "180 min se purani/stale news ka decision weight zero rehta hai."
    )
    news_display = normalized_news_display(news)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("News Bias", news_display.bias)
    c2.metric("News Risk", news_display.risk)
    c3.metric("Headlines", len(news.headlines))
    c4.metric(
        "Newest Age",
        f"{news.newest_age_minutes:.0f} min" if news.newest_age_minutes is not None else "—",
    )
    if news_display.status == "READY":
        st.info(f"News status: READY — {news_display.note}. {news.summary}")
    elif news_display.status.startswith("OLD"):
        st.warning(f"News status: {news_display.status} — {news_display.note}")
    else:
        st.warning(f"News status: {news_display.status} — {news_display.note}")
    if news.headlines:
        rows = []
        for item in news.headlines:
            rows.append(
                {
                    "Age min": item.age_minutes,
                    "Impact": item.impact,
                    "Bias": item.bias,
                    "Headline": item.title,
                    "Source": item.source,
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_decision(snapshot: MarketSnapshot) -> None:
    item = snapshot.decision
    st.subheader("Final One-Brain Decision")
    st.caption(
        "CE Sell, PE Sell and Iron Condor are independent suitability percentages. "
        "WAIT is a separate uncertainty/risk need, so the four values do not add to 100."
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("CE Sell", f"{item.ce_sell.score:.1f}%")
    c2.metric("PE Sell", f"{item.pe_sell.score:.1f}%")
    c3.metric("Iron Condor", f"{item.iron_condor.score:.1f}%")
    c4.metric("WAIT Need", f"{item.wait_need.score:.1f}%")
    c5.metric("Signal State", item.signal_state)

    instant_note = (
        f" | Instant read: {item.instant_action}"
        if item.instant_action != item.final_action
        else ""
    )
    message = (
        f"FINAL ACTION: {item.final_action} | Execution: {item.execution_status} | "
        f"Decision confidence: {item.decision_confidence:.1f}% | "
        f"Hedge required: {'YES' if item.hedge_required else 'NO'}{instant_note}"
    )
    if item.final_action == "WAIT":
        st.warning(message)
    else:
        st.success(message)

    st.info("🧠 **Brain samjha raha hai:** " + safe_brain_hinglish_line(snapshot))

    left, right = st.columns(2)
    with left:
        st.write("**Top reasons**")
        for reason in item.reasons or ("No decisive evidence",):
            st.write(f"• {reason}")
    with right:
        st.write("**Main blocker**")
        st.write(f"• {display_main_blocker(snapshot)}")

    with st.expander("Decision evidence & cautions", expanded=False):
        rows = []
        for strategy in (item.ce_sell, item.pe_sell, item.iron_condor, item.wait_need):
            rows.append(
                {
                    "Setup": strategy.name,
                    "Score / Need %": strategy.score,
                    "Status": strategy.status,
                    "Key evidence": " | ".join(strategy.reasons),
                    "Cautions": " | ".join(strategy.cautions) or "None",
                }
            )
        st.dataframe(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            row_height=44,
            column_config={
                "Setup": st.column_config.TextColumn(width="small"),
                "Score / Need %": st.column_config.NumberColumn(
                    format="%.1f%%", width="small"
                ),
                "Status": st.column_config.TextColumn(width="small"),
                "Key evidence": st.column_config.TextColumn(width="large"),
                "Cautions": st.column_config.TextColumn(width="large"),
            },
        )


def render_market_outlook(snapshot: MarketSnapshot) -> None:
    item = snapshot.decision.outlook
    st.subheader("Next 5–15 Min Market Outlook")
    st.caption(
        "Conditional scenario weights from the same Final One-Brain Decision. "
        "They are not guaranteed price predictions. Signal memory and fake-move "
        "risk stop a single opposite snapshot from immediately flipping the action."
    )
    row = {
        "Bullish path %": item.bullish_path_pct,
        "Range path %": item.range_path_pct,
        "Bearish path %": item.bearish_path_pct,
        "Fake-move risk %": item.fake_move_risk,
        "Risk state": item.fake_move_state,
        "Signal memory": item.signal_memory,
        "Invalidation": item.invalidation_text,
        "Status": item.status,
    }
    st.dataframe(
        pd.DataFrame([row]),
        width="stretch",
        hide_index=True,
        column_config={
            "Bullish path %": st.column_config.ProgressColumn(
                "Bullish path", min_value=0, max_value=100, format="%.1f%%"
            ),
            "Range path %": st.column_config.ProgressColumn(
                "Range", min_value=0, max_value=100, format="%.1f%%"
            ),
            "Bearish path %": st.column_config.ProgressColumn(
                "Bearish path", min_value=0, max_value=100, format="%.1f%%"
            ),
            "Fake-move risk %": st.column_config.ProgressColumn(
                "Fake-move risk", min_value=0, max_value=100, format="%.1f%%"
            ),
        },
    )
    if item.reasons:
        st.caption("Fake-move checks: " + " | ".join(item.reasons))


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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

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
    st.dataframe(pd.DataFrame(risk_rows), width="stretch", hide_index=True)

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


def render_position_guardian(snapshot: MarketSnapshot) -> None:
    item = snapshot.position_guardian
    st.subheader("Position Guardian — Manual Trade Monitor")
    st.caption(
        "This monitor starts only after you manually mark a protected trade taken. "
        "It tracks the exact stored legs from the same full option-chain snapshot and "
        "shows deterministic target, SL, spot-invalidation and time-exit alerts. It "
        "cannot place, modify or exit a broker order."
    )

    if item.status == "IDLE":
        st.info(
            "No open trade is recorded. Position monitoring will start after an ENTRY READY setup is manually marked taken."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Guardian Instruction", item.instruction)
    c2.metric(
        "Current Combo Debit",
        f"{item.current_debit_points:.2f} pts"
        if item.current_debit_points is not None
        else "—",
    )
    c3.metric(
        "P&L Estimate",
        f"₹{item.unrealized_pnl_rupees:,.0f}"
        if item.unrealized_pnl_rupees is not None
        else "—",
        f"{item.unrealized_pnl_points:+.2f} pts"
        if item.unrealized_pnl_points is not None
        else None,
    )
    c4.metric(
        "Target Progress",
        f"{item.target_progress_pct:.1f}%"
        if item.target_progress_pct is not None
        else "—",
    )

    summary = (
        f"Action: {item.action or '—'} | Expiry: {item.expiry or '—'} | "
        f"Lots: {item.lots} × {item.lot_size} | Entry credit: "
        f"{item.entry_credit_points:.2f} pts"
        if item.entry_credit_points is not None
        else f"Action: {item.action or '—'} | Expiry: {item.expiry or '—'} | Lots: {item.lots} × {item.lot_size}"
    )
    if item.status in {"EXIT ALERT", "TARGET ALERT"}:
        st.warning(summary)
    elif item.status == "DATA BLOCKED":
        st.error(summary)
    elif item.status == "CLOSED":
        st.info(summary)
    else:
        st.success(summary)

    rule_rows = [
        {
            "Entry spot": item.entry_spot,
            "Current spot": item.current_spot,
            "Target debit": item.target_exit_debit_points,
            "SL debit": item.stop_exit_debit_points,
            "Spot invalidation low": item.spot_invalidation_low,
            "Spot invalidation high": item.spot_invalidation_high,
            "Compulsory exit": item.forced_exit_time,
            "Status": item.status,
        }
    ]
    st.dataframe(pd.DataFrame(rule_rows), width="stretch", hide_index=True)

    if item.legs:
        leg_rows = [
            {
                "Role": leg.role,
                "Side": leg.side,
                "Strike": leg.strike,
                "Entry price": leg.entry_price,
                "Current close price": leg.current_price,
                "P&L contribution pts": leg.pnl_contribution_points,
                "Status": leg.status,
            }
            for leg in item.legs
        ]
        st.dataframe(pd.DataFrame(leg_rows), width="stretch", hide_index=True)

    left, right = st.columns(2)
    with left:
        st.write("**Guardian evidence**")
        for reason in item.reasons or ("No active guardian reason",):
            st.write(f"• {reason}")
    with right:
        st.write("**Guardian blockers**")
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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
        st.dataframe(snapshot.candles_3m.tail(30), width="stretch", hide_index=True)
    with tabs[1]:
        st.dataframe(snapshot.candles_15m.tail(30), width="stretch", hide_index=True)
    with tabs[2]:
        st.dataframe(snapshot.candles_1m.tail(30), width="stretch", hide_index=True)
    with tabs[3]:
        st.dataframe(
            snapshot.future_candles_3m.tail(30),
            width="stretch",
            hide_index=True,
        )
    with tabs[4]:
        st.dataframe(
            snapshot.future_candles_15m.tail(30),
            width="stretch",
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
    st.dataframe(snapshot.option_chain[available], width="stretch", hide_index=True)


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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
    st.dataframe(pd.DataFrame(walls), width="stretch", hide_index=True)
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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_market_context(snapshot: MarketSnapshot) -> None:
    institutional = snapshot.institutional_context
    event = snapshot.event_risk
    st.caption(
        "FII/DII cash main background context hai. FII Index Futures me contracts sirf size dikhate hain; "
        "direction Long % / Short % se nikalti hai aur ye secondary confirmation hai. "
        "Same-date save update hota hai; latest 15 dates primary + mirror journal me rehti hain."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Latest FII cash ₹ cr",
        f"{institutional.latest_fii_net:,.1f}"
        if institutional.latest_fii_net is not None
        else "Missing",
    )
    c2.metric(
        "Latest DII cash ₹ cr",
        f"{institutional.latest_dii_net:,.1f}"
        if institutional.latest_dii_net is not None
        else "Missing",
    )
    c3.metric(
        "FII Futures contracts",
        f"{institutional.latest_fii_index_futures_contracts:,.0f}"
        if institutional.latest_fii_index_futures_contracts is not None
        else "Missing",
    )
    futures_split = (
        f"Long {institutional.latest_fii_futures_long_pct:.2f}% | Short {institutional.latest_fii_futures_short_pct:.2f}%"
        if institutional.latest_fii_futures_long_pct is not None
        and institutional.latest_fii_futures_short_pct is not None
        else "Long/Short missing"
    )
    c4.metric("FII Futures Bias", institutional.fii_futures_bias, delta=futures_split, delta_color="off")
    st.info(f"**Institutional State:** {institutional.state} | **Verified Event Risk:** {event.level}")
    rows = [
        {
            "Window": "5 sessions",
            "FII cash net ₹ cr": institutional.fii_5d_net,
            "DII cash net ₹ cr": institutional.dii_5d_net,
            "FII Futures Long avg %": institutional.fii_futures_5d_long_avg_pct,
            "FII Futures Short avg %": institutional.fii_futures_5d_short_avg_pct,
        },
        {
            "Window": "10 sessions",
            "FII cash net ₹ cr": institutional.fii_10d_net,
            "DII cash net ₹ cr": institutional.dii_10d_net,
            "FII Futures Long avg %": institutional.fii_futures_10d_long_avg_pct,
            "FII Futures Short avg %": institutional.fii_futures_10d_short_avg_pct,
        },
        {
            "Window": "15 sessions",
            "FII cash net ₹ cr": institutional.fii_15d_net,
            "DII cash net ₹ cr": institutional.dii_15d_net,
            "FII Futures Long avg %": institutional.fii_futures_15d_long_avg_pct,
            "FII Futures Short avg %": institutional.fii_futures_15d_short_avg_pct,
        },
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.write(
        f"**Institutional status:** {institutional.status} | "
        f"**Observations:** {institutional.observations}/15 | "
        f"**As of:** {institutional.as_of_date or '—'} | "
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
    message = (
        f"**Seller environment:** {item.seller_environment} | **Status:** {item.status}"
    )
    if item.status != "READY":
        st.warning(
            f"{message} — zero or missing VIX is treated as unavailable, never as a "
            "balanced premium environment."
        )
    else:
        st.info(message)
