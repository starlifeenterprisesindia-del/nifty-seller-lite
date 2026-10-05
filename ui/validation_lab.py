"""Phase-6 Validation + Robustness Lab UI: explicit/on-demand recorded-data analysis only."""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from analysis.validation_lab import build_validation_report
from services.railway_live_client import RailwayDhanClient


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _load_replay(url: str, key: str) -> dict[str, Any] | None:
    cached = st.session_state.get("phase3_replay_bundle")
    if isinstance(cached, dict) and cached.get("timeline"):
        return cached
    try:
        bundle = RailwayDhanClient(url, key, timeout_seconds=10)._post(
            "/day-memory-replay", {"max_rows": 390}
        )
        if isinstance(bundle, dict):
            st.session_state.phase3_replay_bundle = bundle
            return bundle
    except Exception as exc:
        st.session_state.phase5_validation_error = type(exc).__name__
    return None


def render_phase5_validation_lab(snapshot: Any, url: str, key: str) -> None:
    st.caption(
        "🧪 Phase-6 Robustness Backtest · recorded One Brain decisions vs later NIFTY spot only · "
        "no broker market-data call · no threshold auto-tuning"
    )
    if not url or not key:
        st.info("Validation Lab ke liye Railway persistent recorder connection required hai.")
        return

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        horizon = st.selectbox("Outcome horizon", [5, 15, 30], index=1, key="phase5_horizon")
    with c2:
        flat = st.number_input(
            "Flat band (NIFTY pts)", min_value=0.0, max_value=50.0, value=5.0, step=1.0,
            key="phase5_flat_points",
        )
    with c3:
        wait_move = st.slider(
            "WAIT review: large move threshold", 10, 100, 25, 5, key="phase5_wait_move"
        )

    load = st.button("Load / Refresh Validation", key="phase5_load_validation", width="stretch")
    if load:
        st.session_state.pop("phase3_replay_bundle", None)
        st.session_state.pop("phase5_validation_error", None)

    bundle = _load_replay(url, key)
    if st.session_state.get("phase5_validation_error"):
        st.warning("Recorded validation data load nahi hua; live One Brain safe aur unchanged hai.")
    if not isinstance(bundle, dict) or not bundle.get("timeline"):
        st.info("Abhi recorded session data available nahi hai.")
        return

    report = build_validation_report(
        bundle, horizon=int(horizon), flat_points=float(flat), wait_large_move_points=float(wait_move)
    )
    directional = report["directional"]
    a, b, c, d, e = st.columns(5)
    a.metric("Scored signals", directional.get("rows", 0))
    b.metric("HIT", directional.get("hits", 0))
    c.metric("MISS", directional.get("misses", 0))
    d.metric("FLAT", directional.get("flat", 0))
    e.metric("Directional hit rate", _pct(directional.get("directional_hit_rate_pct")))
    st.caption(
        f"Session {report.get('session_date') or '—'} · {report['horizon_minutes']}m spot outcome · "
        f"flat band ±{report['flat_points']:.1f} pts. Hit-rate denominator excludes FLAT/WAIT/Condor."
    )

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
        ["Walk-forward", "Readiness", "Regime", "Action matrix", "Evidence alignment", "Miss review", "WAIT review"]
    )
    with tab1:
        wf = report["walk_forward"]
        if wf.get("status") != "READY":
            st.info(wf.get("note") or "Insufficient data")
        else:
            left, right = st.columns(2)
            ref, val = wf["reference"], wf["validation"]
            left.metric("Reference 60%", _pct(ref.get("directional_hit_rate_pct")), f"n={ref.get('rows',0)}")
            right.metric("Validation 40%", _pct(val.get("directional_hit_rate_pct")), f"n={val.get('rows',0)}")
            st.caption(wf.get("note") or "")
        st.markdown("**Readiness sensitivity — descriptive filter only**")
        st.dataframe(pd.DataFrame(report["sensitivity"]), hide_index=True, width="stretch")
        st.caption("Ye table purane decisions ko filter karti hai; ye prove nahi karti ki threshold change karne par wahi decisions hote.")

    with tab2:
        frame = pd.DataFrame(report["by_readiness"])
        if frame.empty:
            st.caption("Readiness-bucket observations abhi available nahi hain.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")

    with tab3:
        frame = pd.DataFrame(report["by_regime"])
        if frame.empty:
            st.caption("Regime validation observations abhi available nahi hain.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")

    with tab4:
        st.markdown("**Directional result by action**")
        action_frame = pd.DataFrame(report.get("by_action") or [])
        if action_frame.empty:
            st.caption("Action-wise scored observations abhi available nahi hain.")
        else:
            st.dataframe(action_frame, hide_index=True, width="stretch")
        st.markdown("**Regime × action robustness**")
        cross = pd.DataFrame(report.get("by_regime_action") or [])
        if not cross.empty:
            st.dataframe(cross, hide_index=True, width="stretch")
        st.markdown("**5m / 15m / 30m horizon matrix**")
        st.dataframe(pd.DataFrame(report.get("horizon_matrix") or []), hide_index=True, width="stretch")

    with tab5:
        align = pd.DataFrame(report.get("evidence_alignment") or [])
        if align.empty:
            st.caption("Big Player / option-bias alignment observations abhi available nahi hain.")
        else:
            st.dataframe(align, hide_index=True, width="stretch")
        st.caption("Alignment is descriptive only: Big Player + option-bias recorded context vs recorded directional action.")
        rolling = report.get("rolling_stability") or {}
        st.markdown("**Rolling stability**")
        if rolling.get("status") != "READY":
            st.info(rolling.get("note") or "Insufficient data")
        else:
            if rolling.get("hit_rate_spread_pct_points") is not None:
                st.metric("Rolling hit-rate spread", f"{rolling['hit_rate_spread_pct_points']:.1f} pp")
            st.dataframe(pd.DataFrame(rolling.get("rows") or []), hide_index=True, width="stretch")
            st.caption(rolling.get("note") or "")
        mf = report.get("multi_fold_walk_forward") or {}
        st.markdown("**Multi-fold chronological validation**")
        if mf.get("status") != "READY":
            st.info(mf.get("note") or "Insufficient data")
        else:
            st.dataframe(pd.DataFrame(mf.get("rows") or []), hide_index=True, width="stretch")
            st.caption(mf.get("note") or "")

    with tab6:
        misses = pd.DataFrame(report["misses"])
        if misses.empty:
            st.success("Selected horizon par recorded directional MISS rows nahi mile.")
        else:
            st.dataframe(misses, hide_index=True, width="stretch")
        st.caption("MISS = recorded directional action ke opposite NIFTY move; option P&L/fill ka verdict nahi.")

    with tab7:
        wait = report["wait_review"]
        x, y, z = st.columns(3)
        x.metric("WAIT outcomes covered", wait.get("covered_waits", 0))
        y.metric("Large moves after WAIT", wait.get("large_move_waits", 0))
        z.metric("Median |move|", "—" if wait.get("median_abs_move_points") is None else f"{wait['median_abs_move_points']:.1f} pts")
        frame = pd.DataFrame(wait.get("largest") or [])
        if not frame.empty:
            st.dataframe(frame, hide_index=True, width="stretch")
        st.caption(wait.get("note") or "")

    with st.expander("Validation limitations", expanded=False):
        for item in report.get("limitations") or []:
            st.write("• " + str(item))
    st.caption(
        "Golden Rule: Robustness Backtest display/research layer hai. Koi One Brain weight, threshold, "
        "strategy selection, execution guard ya broker/API market-data path yahan se change nahi hota."
    )
