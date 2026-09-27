"""Presentation-only performance panel."""
from __future__ import annotations

import streamlit as st

from analysis.performance_diagnostics import build_performance_report


def render_performance_diagnostics(
    snapshot,
    *,
    refresh_interval_seconds: float = 30.0,
    snapshot_built_now: bool = False,
) -> None:
    report = build_performance_report(
        snapshot,
        refresh_interval_seconds=refresh_interval_seconds,
        snapshot_built_now=snapshot_built_now,
    )
    st.caption(report["note"])
    a, b, c, d = st.columns(4)
    a.metric("Pipeline", f"{report['pipeline_seconds']:.2f}s")
    b.metric("Snapshot build", f"{report['build_seconds']:.2f}s")
    c.metric("Headroom", f"{report['refresh_headroom_seconds']:.1f}s")
    d.metric("Refresh load", f"{report['refresh_load_pct']:.1f}%")

    slowest = report.get("slowest_stage") or "—"
    st.caption(
        f"Snapshot mode: {report['snapshot_mode']} · "
        f"Slowest stage: {slowest} {report['slowest_stage_seconds']:.2f}s · "
        f"Critical feed issues: {report['critical_issue_count']}"
    )

    tab1, tab2 = st.tabs(["Stage timing", "Feed freshness"])
    with tab1:
        rows = report.get("stage_rows") or []
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.info("Stage timing next fresh snapshot ke baad available hogi.")
    with tab2:
        rows = report.get("feed_rows") or []
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.info("Feed diagnostics unavailable.")
