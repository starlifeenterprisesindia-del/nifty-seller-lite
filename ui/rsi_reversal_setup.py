from __future__ import annotations

from html import escape

import streamlit as st

from analysis.rsi_reversal_setup import evaluate_rsi_reversal_setup
from models import MarketSnapshot


def _value(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def render_rsi_reversal_setup(
    snapshot: MarketSnapshot,
    previous_snapshot: MarketSnapshot | None,
) -> None:
    item = evaluate_rsi_reversal_setup(snapshot, previous_snapshot)
    tone = (
        "ready" if item.status == "ENTRY READY" else
        "reference" if item.status == "REFERENCE ONLY" else
        "wait"
    )
    html = (
        '<style>'
        '.rtr-card{border:1px solid rgba(127,127,127,.28);border-radius:15px;padding:13px;margin:4px 0 10px}'
        '.rtr-card.ready{border-color:rgba(34,197,94,.55);background:rgba(34,197,94,.10)}'
        '.rtr-card.wait{border-color:rgba(245,158,11,.48);background:rgba(245,158,11,.08)}'
        '.rtr-card.reference{border-color:rgba(59,130,246,.45);background:rgba(59,130,246,.08)}'
        '.rtr-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}'
        '.rtr-action{font-size:1.42rem;font-weight:950}.rtr-badge{font-size:.75rem;font-weight:850;padding:5px 8px;border-radius:99px;background:rgba(127,127,127,.14)}'
        '.rtr-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:11px}'
        '.rtr-cell{padding:8px;border-radius:10px;background:rgba(127,127,127,.08);min-width:0}'
        '.rtr-label{font-size:.68rem;font-weight:800;opacity:.68}.rtr-value{font-size:.86rem;font-weight:850;margin-top:3px;overflow-wrap:anywhere}'
        '@media(max-width:760px){.rtr-action{font-size:1.20rem}.rtr-grid{grid-template-columns:1fr}}'
        '</style>'
        f'<div class="rtr-card {tone}"><div class="rtr-head">'
        f'<div><div class="rtr-label">ALAG RSI REVERSAL STRATEGY</div><div class="rtr-action">{escape(item.action)}</div></div>'
        f'<div class="rtr-badge">{escape(item.status)} · {item.confidence}%</div></div>'
        '<div class="rtr-grid">'
        f'<div class="rtr-cell"><div class="rtr-label">ZONE + RSI</div><div class="rtr-value">{escape(item.zone)} · {_value(item.rsi_previous)} → {_value(item.rsi_now)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">BARRIER + OI</div><div class="rtr-value">{escape(item.barrier_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">BIG PLAYER</div><div class="rtr-value">{escape(item.big_player_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">STRIKE + HEDGE</div><div class="rtr-value">{escape(item.structure_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">MARKET SL</div><div class="rtr-value">{escape(item.market_sl_text)}</div></div>'
        f'<div class="rtr-cell"><div class="rtr-label">MONEY SL + QTY</div><div class="rtr-value">{escape(item.money_sl_text)} · Max {item.suggested_lots} lot</div></div>'
        '</div></div>'
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
