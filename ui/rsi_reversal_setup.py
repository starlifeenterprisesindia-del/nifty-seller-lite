from __future__ import annotations

from html import escape
from datetime import datetime

import streamlit as st

from analysis.rsi_reversal_setup import (
    evaluate_rsi_reversal_setup,
    create_rsi_trade_record,
    _plan,
)
from models import MarketSnapshot


def _value(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def render_rsi_reversal_setup(
    snapshot: MarketSnapshot,
    previous_snapshot: MarketSnapshot | None,
    record_trade=None,
) -> None:
    item = evaluate_rsi_reversal_setup(snapshot, previous_snapshot)
    tone = (
        "ready"
        if item.status == "ENTRY READY"
        else "reference"
        if item.status == "REFERENCE ONLY"
        else "wait"
    )
    html = (
        "<style>"
        ".rtr-card{border:1px solid rgba(127,127,127,.28);border-radius:15px;padding:13px;margin:4px 0 10px}"
        ".rtr-card.ready{border-color:rgba(34,197,94,.55);background:rgba(34,197,94,.10)}"
        ".rtr-card.wait{border-color:rgba(245,158,11,.48);background:rgba(245,158,11,.08)}"
        ".rtr-card.reference{border-color:rgba(59,130,246,.45);background:rgba(59,130,246,.08)}"
        ".rtr-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}"
        ".rtr-action{font-size:1.42rem;font-weight:950}.rtr-badge{font-size:.75rem;font-weight:850;padding:5px 8px;border-radius:99px;background:rgba(127,127,127,.14)}"
        ".rtr-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:11px}"
        ".rtr-cell{padding:8px;border-radius:10px;background:rgba(127,127,127,.08);min-width:0}"
        ".rtr-label{font-size:.68rem;font-weight:800;opacity:.68}.rtr-value{font-size:.86rem;font-weight:850;margin-top:3px;overflow-wrap:anywhere}"
        "@media(max-width:760px){.rtr-action{font-size:1.20rem}.rtr-grid{grid-template-columns:1fr}}"
        "</style>"
        f'<div class="rtr-card {tone}"><div class="rtr-head">'
        f'<div><div class="rtr-label">ALAG RSI REVERSAL STRATEGY</div><div class="rtr-action">{escape(item.action)}</div></div>'
        f'<div class="rtr-badge">{escape(item.status)} · Setup score {item.confidence}/100</div></div>'
        '<div class="rtr-grid">'
        f'<div class="rtr-cell"><div class="rtr-label">ZONE + RSI</div><div class="rtr-value">{escape(item.zone)} · {_value(item.rsi_previous)} → {_value(item.rsi_now)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">BARRIER + OI</div><div class="rtr-value">{escape(item.barrier_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">BIG PLAYER</div><div class="rtr-value">{escape(item.big_player_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">STRIKE + HEDGE</div><div class="rtr-value">{escape(item.structure_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">MARKET SL</div><div class="rtr-value">{escape(item.market_sl_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">MONEY SL + QTY</div><div class="rtr-value">{escape(item.money_sl_text)} · Max {item.suggested_lots} lot</div></div>'
        "</div></div>"
    )
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)

    if item.reasons:
        st.success(" • ".join(item.reasons))
    if item.cautions:
        st.warning(" • ".join(item.cautions))
    st.caption(
        "Ye alag advisory strategy hai. Main AI/One-Brain ke score ya final decision ko change nahi karti. "
        "App order khud place nahi karti."
    )
    record = getattr(snapshot.discipline_state, "trade_record", None)
    if record and record.get("strategy") == "RSI TOP BOTTOM":
        from ui.components import render_position_guardian

        render_position_guardian(snapshot)
    elif item.status == "ENTRY READY" and record_trade is not None:
        st.warning(
            "Monitoring ke liye actual broker fills record karo. Ye button order place nahi karta. SL alert automatic exit nahi hai; charges/slippage alag hain."
        )
        plan = _plan(snapshot, item.action)
        with st.form("rsi_actual_trade_record"):
            lots = st.number_input(
                "Actual lots",
                min_value=1,
                max_value=item.suggested_lots,
                value=1,
                step=1,
            )
            fills = []
            for index, leg in enumerate((*plan.short_legs, *plan.hedge_legs)):
                fills.append(
                    st.number_input(
                        f"Actual fill: {leg.role} {leg.strike:g} {leg.side}",
                        min_value=0.0,
                        value=0.0,
                        key=f"rsi_fill_{plan.name}_{leg.strike}_{leg.side}_{index}",
                    )
                )
            confirm = st.checkbox("Maine ye exact hedged trade broker par li hai")
            submitted = st.form_submit_button("Record actual trade / start loss alerts")
        if submitted:
            try:
                if not confirm:
                    raise ValueError("Confirm actual broker fills first")
                if (
                    datetime.now(snapshot.created_at.tzinfo) - snapshot.created_at
                ).total_seconds() > 60:
                    raise ValueError(
                        "Snapshot old hai; refresh karke dobara verify karo"
                    )
                record = create_rsi_trade_record(snapshot, int(lots), fills)
                record_trade(
                    session_date=snapshot.created_at,
                    action=item.action,
                    trade_record=record,
                )
                st.success(
                    "Trade recorded. Agle data refresh se Position Guardian loss/barrier alerts dikhayega."
                )
            except ValueError as exc:
                st.error(str(exc))
