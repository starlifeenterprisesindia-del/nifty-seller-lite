from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from config import CONFIG


def _decision_rows(store=None) -> list[dict[str, Any]]:
    """Merge local decisions with Railway-persistent app decisions.

    Railway is authoritative for live-session persistence.  Local rows are still
    useful for the current Streamlit process and for reference-only snapshots.
    """

    local = store.load_decisions() if store is not None else []
    report = st.session_state.get("day_memory_report") or {}
    remote = report.get("app_decisions") or []
    merged: dict[str, dict[str, Any]] = {}
    for row in [*local, *remote]:
        if not isinstance(row, dict):
            continue
        key = str(row.get("at") or "")
        if not key:
            continue
        merged[key] = dict(row)
    return sorted(merged.values(), key=lambda row: str(row.get("at") or ""))


def render_shadow_journal_status(entries: list[dict[str, Any]], store=None) -> None:
    decisions = _decision_rows(store)
    report = st.session_state.get("day_memory_report") or {}
    today = (
        str(getattr(store, "last_checked", ""))[:10]
        or str(report.get("day") or "")[:10]
        or (str(decisions[-1].get("session_date") or "") if decisions else "")
    )
    current = [item for item in entries if str(item.get("session_date")) == today]
    open_items = [item for item in current if str(item.get("status")).upper() == "OPEN"]
    with st.container(border=True):
        st.markdown("**🧪 Auto Shadow Journal**")
        try:
            import json
            checks = json.loads(store.path.with_suffix(".signals.json").read_text()) if store is not None else []
        except (OSError, ValueError):
            checks = []
        rejected = [row for row in checks if str(row.get("at", ""))[:10] == today and row.get("reason") != "READY"]
        today_decisions = [
            row for row in decisions
            if str(row.get("session_date")) == today and bool(row.get("session_live", True))
        ]
        cols = st.columns(5)
        cols[0].metric("Live decisions", len(today_decisions))
        cols[1].metric("Paper trades", len(current))
        cols[2].metric("Open paper", len(open_items))
        cols[3].metric("Direction floor", f"{CONFIG.simple_direction_min_strength:.0f}%")
        cols[4].metric("Entry ready", f"{CONFIG.simple_entry_ready_score:.0f}%")
        coverage = ((report.get("recording_coverage") or {}) if isinstance(report, dict) else {})
        if coverage.get("app_session_status"):
            st.caption(
                f"Railway journal: {coverage.get('app_session_status')} · "
                f"persistent rows {coverage.get('app_decision_rows', 0)}"
            )
        if store is not None:
            st.caption(f"Last check: {store.last_checked or '—'} · Exact blocker: {store.last_blocker or '—'}")


def render_auto_shadow_journal(entries: list[dict[str, Any]], session_date: str, store=None) -> None:
    st.subheader("🧪 Auto Journal — Decisions + Paper Trades")
    st.caption(
        "v2.48 Simple One-Brain har meaningful WAIT/READY/ENTRY decision record karta hai. "
        "Paper P&L sirf actual gate-passed protected setups ka hota hai; decision journal "
        "5m/15m/30m baad missed-move outcome bhi backfill karta hai. Koi broker order nahi."
    )
    if store is not None:
        st.caption(f"Last checked: {store.last_checked} · Candidate status: {store.last_blocker} · Last saved: {store.last_saved or 'No save yet'}")
        if store.last_error:
            st.warning(store.last_error)
        else:
            st.caption("Local journal available; cloud backup only if configured. Not broker trades.")
        import json
        try:
            history = json.loads(store.path.with_suffix(".signals.json").read_text())
        except (OSError, ValueError):
            history = []
        with st.expander("Signal / WAIT reasons history"):
            st.dataframe(history[-100:], width="stretch", hide_index=True)
        decisions = _decision_rows(store)
        with st.expander("Decision Journal — WAIT bhi record hota hai", expanded=True):
            today_decisions = [row for row in decisions if str(row.get("session_date")) == session_date]
            if today_decisions:
                st.dataframe(pd.DataFrame(today_decisions[-120:]), width="stretch", hide_index=True)
            else:
                st.info(
                    "Is date par live app-decision record nahi mila. Agar app/token market hours me active "
                    "nahi tha to yeh expected hai; missing session ko trading bug nahi maana jayega."
                )
    dates = sorted({session_date, *(str(x.get("session_date")) for x in entries)}, reverse=True)
    selected_date = st.selectbox("Journal date", dates, key="shadow_history_date")
    today = [item for item in entries if str(item.get("session_date")) == selected_date]
    qualified = [item for item in today if bool(item.get("counts_for_ai_accuracy"))]
    experimental = [item for item in today if not bool(item.get("counts_for_ai_accuracy"))]
    closed = [item for item in qualified if str(item.get("status")).upper() == "CLOSED"]
    open_items = [item for item in today if str(item.get("status")).upper() == "OPEN"]
    net = sum(float(item.get("net_pnl_rupees") or 0.0) for item in closed)
    wins = sum(float(item.get("net_pnl_rupees") or 0.0) > 0 for item in closed)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Qualified / Experimental", f"{len(qualified)} / {len(experimental)}")
    c2.metric("Open", len(open_items))
    c3.metric("Closed", len(closed))
    c4.metric("Wins", wins)
    c5.metric("Qualified Net P&L", f"₹{net:,.0f}")

    all_closed = [item for item in entries if str(item.get("status")).upper() == "CLOSED"]
    if len(all_closed) >= 20:
        wins_all = sum(float(item.get("net_pnl_rupees") or 0.0) > 0 for item in all_closed)
        avg_net = sum(float(item.get("net_pnl_rupees") or 0.0) for item in all_closed) / len(all_closed)
        st.info(
            f"Recorded calibration ({len(all_closed)} closed paper samples): "
            f"win rate {wins_all / len(all_closed) * 100:.1f}% · average net ₹{avg_net:,.0f}. "
            "Yeh historical paper result hai, future profit guarantee nahi."
        )
    else:
        st.caption(f"Historical success rate: insufficient samples ({len(all_closed)}/20 closed paper trades).")

    if not today:
        st.info(
            "Aaj abhi koi Simple One-Brain gate-passed paper trade record nahi hua. "
            "WAIT/READY decisions upar Decision Journal me phir bhi record hote hain."
        )
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
                "Score band": item.get("score_band", "LEGACY"),
                "Qualification": item.get("qualification") or "LEGACY",
                "Exact blocker/warning": item.get("candidate_warning") or "—",
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

    # Downloads live in the single Checks & Downloads Centre.


def render_shadow_journal_download(entries: list[dict[str, Any]], session_date: str, store=None) -> None:
    """Download one useful journal even when no paper trade was approved."""
    decisions = _decision_rows(store)
    decision_rows = [row for row in decisions if str(row.get("session_date")) == session_date]
    if decision_rows:
        csv = pd.DataFrame(decision_rows).to_csv(index=False).encode("utf-8")
        label = "Download Decision Journal CSV"
        filename = f"nifty_decision_journal_{session_date}.csv"
    else:
        rows = [item for item in entries if str(item.get("session_date")) == session_date]
        csv = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
        label = "Download Shadow Journal CSV"
        filename = f"auto_shadow_journal_{session_date}.csv"
    st.download_button(
        label,
        data=csv,
        file_name=filename,
        mime="text/csv",
        width="stretch",
    )

