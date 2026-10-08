from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from analysis.spot_premium_calculator import (
    calculate_spot_premium_range,
    calculate_target_premium,
    estimate_target_reach,
)
from analysis.sl_target_planner import (
    build_sl_target_plan,
    directional_intent,
    expiry_context,
    stop_buffer_points,
    stop_reference,
    stop_spot_price,
)
from analysis.smart_entry_advisor import build_smart_entry_advisor
from analysis.iv_delta_display import compute_iv_delta_payload
from models import MarketSnapshot
from ui.strike_entry import prepare_strike_entry, render_strike_entry_result, reset_strike_entry_state, strike_entry_state_key


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}₹{value:,.0f}"


def _premium_zone(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "—"
    return f"₹{low:,.2f} – ₹{high:,.2f}"


def _render_smart_entry_advisor(advisor: Any, *, side: str, position: str, strike: float) -> None:
    """One-glance default view; calculations remain available below."""
    st.markdown("### 🎯 Smart Entry Advisor")
    st.caption(f"{strike:,.0f} {side} · {position} · price + timing + no-chase advisory")
    if advisor.status_tone == "SUCCESS":
        st.success(f"**{advisor.status}**")
    elif advisor.status_tone == "DANGER":
        st.error(f"**{advisor.status}**")
    elif advisor.status_tone == "REFERENCE":
        st.info(f"**{advisor.status}**")
    else:
        st.warning(f"**{advisor.status}**")

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Current executable", f"₹{advisor.current_executable_premium:,.2f}")
    a2.metric("Best entry zone", _premium_zone(advisor.preferred_low, advisor.preferred_high))
    a3.metric("Acceptable", _premium_zone(advisor.acceptable_low, advisor.acceptable_high))
    chase = "—"
    if advisor.no_chase_level is not None:
        chase = f"{advisor.no_chase_relation.title()} ₹{advisor.no_chase_level:,.2f}"
    a4.metric("No chase", chase)

    b1, b2, b3 = st.columns(3)
    b1.metric("Entry price", advisor.price_quality_state)
    b2.metric("Move urgency", advisor.move_urgency_state)
    b3.metric("Institutional Window", advisor.institutional_state)

    reason_text = " · ".join(str(x) for x in advisor.reasons[:3])
    if reason_text:
        st.caption("Why: " + reason_text)
    magnet_score = "—" if advisor.liquidity_magnet_score is None else f"{advisor.liquidity_magnet_score:.0f}/100"
    st.caption(
        f"Liquidity Magnet: {advisor.liquidity_magnet_bias} {magnet_score} · "
        f"Price quality {advisor.price_quality_score:.0f}/100 · Move urgency {advisor.move_urgency_score:.0f}/100"
    )


def _contract_row(snapshot: MarketSnapshot, side: str, strike: float) -> pd.Series | None:
    frame = snapshot.option_chain
    if frame.empty or not {"side", "strike"}.issubset(frame.columns):
        return None
    rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    numeric = pd.to_numeric(rows["strike"], errors="coerce")
    exact = rows[numeric.sub(float(strike)).abs() < 0.01]
    return None if exact.empty else exact.iloc[0]


def _cell(row: pd.Series | None, name: str) -> float:
    return _number(row.get(name)) if row is not None else 0.0


def _barrier_targets(snapshot: MarketSnapshot) -> list[tuple[str, Any]]:
    item = snapshot.barrier_map
    return [
        ("R2", item.next_resistance),
        ("R1", item.nearest_resistance),
        ("S1", item.nearest_support),
        ("S2", item.next_support),
    ]


def _target_bundle(
    snapshot: MarketSnapshot,
    *,
    label: str,
    target_spot: float,
    strength: float,
    break_pressure: float,
    side: str,
    position: str,
    strike: float,
    current_spot: float,
    current_premium: float,
    entry_premium: float,
    lot_size: int,
    lots: int,
    feed_state: str,
    iv_change_points: float,
    minutes_to_expiry: int | None,
    holding_limit_minutes: int,
) -> dict[str, Any]:
    speed = snapshot.barrier_map.market_speed
    reach = estimate_target_reach(
        current_spot=current_spot,
        target_spot=target_spot,
        speed_score=speed.score,
        speed_direction=speed.direction,
        move_1m_points=speed.move_1m_points,
        move_3m_points=speed.move_3m_points,
        move_5m_points=speed.move_5m_points,
        expected_remaining_move_points=snapshot.barrier_map.vix_expected_remaining_move_points,
        barrier_strength=strength,
        break_pressure=break_pressure,
    )
    minutes = max(1, round((reach.minutes_low + reach.minutes_high) / 2))
    minutes = min(minutes, max(1, int(holding_limit_minutes)))
    if minutes_to_expiry is not None:
        minutes = min(minutes, max(0, int(minutes_to_expiry)))
    premium = calculate_target_premium(
        option_chain=snapshot.option_chain,
        side=side,
        position=position,
        strike=strike,
        current_spot=current_spot,
        current_premium=current_premium,
        entry_premium=entry_premium,
        target_spot=target_spot,
        target_minutes=minutes,
        lot_size=lot_size,
        lots=lots,
        feed_state=feed_state,
        iv_change_points=iv_change_points,
        minutes_to_expiry=minutes_to_expiry,
    )
    return {
        "level": label,
        "target": target_spot,
        "eta": f"{reach.minutes_low}–{reach.minutes_high}m",
        "chance": reach.probability_pct,
        "eta_low": reach.minutes_low,
        "eta_high": reach.minutes_high,
        "target_minutes": minutes,
        "premium": premium,
    }


def _holding_limit(snapshot: MarketSnapshot, selection: str, expiry_minutes: int | None) -> int:
    if selection == "1 din":
        limit = 1440
    elif selection == "2 din":
        limit = 2880
    elif selection == "3 din":
        limit = 4320
    else:
        current = snapshot.created_at
        close = current.replace(hour=15, minute=30, second=0, microsecond=0)
        limit = max(0, int((close - current).total_seconds() // 60))
        if limit <= 0:
            limit = 240
    if expiry_minutes is not None:
        limit = min(limit, max(0, expiry_minutes))
    return max(1, limit)


def render_spot_premium_calculator(snapshot: MarketSnapshot, option_state_store: Any | None = None) -> None:
    frame = snapshot.option_chain
    live_spot = _number(snapshot.nifty_quote.get("last_price"))
    if frame.empty or live_spot <= 0:
        st.warning("NIFTY ya option-chain data available nahi hai.")
        return

    c1, c2, c3 = st.columns(3)
    side = c1.selectbox("Option", ["CE", "PE"], key="spc2_side")
    position = c2.selectbox("Position", ["SELL", "BUY"], key="spc2_position")
    side_rows = frame[frame["side"].astype(str).str.upper().eq(side)].copy()
    strikes = sorted(
        float(value)
        for value in pd.to_numeric(side_rows["strike"], errors="coerce").dropna().unique()
    )
    if not strikes:
        st.warning(f"{side} strikes available nahi hain.")
        return
    default_strike = min(strikes, key=lambda value: abs(value - live_spot))
    strike = c3.selectbox(
        "Strike",
        strikes,
        index=strikes.index(default_strike),
        format_func=lambda value: f"{value:,.0f} {side}",
        key=f"spc2_strike_{side}",
    )

    row = _contract_row(snapshot, side, strike)
    chain_price = _cell(row, "last_price")
    if chain_price <= 0:
        st.warning("Selected strike ka premium available nahi hai.")
        return
    quality = str(row.get("greeks_quality", ""))
    projection_blocked = bool(quality and quality not in {"READY", "IV WARNING"})
    if projection_blocked:
        st.warning(
            f"Current premium ₹{chain_price:,.2f}; Greeks invalid/unavailable hain. "
            "Smart Entry Advisor book/barrier context dikha sakta hai, lekin future premium/SL-target projection blocked rahegi."
        )
    if quality == "IV WARNING":
        st.warning("CE/PE IV difference: neeche premiums sirf conditional scenarios hain, verified entry/SL prices nahi. Automatic retest estimate disabled; source values force-match nahi ki gayi.")
    chain_state = snapshot.feed_status.get("option_chain")
    feed_state = str(getattr(chain_state, "use_state", "UNAVAILABLE") or "UNAVAILABLE").upper()

    expiry_info = expiry_context(captured_at=snapshot.created_at, expiry=snapshot.expiry)
    planner_mode = st.selectbox(
        "Calculator mode",
        ["Plan new entry", "Already entered — actual fill"],
        key="spc_planner_mode",
    )
    lot_size = int(snapshot.risk_profile.lot_size)
    advisor = None
    planner_result = None
    hedge = None

    if planner_mode == "Plan new entry":
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Current premium", f"₹{chain_price:,.2f}")
        q2.metric("Current NIFTY", f"{live_spot:,.2f}")
        lots = q3.number_input("Lots", min_value=1, max_value=100, value=1, step=1, key="spc2_lots")
        q4.metric("Expiry / Time", expiry_info.label)

        planner_result, hedge = prepare_strike_entry(
            snapshot, side, position, strike, lots, compact=True
        )
        if planner_result is None:
            st.warning("Planner setup unavailable — quantity/protective hedge check karo.")
            return
        advisor = build_smart_entry_advisor(
            snapshot,
            side=side,
            position=position,
            strike=float(strike),
            lots=int(lots),
            planner=planner_result,
            hedge_strike=hedge,
        )
        _render_smart_entry_advisor(advisor, side=side, position=position, strike=float(strike))

        entry_spot = (
            float(live_spot)
            if advisor.status.startswith("FAST MOVE") or not advisor.spot_zone
            else float(sum(advisor.spot_zone) / 2.0)
        )
        entry_premium = float(advisor.reference_entry)

        with st.expander("Advanced Entry Detail", expanded=False):
            st.caption(
                "Useful detail hidden nahi hai — planner, ladder, net credit, structural invalidation aur exact scores yahan hain."
            )
            render_strike_entry_result(planner_result, hedge, show_reset_note=False)
            reset_key = strike_entry_state_key(snapshot, side, position, strike, hedge) + "_compact_reset"
            if st.button("Reset / re-plan selected strike", key=reset_key):
                reset_strike_entry_state(snapshot, side, position, strike, hedge)
                st.rerun()
            e1, e2, e3 = st.columns(3)
            e1.metric("Planned entry used", f"₹{entry_premium:,.2f}")
            e2.metric("Price quality", f"{advisor.price_quality_score:.0f}/100")
            e3.metric("Move urgency", f"{advisor.move_urgency_score:.0f}/100")
            if advisor.ladder:
                st.dataframe(
                    [
                        {
                            "Entry": f"E{x.entry_no}",
                            "Premium": f"₹{x.premium:,.2f}",
                            "Lots": x.lots,
                            "Condition": x.condition,
                        }
                        for x in advisor.ladder
                    ],
                    hide_index=True,
                    width="stretch",
                )
                st.caption("E2/E3 automatic averaging nahi — setup invalid ho to remaining entries CANCEL.")
            if position == "SELL" and hedge is not None:
                n1, n2, n3 = st.columns(3)
                n1.metric(
                    "Current net credit",
                    "—" if advisor.current_net_credit is None else f"₹{advisor.current_net_credit:,.2f}",
                )
                n2.metric(
                    "Preferred net credit",
                    _premium_zone(advisor.preferred_net_credit_low, advisor.preferred_net_credit_high),
                )
                n3.metric(
                    "Minimum credit",
                    "—" if advisor.minimum_net_credit is None else f"≥ ₹{advisor.minimum_net_credit:,.2f}",
                )
            override = st.checkbox(
                "Projection ke liye planned entry premium manually override karo",
                value=False,
                key=f"spc2_plan_override_{side}_{position}_{int(strike)}",
            )
            if override:
                entry_premium = st.number_input(
                    "Planned entry premium",
                    min_value=0.05,
                    value=float(advisor.reference_entry),
                    step=0.05,
                    key=f"spc2_plan_entry_{side}_{position}_{int(strike)}",
                )
            st.caption(
                "Default projections latest preferred/acceptable entry logic use karte hain. "
                "W/M aur strong candle supportive only hain, mandatory nahi."
            )
    else:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Current premium", f"₹{chain_price:,.2f}")
        entry_premium = p2.number_input(
            "Actual fill premium",
            min_value=0.05,
            value=float(chain_price),
            step=0.05,
            key=f"spc2_entry_{side}_{int(strike)}",
        )
        entry_spot = p3.number_input(
            "Actual entry NIFTY",
            min_value=1.0,
            value=float(live_spot),
            step=1.0,
            key="spc2_entry_spot",
        )
        lots = p4.number_input("Lots", min_value=1, max_value=100, value=1, step=1, key="spc2_lots_actual")
        st.caption("Actual-fill mode: calculator tumhari real entry ko use karega; Smart Entry Advisor is mode me entry rewrite nahi karta.")

    h1, h2 = st.columns(2)
    holding = h1.selectbox(
        "Maximum holding",
        ["Aaj / Intraday", "1 din", "2 din", "3 din"],
        key="spc2_holding",
    )
    h2.metric("Expiry / Time", expiry_info.label)
    holding_limit = _holding_limit(snapshot, holding, expiry_info.minutes_remaining)

    manual_on = st.checkbox("Apna Upper/Lower target bhi check karo", key="spc2_manual_on")
    manual_lower = manual_upper = None
    if manual_on:
        m1, m2 = st.columns(2)
        manual_lower = m1.number_input(
            "Lower target",
            min_value=1.0,
            value=float(round((live_spot - 50.0) / 5.0) * 5.0),
            step=5.0,
            key="spc2_lower",
        )
        manual_upper = m2.number_input(
            "Upper target",
            min_value=1.0,
            value=float(round((live_spot + 50.0) / 5.0) * 5.0),
            step=5.0,
            key="spc2_upper",
        )

    advanced_on = st.checkbox("Advanced IV/Time details", key="spc2_advanced_on")
    iv_change = 0.0
    auto_iv_status = "OFF"
    auto_iv_window = None
    auto_iv_delta = None
    if advanced_on:
        # Auto IV delta is presentation/calculator-only. It reuses the compact
        # option snapshot already attached to MarketSnapshot and bounded same-day
        # persisted history. No broker/API call and no One-Brain recomputation.
        iv_payload = None
        if st.session_state.get("iv_delta_snapshot_id") == snapshot.snapshot_id:
            cached = st.session_state.get("iv_delta_payload")
            if isinstance(cached, dict):
                iv_payload = cached
        if iv_payload is None:
            current_state = (getattr(snapshot, "metadata", {}) or {}).get("option_state_snapshot") or {}
            if option_state_store is not None and snapshot.expiry and current_state:
                try:
                    history = option_state_store.load_session(
                        captured_at=snapshot.created_at, expiry=str(snapshot.expiry)
                    )
                    iv_payload = compute_iv_delta_payload(
                        current_snapshot=current_state, history=history
                    )
                except Exception as exc:
                    iv_payload = {
                        "status": "UNAVAILABLE",
                        "message": f"IV history read failed: {exc}",
                        "windows": [],
                        "preferred_window": None,
                        "preferred_strikes": {},
                    }
            else:
                iv_payload = {
                    "status": "UNAVAILABLE",
                    "message": "Saved IV history unavailable.",
                    "windows": [],
                    "preferred_window": None,
                    "preferred_strikes": {},
                }
            st.session_state.iv_delta_payload = iv_payload
            st.session_state.iv_delta_snapshot_id = snapshot.snapshot_id

        auto_iv_status = str(iv_payload.get("status") or "UNAVAILABLE")
        auto_iv_window = iv_payload.get("preferred_window")
        preferred = iv_payload.get("preferred_strikes") or {}
        side_map = preferred.get(side) or {} if isinstance(preferred, dict) else {}
        try:
            raw_delta = side_map.get(float(strike))
            if raw_delta is None:
                # JSON/cache variants may carry string strike keys.
                raw_delta = side_map.get(str(float(strike))) or side_map.get(str(int(float(strike))))
            if raw_delta is not None:
                auto_iv_delta = float(raw_delta)
        except (TypeError, ValueError, AttributeError):
            auto_iv_delta = None

        if auto_iv_delta is not None:
            iv_change = max(-20.0, min(20.0, float(auto_iv_delta)))
            st.info(
                f"⚡ Auto IV Δ: {iv_change:+.2f} pts ({auto_iv_window or 'saved history'}) · "
                "selected strike · no broker/API call"
            )
        else:
            st.caption(
                f"Auto IV Δ: {auto_iv_status} · saved history abhi enough nahi; "
                "IV effect 0 rakha gaya, koi value invent nahi ki gayi."
            )

        manual_iv_override = st.checkbox(
            "Manual IV scenario override",
            value=False,
            key="spc2_manual_iv_override",
            help="Normally OFF rakho. Auto IV Δ available ho to wahi calculator use karega.",
        )
        if manual_iv_override:
            iv_change = st.number_input(
                "IV change scenario (optional)",
                min_value=-20.0,
                max_value=20.0,
                value=float(round(iv_change, 2)),
                step=0.5,
                key="spc2_iv_change",
            )

    signature = (
        snapshot.snapshot_id,
        side,
        position,
        float(strike),
        float(entry_premium),
        float(entry_spot),
        int(lots),
        holding,
        bool(manual_on),
        float(manual_lower or 0),
        float(manual_upper or 0),
        float(iv_change),
    )
    calculate = st.button(
        "Calculate Premium at R1/R2/S1/S2",
        type="primary",
        width="stretch",
        disabled=projection_blocked,
    )
    if projection_blocked:
        st.caption("Detailed future-premium projection ke liye valid Greeks/IV quality required hai; koi value invent nahi ki gayi.")
    bundle = None
    if calculate:
        try:
            auto = []
            for label, barrier in _barrier_targets(snapshot):
                if barrier is None:
                    continue
                auto.append(
                    _target_bundle(
                        snapshot,
                        label=label,
                        target_spot=float(barrier.midpoint),
                        strength=float(barrier.strength),
                        break_pressure=float(barrier.break_pressure),
                        side=side,
                        position=position,
                        strike=float(strike),
                        current_spot=live_spot,
                        current_premium=chain_price,
                        entry_premium=float(entry_premium),
                        lot_size=lot_size,
                        lots=int(lots),
                        feed_state=feed_state,
                        iv_change_points=float(iv_change),
                        minutes_to_expiry=expiry_info.minutes_remaining,
                        holding_limit_minutes=holding_limit,
                    )
                )
            manual = []
            if manual_on:
                if manual_lower is None or manual_upper is None or manual_lower >= manual_upper:
                    raise ValueError("Lower target, Upper target se chhota hona chahiye")
                for label, target in (("Lower", manual_lower), ("Upper", manual_upper)):
                    manual.append(
                        _target_bundle(
                            snapshot,
                            label=label,
                            target_spot=float(target),
                            strength=50.0,
                            break_pressure=50.0,
                            side=side,
                            position=position,
                            strike=float(strike),
                            current_spot=live_spot,
                            current_premium=chain_price,
                            entry_premium=float(entry_premium),
                            lot_size=lot_size,
                            lots=int(lots),
                            feed_state=feed_state,
                            iv_change_points=float(iv_change),
                            minutes_to_expiry=expiry_info.minutes_remaining,
                            holding_limit_minutes=holding_limit,
                        )
                    )
            direction = directional_intent(side=side, position=position)
            stop_level = stop_reference(
                barrier_map=snapshot.barrier_map, direction=direction
            )
            buffer_points = stop_buffer_points(
                atr3=snapshot.price_action.three_minute.atr14,
                zone_width=snapshot.levels.zone_width,
                expiry=expiry_info,
            )
            stop_spot = stop_spot_price(
                level=stop_level,
                direction=direction,
                buffer_points=buffer_points,
            )
            stop_item = _target_bundle(
                snapshot,
                label="SL",
                target_spot=stop_spot,
                strength=float(stop_level.strength),
                break_pressure=float(stop_level.break_pressure),
                side=side,
                position=position,
                strike=float(strike),
                current_spot=live_spot,
                current_premium=chain_price,
                entry_premium=float(entry_premium),
                lot_size=lot_size,
                lots=int(lots),
                feed_state=feed_state,
                iv_change_points=float(iv_change),
                minutes_to_expiry=expiry_info.minutes_remaining,
                holding_limit_minutes=holding_limit,
            )
            favorable_labels = ("R1", "R2") if direction == "BULLISH" else ("S1", "S2")
            plan_targets = [
                (
                    item["level"],
                    float(item["target"]),
                    item["premium"],
                    int(item["eta_high"]),
                )
                for item in auto
                if item["level"] in favorable_labels
            ]
            plan = build_sl_target_plan(
                side=side,
                position=position,
                entry_premium=float(entry_premium),
                lot_size=lot_size,
                lots=int(lots),
                entry_spot=float(entry_spot),
                current_spot=live_spot,
                barrier_map=snapshot.barrier_map,
                atr3=snapshot.price_action.three_minute.atr14,
                zone_width=snapshot.levels.zone_width,
                expiry=expiry_info,
                stop_estimate=stop_item["premium"],
                target_estimates=plan_targets,
                holding_limit_minutes=holding_limit,
            )
            bundle = {"auto": auto, "manual": manual, "plan": plan}
            st.session_state.spc2_bundle = bundle
            st.session_state.spc2_signature = signature
        except Exception as exc:
            st.error(f"Calculator input check karo: {exc}")
    elif st.session_state.get("spc2_signature") == signature:
        bundle = st.session_state.get("spc2_bundle")

    if not bundle:
        return

    rows = []
    auto_by_label = {item["level"]: item for item in bundle["auto"]}
    for label in ("R2", "R1"):
        item = auto_by_label.get(label)
        if item:
            estimate = item["premium"]
            rows.append(
                {"Level": label, "NIFTY": f"{item['target']:,.0f}", "ETA • Chance": f"{item['eta']} • {item['chance']:.0f}%", "Premium": f"₹{estimate.best_price:,.2f}", "Total P&L": _money(estimate.total_pnl)}
            )
    rows.append({"Level": "NOW", "NIFTY": f"{live_spot:,.0f}", "ETA • Chance": "Abhi", "Premium": f"₹{chain_price:,.2f}", "Total P&L": _money((float(entry_premium) - chain_price if position == 'SELL' else chain_price - float(entry_premium)) * lot_size * int(lots))})
    for label in ("S1", "S2"):
        item = auto_by_label.get(label)
        if item:
            estimate = item["premium"]
            rows.append(
                {"Level": label, "NIFTY": f"{item['target']:,.0f}", "ETA • Chance": f"{item['eta']} • {item['chance']:.0f}%", "Premium": f"₹{estimate.best_price:,.2f}", "Total P&L": _money(estimate.total_pnl)}
            )
    st.dataframe(rows, width="stretch", hide_index=True)

    plan = bundle.get("plan")
    if plan is not None:
        st.write("**SL + Target Plan**")
        plan_rows = [
            {
                "Plan": f"SL ({plan.stop_level_label})",
                "NIFTY": f"{plan.stop_spot:,.2f}",
                "Premium": f"₹{plan.stop_premium:,.2f}",
                "P&L": _money(-plan.total_risk),
                "RR": "—",
                "Action": "3m close/hold par exit",
            }
        ]
        plan_rows.extend(
            {
                "Plan": item.label,
                "NIFTY": f"{item.spot:,.2f}",
                "Premium": f"₹{item.premium:,.2f}",
                "P&L": _money(item.total_pnl),
                "RR": f"{item.risk_reward:.2f}R" if item.risk_reward is not None else "—",
                "Action": item.action,
            }
            for item in plan.targets
        )
        plan_rows.append(
            {
                "Plan": "TIME EXIT",
                "NIFTY": f"{plan.time_exit_minutes} min",
                "Premium": "Live bid/ask",
                "P&L": "—",
                "RR": "—",
                "Action": "Move na aaye to exit/check",
            }
        )
        st.dataframe(plan_rows, width="stretch", hide_index=True)
        st.info(
            f"🧠 **Plan:** {plan.verdict} • SL buffer {plan.stop_buffer_points:.1f} pts • "
            f"{expiry_info.label}."
        )
        if plan.warnings:
            st.caption(" • ".join(plan.warnings))

    if bundle["manual"]:
        st.write("**Tumhare manual targets**")
        st.dataframe(
            [
                {
                    "Target": f"{item['level']} {item['target']:,.0f}",
                    "ETA • Chance": f"{item['eta']} • {item['chance']:.0f}%",
                    "Premium": f"₹{item['premium'].best_price:,.2f}",
                    "Total P&L": _money(item["premium"].total_pnl),
                }
                for item in bundle["manual"]
            ],
            width="stretch",
            hide_index=True,
        )

    favorable = max(bundle["auto"], key=lambda item: item["premium"].total_pnl)
    adverse = min(bundle["auto"], key=lambda item: item["premium"].total_pnl)
    st.info(
        f"🧠 **Samajh:** {favorable['level']} par premium ₹{favorable['premium'].best_price:,.2f} "
        f"aur P&L {_money(favorable['premium'].total_pnl)}; {adverse['level']} par premium "
        f"₹{adverse['premium'].best_price:,.2f} aur P&L {_money(adverse['premium'].total_pnl)}."
    )

    if advanced_on:
        detail = calculate_spot_premium_range(
            option_chain=frame,
            side=side,
            position=position,
            strike=float(strike),
            current_spot=live_spot,
            current_premium=chain_price,
            entry_premium=float(entry_premium),
            lower_spot=float(manual_lower if manual_on else live_spot - 50),
            upper_spot=float(manual_upper if manual_on else live_spot + 50),
            target_minutes=15,
            lot_size=lot_size,
            lots=int(lots),
            feed_state=feed_state,
            iv_change_points=float(iv_change),
            minutes_to_expiry=expiry_info.minutes_remaining,
        )
        st.caption(
            f"Delta {detail.current_delta if detail.current_delta is not None else '—'} • "
            f"Gamma {detail.current_gamma if detail.current_gamma is not None else '—'} • "
            f"Theta {detail.current_theta if detail.current_theta is not None else '—'} • "
            f"Vega {detail.current_vega if detail.current_vega is not None else '—'} • "
            f"IV {detail.current_iv if detail.current_iv is not None else '—'} • Estimate quality {detail.overall_reliability:.0f}/100 (win probability nahi) • {detail.status}"
        )
