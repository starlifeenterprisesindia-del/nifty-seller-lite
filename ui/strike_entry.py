import streamlit as st
from analysis.strike_entry import plan_strike_entry


def strike_entry_state_key(snapshot, side, position, strike, hedge):
    return f"ind_zone_{snapshot.created_at.date()}_{snapshot.expiry}_{side}_{position}_{strike}_{hedge}"


def reset_strike_entry_state(snapshot, side, position, strike, hedge):
    key = strike_entry_state_key(snapshot, side, position, strike, hedge)
    st.session_state.pop(key, None)
    st.session_state.pop(key + "_cancel", None)
    st.session_state.pop(key + "_ready", None)


def prepare_strike_entry(snapshot, side, position, strike, lots, *, compact: bool = False):
    """Build the independent planner once and return ``(result, hedge)``.

    ``compact=True`` is used by the Premium Calculator so the Smart Entry Advisor can
    present one clear decision card while the full planner detail stays in an expander.
    The underlying planner/risk logic is unchanged.
    """
    if int(lots) < 1 or int(lots) > snapshot.risk_profile.max_lots_cap:
        return None, None

    hedge = None
    if position == "SELL":
        rows = snapshot.option_chain
        strikes = sorted(
            float(x) for x in rows.loc[rows.side.eq(side), "strike"].unique()
            if (float(x) - strike) * (1 if side == "CE" else -1) > 0
        )
        if not strikes:
            return None, None
        if side == "PE":
            strikes.reverse()
        hedge = st.selectbox(
            "Protective hedge",
            strikes,
            key=f"ind_hedge_{side}_{strike}",
            help="Same-expiry defined-risk hedge. Smart Entry Advisor net-credit quality bhi isi pair par check karega.",
        )

    key = strike_entry_state_key(snapshot, side, position, strike, hedge)
    if not compact and st.button("Reset / re-plan selected strike", key=key + "_reset"):
        reset_strike_entry_state(snapshot, side, position, strike, hedge)

    live = snapshot.market_session.is_live and all(
        getattr(snapshot.feed_status.get(name), "use_state", "") == "LIVE"
        for name in ("quotes", "candles", "option_chain")
    )
    result = plan_strike_entry(
        candles=snapshot.candles_3m,
        barrier_map=snapshot.barrier_map,
        option_chain=snapshot.option_chain,
        side=side,
        position=position,
        strike=strike,
        spot=float(snapshot.nifty_quote["last_price"]),
        as_of=snapshot.created_at,
        expiry=snapshot.expiry,
        live=live,
        risk_budget=min(5000, snapshot.risk_profile.risk_budget_rupees),
        lot_size=snapshot.risk_profile.lot_size,
        lots=int(lots),
        hedge_strike=hedge,
        frozen_zone=st.session_state.get(key),
    )
    if result.zone and live:
        st.session_state[key] = result.zone
    if result.status == "CANCEL":
        st.session_state[key + "_cancel"] = True
    if st.session_state.get(key + "_cancel"):
        # Preserve the frozen invalidation until the user explicitly resets/replans.
        result = type(result)(
            "CANCEL",
            "Frozen barrier failed; reset/re-plan explicitly before evaluating a new setup",
            result.zone,
            result.invalidation,
            result.premium_range,
            result.net_credit,
            result.worst_case_rupees,
            result.valid_until,
        )
    status = result.status
    if status == "NO CHASE" and st.session_state.get(key + "_ready"):
        result = type(result)(
            "MISSED / NO CHASE",
            result.reason,
            result.zone,
            result.invalidation,
            result.premium_range,
            result.net_credit,
            result.worst_case_rupees,
            result.valid_until,
        )
    if result.status.startswith("ENTRY NOW"):
        st.session_state[key + "_ready"] = True
    return result, hedge


def render_strike_entry_result(result, hedge=None, *, show_reset_note: bool = True):
    if result is None:
        st.warning("Planner data unavailable — quantity/hedge check karo")
        return
    status = result.status
    if status in {"CANCEL", "MISSED / NO CHASE", "NO CHASE"}:
        st.warning(f"{status} · {result.reason}")
    else:
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
    if show_reset_note:
        st.caption("Frozen barrier fail hone par planner CANCEL rahega jab tak selected strike explicitly re-plan/reset na ho.")


def render_strike_entry(snapshot, side, position, strike, lots):
    st.markdown("**Independent Strike Entry Planner**")
    st.caption("3m candle + barrier + quotes only. Main AI does not approve/override this planner. Advisory, no orders.")
    result, hedge = prepare_strike_entry(snapshot, side, position, strike, lots, compact=False)
    render_strike_entry_result(result, hedge)
    return result, hedge
