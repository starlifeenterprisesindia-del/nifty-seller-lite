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
from models import MarketSnapshot


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}₹{value:,.0f}"


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


def render_spot_premium_calculator(snapshot: MarketSnapshot) -> None:
    with st.expander("🧮 Spot-to-Premium Calculator", expanded=False):
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
        chain_state = snapshot.feed_status.get("option_chain")
        feed_state = str(getattr(chain_state, "use_state", "UNAVAILABLE") or "UNAVAILABLE").upper()

        expiry_info = expiry_context(captured_at=snapshot.created_at, expiry=snapshot.expiry)
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Current premium", f"₹{chain_price:,.2f}")
        entry_premium = p2.number_input(
            "Entry premium",
            min_value=0.05,
            value=float(chain_price),
            step=0.05,
            key=f"spc2_entry_{side}_{int(strike)}",
        )
        entry_spot = p3.number_input(
            "Entry NIFTY",
            min_value=1.0,
            value=float(live_spot),
            step=1.0,
            key="spc2_entry_spot",
        )
        lots = p4.number_input("Lots", min_value=1, max_value=100, value=1, step=1, key="spc2_lots")
        lot_size = int(snapshot.risk_profile.lot_size)

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
        if advanced_on:
            iv_change = st.number_input(
                "IV change scenario (optional)",
                min_value=-20.0,
                max_value=20.0,
                value=0.0,
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
        calculate = st.button("Calculate Premium at R1/R2/S1/S2", type="primary", width="stretch")
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
                f"IV {detail.current_iv if detail.current_iv is not None else '—'} • Bharosa {detail.overall_reliability:.0f}/100"
            )
