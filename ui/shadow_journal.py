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
    if store is not None and remote:
        try:
            # Fresh Streamlit/Railway deploys start with an empty local file. Restore
            # the persistent Railway rows instead of briefly showing a false zero.
            store.merge_decisions([row for row in remote if isinstance(row, dict)])
        except Exception:
            pass
    merged: dict[str, dict[str, Any]] = {}
    for row in [*local, *remote]:
        if not isinstance(row, dict):
            continue
        key = str(row.get("at") or "")
        if not key:
            continue
        previous = merged.get(key, {})
        combined = dict(previous)
        # Remote persistence is authoritative, but an older remote row may not
        # yet contain outcome fields that the local process already backfilled.
        # Never replace a useful value with None/blank during the merge.
        for field, value in row.items():
            if value is not None and value != "":
                combined[field] = value
            elif field not in combined:
                combined[field] = value
        merged[key] = combined
    return sorted(merged.values(), key=lambda row: str(row.get("at") or ""))


def _lane(item: dict[str, Any]) -> str:
    return str(item.get("validation_lane") or "ONE BRAIN").upper()


def _lane_stats(rows: list[dict[str, Any]], lane: str) -> dict[str, Any]:
    lane_rows = [x for x in rows if _lane(x) == lane]
    closed = [x for x in lane_rows if str(x.get("status") or "").upper() == "CLOSED"]
    opened = [x for x in lane_rows if str(x.get("status") or "").upper() == "OPEN"]
    wins = sum(float(x.get("net_pnl_rupees") or 0.0) > 0 for x in closed)
    losses = sum(float(x.get("net_pnl_rupees") or 0.0) < 0 for x in closed)
    net = sum(float(x.get("net_pnl_rupees") or 0.0) for x in closed)
    return {
        "total": len(lane_rows), "closed": len(closed), "open": len(opened),
        "wins": wins, "losses": losses, "net": net,
    }


def render_shadow_journal_status(entries: list[dict[str, Any]], store=None, snapshot=None) -> None:
    decisions = _decision_rows(store)
    report = st.session_state.get("day_memory_report") or {}
    coverage = ((report.get("recording_coverage") or {}) if isinstance(report, dict) else {})
    market_live = bool(getattr(getattr(snapshot, "market_session", None), "is_live", False))
    journal_error = str(st.session_state.get("day_memory_error") or "")
    sample_age = report.get("last_sample_age_seconds") if isinstance(report, dict) else None
    persistent_rows = int(coverage.get("app_decision_rows") or 0)
    snapshot_day = str(getattr(getattr(snapshot, "created_at", None), "date", lambda: "")()) if snapshot is not None else ""
    local_today_count = sum(1 for row in decisions if str(row.get("session_date") or "") == snapshot_day)
    if journal_error and not report and not local_today_count:
        feed_label, feed_kind = "WARMING / RETRY", "warning"
    elif not market_live:
        feed_label, feed_kind = "REFERENCE — SESSION CLOSED", "info"
    elif sample_age is not None and float(sample_age) <= 180:
        feed_label, feed_kind = "LIVE / RECENT", "success"
    elif local_today_count and not report:
        feed_label, feed_kind = "LOCAL RECORDING · RAILWAY WARMING", "success"
    elif report:
        feed_label, feed_kind = "STALE / GAP", "warning"
    else:
        feed_label, feed_kind = "WARMING UP", "info"

    today = (
        snapshot_day[:10]
        or str(getattr(store, "last_checked", ""))[:10]
        or str(report.get("day") or "")[:10]
        or (str(decisions[-1].get("session_date") or "") if decisions else "")
    )
    current = [item for item in entries if str(item.get("session_date")) == today]
    ob = _lane_stats(current, "ONE BRAIN")
    mi = _lane_stats(current, "MARKET INTELLIGENCE")
    open_items = [item for item in current if str(item.get("status")).upper() == "OPEN"]

    with st.container(border=True):
        st.markdown("**🧪 AI Validation Journal — Decisions + Paper Trades**")
        health_text = (
            f"Journal Feed: **{feed_label}** · local decisions {len([row for row in decisions if str(row.get('session_date')) == today])} · "
            f"Railway decisions {persistent_rows} · last sample age {'—' if sample_age is None else f'{float(sample_age):.0f}s'}"
        )
        if feed_kind == "success":
            st.success(health_text)
        elif feed_kind == "warning":
            st.warning(health_text)
        else:
            st.info(health_text)
        today_decisions = [
            row for row in decisions
            if str(row.get("session_date")) == today and bool(row.get("session_live", True))
        ]
        cols = st.columns(5)
        diagnostics = (getattr(snapshot, "metadata", {}) or {}).get("recording_diagnostics") or {}
        pending = bool((diagnostics.get("async_refresh") or {}).get("pending"))
        decision_display = "SYNCING" if pending and not today_decisions and not report else len(today_decisions)
        cols[0].metric("Live decisions", decision_display)
        cols[1].metric("OB paper", ob["total"])
        cols[2].metric("MI paper", mi["total"])
        cols[3].metric("Open paper", len(open_items))
        cols[4].metric("OB floors", f"{CONFIG.simple_direction_min_strength:.0f}/{CONFIG.simple_entry_ready_score:.0f}")
        st.caption(
            f"Paper caps: One Brain {CONFIG.shadow_journal_max_ob_trades_per_day}/day · "
            f"Market Intelligence {CONFIG.shadow_journal_max_mi_trades_per_day}/day · "
            f"research cooldown {CONFIG.shadow_journal_research_cooldown_minutes}m. "
            "MI uses existing snapshots/protected plans; no broker order is placed."
        )
        if coverage.get("app_session_status"):
            st.caption(
                f"Railway journal: {coverage.get('app_session_status')} · "
                f"persistent rows {coverage.get('app_decision_rows', 0)}"
            )
        if store is not None:
            st.caption(f"Last check: {store.last_checked or '—'} · Exact blocker: {store.last_blocker or '—'}")


