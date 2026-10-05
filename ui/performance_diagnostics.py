"""Presentation-only performance panel."""
from __future__ import annotations

import streamlit as st

from analysis.performance_diagnostics import build_performance_report


def render_performance_diagnostics(
    snapshot,
    *,
    refresh_interval_seconds: float = 30.0,
    snapshot_built_now: bool = False,
    pipeline_history: list[float] | None = None,
) -> None:
    report = build_performance_report(
        snapshot,
        refresh_interval_seconds=refresh_interval_seconds,
        snapshot_built_now=snapshot_built_now,
        pipeline_history=pipeline_history,
    )
    st.caption(report["note"])
    a, b, c, d = st.columns(4)
    a.metric("Pipeline", f"{report['pipeline_seconds']:.2f}s", help="Current full snapshot pipeline ka recorded processing time.")
    b.metric("Snapshot build", f"{report['build_seconds']:.2f}s", help="Fresh snapshot construct hone ka recorded time.")
    c.metric("Headroom", f"{report['refresh_headroom_seconds']:.1f}s", help="Next refresh budget me estimated spare time; higher is better.")
    d.metric("Refresh load", f"{report['refresh_load_pct']:.1f}%", help="Configured refresh interval ka kitna hissa processing ne consume kiya.")

    if report.get("latency_samples", 0):
        p1, p2, p3 = st.columns(3)
        p1.metric("P50 latency", f"{report['p50_pipeline_seconds']:.2f}s")
        p2.metric("P95 latency", f"{report['p95_pipeline_seconds']:.2f}s")
        p3.metric("Samples", int(report["latency_samples"]))

    transport = report.get("transport") or {}
    if transport:
        st.caption(
            "Transport: "
            f"{transport.get('mode', '—')} · HTTP {transport.get('http_calls', 0)} · "
            f"prefetch hits {transport.get('prefetch_hits', 0)} · "
            f"saved ~{transport.get('estimated_round_trips_saved', 0)} round-trips · "
            f"bundle {float(transport.get('bundle_seconds') or 0.0):.2f}s"
        )
        if transport.get("bundle_errors"):
            st.caption(f"Bundle partial fallback: {transport.get('bundle_errors')}")

    async_write = report.get("async_evidence") or {}
    if async_write:
        st.caption(
            f"Evidence write: async · pending {async_write.get('pending', 0)} · "
            f"done {async_write.get('completed', 0)} · failed {async_write.get('failed', 0)}"
        )
    history_async = report.get("history_async") or {}
    if history_async:
        st.caption(
            f"Journal report: async · pending {bool(history_async.get('pending'))} · "
            f"last {float(history_async.get('seconds') or 0.0):.2f}s · "
            f"error {history_async.get('error') or 'none'}"
        )
    master = report.get("instrument_master") or {}
    if master:
        st.caption(
            f"Instrument master: cache {'READY' if master.get('cache_ready') else 'WARMING'} · "
            f"prewarm {'RUNNING' if master.get('running') else 'IDLE'} · "
            f"error {master.get('error') or 'none'}"
        )

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
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.info("Stage timing next fresh snapshot ke baad available hogi.")
    with tab2:
        rows = report.get("feed_rows") or []
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.info("Feed diagnostics unavailable.")
