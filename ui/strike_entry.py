import streamlit as st
from analysis.strike_entry import plan_strike_entry


def render_strike_entry(snapshot, side, position, strike, lots):
    st.markdown("**Independent Strike Entry Planner**")
    st.caption("3m candle + barrier + quotes only. Main AI does not approve/override this planner. Advisory, no orders.")
    if int(lots) < 1 or int(lots) > snapshot.risk_profile.max_lots_cap:
        st.warning("WAIT — lots exceed your configured limit")
        return
    hedge = None
    if position == "SELL":
        rows = snapshot.option_chain
        strikes = sorted(float(x) for x in rows.loc[rows.side.eq(side), "strike"].unique()
                         if (float(x) - strike) * (1 if side == "CE" else -1) > 0)
        if not strikes:
            st.warning("Protective hedge unavailable — WAIT")
            return
        if side == "PE":
            strikes.reverse()
        hedge = st.selectbox("Planner hedge strike", strikes, key=f"ind_hedge_{side}_{strike}")
    key = f"ind_zone_{snapshot.created_at.date()}_{snapshot.expiry}_{side}_{position}_{strike}_{hedge}"
    if st.button("Reset / re-plan selected strike", key=key + "_reset"):
        st.session_state.pop(key, None)
        st.session_state.pop(key + "_cancel", None)
        st.session_state.pop(key + "_ready", None)
    live = snapshot.market_session.is_live and all(
        getattr(snapshot.feed_status.get(name), "use_state", "") == "LIVE"
        for name in ("quotes", "candles", "option_chain"))
    result = plan_strike_entry(candles=snapshot.candles_3m, barrier_map=snapshot.barrier_map,
        option_chain=snapshot.option_chain, side=side, position=position, strike=strike,
        spot=float(snapshot.nifty_quote["last_price"]), as_of=snapshot.created_at, expiry=snapshot.expiry,
        live=live, risk_budget=min(5000, snapshot.risk_profile.risk_budget_rupees),
        lot_size=snapshot.risk_profile.lot_size, lots=int(lots), hedge_strike=hedge,
        frozen_zone=st.session_state.get(key))
    if result.zone and live:
        st.session_state[key] = result.zone
    if result.status == "CANCEL":
        st.session_state[key + "_cancel"] = True
    if st.session_state.get(key + "_cancel"):
        st.warning("CANCEL — frozen barrier failed; reset to explicitly evaluate a new setup")
        return
    status = result.status
    if status == "NO CHASE" and st.session_state.get(key + "_ready"):
        status = "MISSED / NO CHASE"
    if status.startswith("ENTRY NOW"):
        st.session_state[key + "_ready"] = True
    st.info(f"{status} · {result.reason}")
    if result.zone:
        st.write(f"Nifty zone {result.zone[0]:,.2f}–{result.zone[1]:,.2f} · Invalid at {result.invalidation:,.2f}")
    if result.premium_range:
        st.write(f"Indicative premium range ₹{result.premium_range[0]:.2f}–₹{result.premium_range[1]:.2f}")
    else:
        st.caption("Premium estimate unavailable; no invented target price")
    if result.net_credit is not None:
        st.write(f"Current quoted net credit ₹{result.net_credit:.2f} per unit · Hedge {hedge:,.0f}")
    if result.worst_case_rupees is not None:
        st.caption(f"Defined-risk check including 10% reserve: ₹{result.worst_case_rupees:,.0f}. Not a broker SL.")
    if result.valid_until:
        st.caption(f"Quote validity until {result.valid_until}; refresh and recheck before any order. Retest estimates assume 3 minutes and unchanged IV.")