def render_auto_shadow_journal(entries: list[dict[str, Any]], session_date: str, store=None) -> None:
    st.subheader("🧪 AI Validation Journal — One Brain + Market Intelligence")
    st.caption(
        "Decision Journal 09:15–15:00 WAIT/READY/ENTRY evidence record karta hai. Executable paper trades configured "
        "entry window me separate One Brain aur Market Intelligence lanes me bante hain. MI Window OPEN/STRONG 6/6 "
        "ya verified/realized pressure + attack support trigger ho sakta hai; Liquidity Concentration akeli trade nahi banati."
    )
    if store is not None:
        decisions = _decision_rows(store)
        report = st.session_state.get("day_memory_report") or {}
        coverage = (report.get("recording_coverage") or {}) if isinstance(report, dict) else {}
        today_decisions = [row for row in decisions if str(row.get("session_date")) == session_date]
        latest_at = str(today_decisions[-1].get("at") or "") if today_decisions else ""
        latest_time = latest_at[11:19] if len(latest_at) >= 19 else (latest_at or "—")
        persistent_rows = int(coverage.get("app_decision_rows") or 0)
        st.caption(
            f"Decision Journal: {'RECORDED' if today_decisions else 'NO ROWS'} · today {len(today_decisions)} · "
            f"Railway persistent {persistent_rows} · latest {latest_time}"
        )
        if store.last_error:
            st.warning(store.last_error)
        else:
            st.caption("Decision rows, One Brain paper trades and MI paper trades are validation records only; none places broker orders.")
        with st.expander("Decision Journal — WAIT + Intelligence context", expanded=False):
            if today_decisions:
                st.dataframe(pd.DataFrame(today_decisions[-180:]), width="stretch", hide_index=True)
            else:
                st.info(
                    "Is date par Decision Journal row nahi mili. Live market me 09:15–15:00 ke beech fresh snapshots "
                    "aane chahiye; Diagnostics me exact blocker check karo."
                )

    dates = sorted({session_date, *(str(x.get("session_date")) for x in entries if x.get("session_date"))}, reverse=True)
    selected_date = st.selectbox("Journal date", dates, key="shadow_history_date")
    today = [item for item in entries if str(item.get("session_date")) == selected_date]
    ob = _lane_stats(today, "ONE BRAIN")
    mi = _lane_stats(today, "MARKET INTELLIGENCE")
    aligned = [x for x in today if str(x.get("alignment_state") or "").upper() == "ALIGNED"]
    conflict = [x for x in today if str(x.get("alignment_state") or "").upper() == "CONFLICT"]
    aligned_closed = [x for x in aligned if str(x.get("status") or "").upper() == "CLOSED"]
    aligned_net = sum(float(x.get("net_pnl_rupees") or 0.0) for x in aligned_closed)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("One Brain", f"{ob['total']} ({ob['wins']}W/{ob['losses']}L)")
    c2.metric("OB est. Net", f"₹{ob['net']:,.0f}")
    c3.metric("Market Intelligence", f"{mi['total']} ({mi['wins']}W/{mi['losses']}L)")
    c4.metric("MI est. Net", f"₹{mi['net']:,.0f}")
    c5.metric("Aligned / Conflict", f"{len(aligned)} / {len(conflict)}")
    if aligned_closed:
        st.caption(f"Aligned closed samples: {len(aligned_closed)} · estimated net ₹{aligned_net:,.0f}.")

    with st.expander("Research trigger breakdown", expanded=False):
        breakdown = []
        for trigger in ("ONE BRAIN", "INSTITUTIONAL WINDOW", "PRESSURE INTEGRITY"):
            subset = [x for x in today if str(x.get("trigger_type") or "").upper() == trigger]
            closed_subset = [x for x in subset if str(x.get("status") or "").upper() == "CLOSED"]
            wins_subset = sum(float(x.get("net_pnl_rupees") or 0.0) > 0 for x in closed_subset)
            losses_subset = sum(float(x.get("net_pnl_rupees") or 0.0) < 0 for x in closed_subset)
            net_subset = sum(float(x.get("net_pnl_rupees") or 0.0) for x in closed_subset)
            breakdown.append({
                "Trigger": trigger, "Samples": len(subset), "Closed": len(closed_subset),
                "Wins": wins_subset, "Losses": losses_subset, "Est. Net ₹": round(net_subset, 2),
            })
        st.dataframe(pd.DataFrame(breakdown), width="stretch", hide_index=True)

    # Never mix lanes when showing historical performance.
    for lane, label in (("ONE BRAIN", "One Brain"), ("MARKET INTELLIGENCE", "Market Intelligence")):
        lane_closed = [
            x for x in entries
            if _lane(x) == lane and str(x.get("status") or "").upper() == "CLOSED"
        ]
        if len(lane_closed) >= 20:
            wins = sum(float(x.get("net_pnl_rupees") or 0.0) > 0 for x in lane_closed)
            avg_net = sum(float(x.get("net_pnl_rupees") or 0.0) for x in lane_closed) / len(lane_closed)
            st.info(
                f"{label} calibration ({len(lane_closed)} closed paper samples): win rate "
                f"{wins / len(lane_closed) * 100:.1f}% · average estimated net ₹{avg_net:,.0f}. "
                "Overlapping samples correlated ho sakte hain; future result guarantee nahi."
            )
        else:
            st.caption(f"{label} calibration: insufficient samples ({len(lane_closed)}/20 closed).")

    if not today:
        st.info("Is date par abhi koi executable paper-validation trade record nahi hua; Decision Journal phir bhi upar record ho sakta hai.")
        return

    rows = []
    for item in reversed(today):
        opened = str(item.get("opened_at") or "")
        rows.append(
            {
                "Time": opened[11:19] if len(opened) >= 19 else opened,
                "Source": _lane(item),
                "Trigger": item.get("trigger_type") or "LEGACY",
                "State": item.get("trigger_state") or "—",
                "Direction": item.get("signal_direction") or "—",
                "Strategy": item.get("setup"),
                "Score": item.get("trigger_score") or item.get("decision_confidence"),
                "Alignment": item.get("alignment_state") or "—",
                "Status": item.get("status"),
                "Outcome": item.get("outcome") or "MONITORING",
                "MFE ₹": item.get("mfe_rupees"),
                "MAE ₹": item.get("mae_rupees"),
                "Est. Net ₹": item.get("net_pnl_rupees"),
                "Why result": item.get("diagnosis_summary") or "Monitoring",
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    items = list(reversed(today))
    labels = [
        f"{str(item.get('opened_at') or '')[11:19]} · {_lane(item)} · {item.get('setup')} · {item.get('trade_id')}"
        for item in items
    ]
    selected = st.selectbox("Trade ka complete audit", labels, key="shadow_trade_detail")
    selected_item = items[labels.index(selected)]
    st.write("**Kyun liya:**")
    for reason in selected_item.get("entry_reasons") or ("Reason unavailable",):
        st.write(f"• {reason}")
    if selected_item.get("diagnosis_summary"):
        st.write("**Result / likely reason:**")
        st.write(selected_item.get("diagnosis_summary"))
        for reason in selected_item.get("diagnosis_factors") or ():
            st.write(f"• {reason}")
        st.caption(selected_item.get("diagnosis_basis") or "")
    outcome_cols = st.columns(3)
    outcome_cols[0].metric("5m directional", selected_item.get("directional_outcome_5m_points") if selected_item.get("directional_outcome_5m_points") is not None else "—")
    outcome_cols[1].metric("15m directional", selected_item.get("directional_outcome_15m_points") if selected_item.get("directional_outcome_15m_points") is not None else "—")
    outcome_cols[2].metric("30m directional", selected_item.get("directional_outcome_30m_points") if selected_item.get("directional_outcome_30m_points") is not None else "—")
    if selected_item.get("legs"):
        st.dataframe(pd.DataFrame(selected_item["legs"]), width="stretch", hide_index=True)
    with st.expander("Entry evidence snapshot", expanded=False):
        context = selected_item.get("entry_context") or {}
        if context:
            st.json(context)
        else:
            st.caption("Legacy row — compact entry intelligence context unavailable.")


def render_shadow_journal_download(entries: list[dict[str, Any]], session_date: str, store=None) -> None:
    """Expose decision and paper-validation records separately for post-market review."""
    decisions = _decision_rows(store)
    decision_rows = [row for row in decisions if str(row.get("session_date")) == session_date]
    paper_rows = [item for item in entries if str(item.get("session_date")) == session_date]
    c1, c2 = st.columns(2)
    with c1:
        csv = pd.DataFrame(decision_rows).to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download Decision Journal CSV",
            data=csv,
            file_name=f"nifty_decision_journal_{session_date}.csv",
            mime="text/csv",
            width="stretch",
            disabled=not bool(decision_rows),
        )
    with c2:
        csv = pd.DataFrame(paper_rows).to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download AI Paper Validation CSV",
            data=csv,
            file_name=f"ai_paper_validation_{session_date}.csv",
            mime="text/csv",
            width="stretch",
            disabled=not bool(paper_rows),
        )
