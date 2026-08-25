from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from config import CONFIG


def render_auto_shadow_journal(entries: list[dict[str, Any]], session_date: str) -> None:
    st.subheader("🧪 Auto Shadow Journal — Paper Trades Only")
    st.caption(
        "One-Brain ke confirmed ENTRY READY setups automatically paper-record hote hain. "
        "Koi broker order ya real paisa use nahi hota."
    )
    today = [item for item in entries if str(item.get("session_date")) == session_date]
    closed = [item for item in today if str(item.get("status")).upper() == "CLOSED"]
    open_items = [item for item in today if str(item.get("status")).upper() == "OPEN"]
    net = sum(float(item.get("net_pnl_rupees") or 0.0) for item in closed)
    wins = sum(float(item.get("net_pnl_rupees") or 0.0) > 0 for item in closed)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Paper Trades", f"{len(today)}/{CONFIG.shadow_journal_max_trades_per_day}")
    c2.metric("Open", len(open_items))
    c3.metric("Closed", len(closed))
    c4.metric("Wins", wins)
    c5.metric("Est. Net P&L", f"₹{net:,.0f}")

    if not today:
        st.info("Aaj abhi koi 75%+ confirmed ENTRY READY shadow trade record nahi hua.")
        return

    rows = []
    for item in reversed(today):
        opened = str(item.get("opened_at") or "")
        rows.append(
            {
                "Time": opened[11:19] if len(opened) >= 19 else opened,
                "Strategy": item.get("setup"),
                "Confidence": item.get("decision_confidence"),
                "Strategy score": item.get("strategy_score"),
                "OI bias": item.get("oi_bias"),
                "Big Player": f"{item.get('big_player_direction')} {float(item.get('big_player_score') or 0):.0f}",
                "Status": item.get("status"),
                "Outcome": item.get("outcome") or "MONITORING",
                "MFE ₹": item.get("mfe_rupees"),
                "MAE ₹": item.get("mae_rupees"),
                "Net P&L ₹": item.get("net_pnl_rupees"),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    labels = [
        f"{str(item.get('opened_at') or '')[11:19]} · {item.get('setup')} · {item.get('trade_id')}"
        for item in reversed(today)
    ]
    selected = st.selectbox("Trade ka complete reason", labels, key="shadow_trade_detail")
    selected_item = list(reversed(today))[labels.index(selected)]
    st.write("**Kyun liya:**")
    for reason in selected_item.get("entry_reasons") or ("Reason unavailable",):
        st.write(f"• {reason}")
    if selected_item.get("legs"):
        st.dataframe(pd.DataFrame(selected_item["legs"]), width="stretch", hide_index=True)

    csv = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Shadow Journal CSV",
        data=csv,
        file_name=f"auto_shadow_journal_{session_date}.csv",
        mime="text/csv",
        width="stretch",
    )
