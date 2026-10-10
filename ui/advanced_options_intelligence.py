from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from config import CONFIG

from analysis.advanced_options_display import build_phase9_payload
from analysis.research_edge_context import dte_matched_iv_history


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _history_inputs(snapshot: Any, option_state_store: Any | None) -> tuple[list[float], list[dict[str, Any]], dict[str, Any]]:
    """Read same-DTE saved IV context only when this panel is opened; never calls broker/API."""
    if option_state_store is None:
        return [], [], {"bucket": "UNKNOWN", "days_to_expiry": None, "matched_sessions": 0}
    session_summaries: list[dict[str, Any]] = []
    intraday: list[dict[str, Any]] = []
    try:
        session_summaries = option_state_store.load_iv_history(limit=getattr(CONFIG, "iv_history_max_sessions", 160))
    except Exception:
        session_summaries = []
    try:
        if getattr(snapshot, "expiry", None):
            intraday = option_state_store.load_session(
                captured_at=snapshot.created_at,
                expiry=str(snapshot.expiry),
            )
    except Exception:
        intraday = []
    historical, _rows, bucket, days = dte_matched_iv_history(snapshot, session_summaries)
    return historical, intraday, {
        "bucket": bucket,
        "days_to_expiry": days,
        "matched_sessions": len(historical),
        "all_saved_sessions": len(session_summaries),
    }


def render_phase2_options_intelligence(snapshot: Any, option_state_store: Any | None = None) -> None:
    """Phase-10 advanced options intelligence, display-only and on-open."""
    historical_iv, intraday_history, iv_history_meta = _history_inputs(snapshot, option_state_store)
    payload = build_phase9_payload(
        snapshot,
        historical_atm_iv=historical_iv,
        intraday_history=intraday_history,
    )
    st.caption(
        "⚡ Advanced Options + Phase-10 Activity/Liquidity Intelligence · on-open/display-only · "
        "existing snapshot + saved option-state · no extra Dhan/broker call"
    )

    straddle = payload["straddle"]
    iv = payload["iv"]
    skew = payload["skew"]
    regime = payload["volatility_regime"]
    cols = st.columns(6)
    cols[0].metric("ATM", _fmt(straddle.get("atm") or iv.get("atm"), 0))
    cols[1].metric("ATM Straddle", _fmt(straddle.get("combined_premium"), 2))
    cols[2].metric("ATM IV", _fmt(iv.get("atm_iv"), 2, "%"))
    cols[3].metric("IV Rank", _fmt(iv.get("iv_rank"), 1))
    cols[4].metric("IV Percentile", _fmt(iv.get("iv_percentile"), 1))
    cols[5].metric("25Δ Skew", _fmt(skew.get("rr25_put_minus_call"), 2))

    if straddle.get("status") == "READY":
        st.info(
            f"ATM {straddle['atm']:.0f}: CE ₹{straddle['ce_premium']:.2f} + PE ₹{straddle['pe_premium']:.2f} "
            f"= ₹{straddle['combined_premium']:.2f}. Spot ± premium proxy: "
            f"{straddle['lower_proxy']:.0f}–{straddle['upper_proxy']:.0f}. "
            "Ye expected-move guarantee nahi hai; current ATM straddle premium context hai."
        )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["🔥 Unusual Activity", "💧 Liquidity Grade", "🌡️ IV / Straddle", "📈 Skew + IV Surface", "⚡ Straddle / Strangle"]
    )
    with tab1:
        rows = payload["unusual_activity"]
        clusters = payload.get("activity_clusters") or []
        if clusters:
            st.markdown("**Clustered activity zones**")
            cview = pd.DataFrame(clusters).rename(columns={
                "bias": "Bias", "strike_from": "From", "strike_to": "To",
                "contracts": "Contracts", "sides": "Sides", "avg_score": "Avg Score",
                "peak_score": "Peak Score", "state": "State",
            })
            st.dataframe(cview, width="stretch", hide_index=True)
        if rows:
            st.markdown("**Contract-level unusual activity**")
            view = pd.DataFrame(rows).rename(columns={
                "strike": "Strike", "side": "Side", "classification": "Flow",
                "bias": "Bias", "oi_delta": "OI Δ", "volume_delta": "Volume Δ",
                "premium_delta": "Premium Δ", "iv": "IV", "iv_rank_chain": "IV Rank*",
                "window_confirm": "Window Confirm", "confirmation": "Confirmation",
                "reason": "Why", "score": "Activity Score", "tag": "Tag",
            })
            st.dataframe(view, width="stretch", hide_index=True)
            st.caption(
                "Phase-10 relative anomaly score = OI + volume + premium movement + current-IV richness + existing flow strength + "
                "ATM proximity + 1m/3m/5m bias confirmation. *IV Rank here is within the current fetched chain, not historical IV Rank. "
                "It is context, not an independent trade signal or proof of institutional identity."
            )
        else:
            st.caption("Unusual activity warming up — matched intraday option-flow rows abhi enough nahi hain.")

    with tab2:
        rows = payload["liquidity"]
        summary = payload.get("liquidity_summary") or {}
        if summary.get("status") == "READY":
            a, b, c, d = st.columns(4)
            a.metric("Liquidity state", str(summary.get("market_state") or "—"))
            b.metric("Entry-friendly", f"{summary.get('friendly_contracts', 0)}/{summary.get('contracts_checked', 0)}")
            c.metric("Median spread", _fmt(summary.get("median_spread_pct"), 2, "%"))
            d.metric("Avg liquidity", _fmt(summary.get("average_score"), 1))
            best_ce = summary.get("best_ce") or {}
            best_pe = summary.get("best_pe") or {}
            if best_ce or best_pe:
                st.caption(
                    f"Best current CE: {best_ce.get('strike', '—')} {best_ce.get('grade', '—')} ({best_ce.get('state', '—')}) · "
                    f"Best current PE: {best_pe.get('strike', '—')} {best_pe.get('grade', '—')} ({best_pe.get('state', '—')})."
                )
        if rows:
            view = pd.DataFrame(rows).rename(columns={
                "strike": "Strike", "side": "Side", "ltp": "LTP", "bid": "Bid", "ask": "Ask", "mid": "Mid",
                "spread_points": "Spread pts", "spread_pct": "Spread %", "oi": "OI", "volume": "Volume",
                "oi_rank": "OI Rank", "volume_rank": "Vol Rank", "distance": "ATM Dist",
                "score": "Liquidity", "grade": "Grade", "state": "Execution State",
                "half_spread_rupees_per_lot": "½-Spread ₹/lot",
            })
            st.dataframe(view, width="stretch", hide_index=True)
            st.caption(
                "Phase-10 grade uses bid-ask spread + relative OI + relative volume + ATM proximity. "
                "½-Spread ₹/lot is only a friction proxy using configured lot size; it is not a guaranteed slippage/fill estimate. "
                "Existing TradePlan liquidity logic is unchanged."
            )
        else:
            st.caption("Liquidity board unavailable because option-chain rows are missing/reference-only.")

    with tab3:
        a, b, c, d = st.columns(4)
        a.metric("ATM IV", _fmt(iv.get("atm_iv"), 2, "%"))
        b.metric("Chain median IV", _fmt(iv.get("chain_median_iv"), 2, "%"))
        c.metric("IV history", str(iv.get("history_sessions", 0)) + " sessions")
        d.metric("Volatility regime", str(regime.get("regime") or "—"))
        st.caption(
            f"{iv.get('ivr_status') or 'IV history unavailable'} · DTE bucket {iv_history_meta.get('bucket', 'UNKNOWN')} "
            f"({iv_history_meta.get('matched_sessions', 0)} matched / {iv_history_meta.get('all_saved_sessions', 0)} saved sessions)."
        )
        st.caption(
            "True IV Rank/Percentile fake nahi kiya gaya. Current ATM IV ko sirf prior real sessions ke same calendar-DTE bucket se compare kiya jata hai; "
            ">=20 matched sessions se pehle WARMING/NO VOTE."
        )
        series = payload.get("intraday_iv") or []
        if series:
            chart = pd.DataFrame(series)
            chart["at"] = pd.to_datetime(chart["at"], errors="coerce")
            chart = chart.dropna(subset=["at", "atm_iv"]).set_index("at")
            if not chart.empty:
                st.markdown("**Intraday ATM IV trend**")
                st.line_chart(chart[["atm_iv"]], width="stretch")

    with tab4:
        a, b, c, d = st.columns(4)
        a.metric("Skew state", str(skew.get("skew_state") or "—"))
        b.metric("25Δ Put IV", _fmt(skew.get("pe25_iv"), 2, "%"))
        c.metric("25Δ Call IV", _fmt(skew.get("ce25_iv"), 2, "%"))
        d.metric("Smile curvature", _fmt(skew.get("butterfly25"), 2))
        st.caption(
            f"Smile: {skew.get('smile_state') or '—'} · Vol regime: {regime.get('regime') or '—'} · "
            f"Basis: {regime.get('basis') or '—'}"
        )
        smile = payload.get("smile") or []
        if smile:
            frame = pd.DataFrame(smile)
            plot = frame[["strike", "ce_iv", "pe_iv", "mid_iv"]].copy().set_index("strike")
            st.markdown("**Current-expiry IV smile**")
            st.line_chart(plot, width="stretch")
            table = frame.rename(columns={
                "strike": "Strike", "moneyness_pct": "Moneyness %", "ce_iv": "CE IV",
                "pe_iv": "PE IV", "mid_iv": "Mid IV", "ce_delta": "CE Δ", "pe_delta": "PE Δ",
                "ce_oi": "CE OI", "pe_oi": "PE OI", "atm": "ATM",
            })
            st.markdown("**Current-expiry IV surface / strike matrix**")
            st.dataframe(table, width="stretch", hide_index=True)
            st.caption(
                "Ye current selected expiry ka strike × CE/PE IV surface hai. Multi-expiry surface ke liye extra expiry-chain fetch nahi kiya gaya, "
                "taaki Dhan rate limit aur One-Brain latency safe rahe."
            )
        else:
            st.caption("IV smile/surface unavailable — valid current-expiry IV rows nahi hain.")

    with tab5:
        expected = payload.get("expected_move") or {}
        strangles = payload.get("strangles") or {}
        decay = payload.get("straddle_decay") or {}
        d = decay.get("decay") or {}
        a, b, c, e = st.columns(4)
        a.metric("Premium move", _fmt(expected.get("premium_move_points"), 1, " pts"))
        b.metric("IV 1σ move", _fmt(expected.get("iv_1sigma_points"), 1, " pts"))
        c.metric("Premium vs IV", _fmt(expected.get("premium_vs_iv_ratio"), 2) + "x" if expected.get("premium_vs_iv_ratio") is not None else "—")
        e.metric("Move state", str(expected.get("state") or "—"))
        if expected.get("status") == "READY":
            st.caption(
                f"ATM-premium proxy: {expected.get('premium_lower', '—')}–{expected.get('premium_upper', '—')} · "
                f"IV 1σ proxy: {expected.get('iv_lower', '—')}–{expected.get('iv_upper', '—')} · "
                f"time to expiry ≈ {expected.get('days_to_expiry', '—')} days. "
                "Ye guaranteed range/probability nahi hai."
            )

        rows = strangles.get("rows") or []
        if rows:
            st.markdown("**OTM strangle context**")
            table = pd.DataFrame(rows).rename(columns={
                "label": "Setup", "pe_strike": "PE Strike", "ce_strike": "CE Strike",
                "pe_premium": "PE Premium", "ce_premium": "CE Premium", "combined_premium": "Combined",
                "lower_breakeven_proxy": "Lower BE proxy", "upper_breakeven_proxy": "Upper BE proxy",
                "wing_width": "Strike width", "combined_oi": "Combined OI", "combined_volume": "Combined Vol",
            })
            show_cols = [c for c in ["Setup", "PE Strike", "CE Strike", "PE Premium", "CE Premium", "Combined", "Lower BE proxy", "Upper BE proxy", "Strike width", "Combined OI", "Combined Vol"] if c in table]
            st.dataframe(table[show_cols], width="stretch", hide_index=True)
            st.caption("Breakeven proxy assumes long/short strangle held to expiry before charges/slippage; advisory context only.")
        else:
            st.caption("Symmetric OTM strangle rows unavailable in the current fetched strike window.")

        series = decay.get("series") or []
        multi = decay.get("multi") or []
        if series:
            frame = pd.DataFrame(series)
            frame["at"] = pd.to_datetime(frame["at"], errors="coerce")
            frame = frame.dropna(subset=["at"]).set_index("at")
            keys = [m.get("key") for m in multi if m.get("key") in frame.columns]
            if keys:
                rename = {m.get("key"): f"{int(round(float(m.get('strike'))))} Straddle" for m in multi if m.get("key")}
                st.markdown("**Multi-straddle premium trend**")
                st.line_chart(frame[keys].rename(columns=rename), width="stretch")
            if "atm_premium" in frame:
                st.markdown("**Fixed current-ATM straddle decay**")
                st.line_chart(frame[["atm_premium"]].rename(columns={"atm_premium": "ATM straddle"}), width="stretch")
        if d:
            x1, x2, x3, x4 = st.columns(4)
            x1.metric("Session decay", _fmt(d.get("decay_points"), 2, " pts"))
            x2.metric("Decay %", _fmt(d.get("decay_pct"), 1, "%"))
            x3.metric("Decay / hour", _fmt(d.get("decay_per_hour"), 2, " pts"))
            x4.metric("Last 15m Δ", _fmt(d.get("change_15m"), 2, " pts"))
            st.caption(decay.get("note") or "")
        else:
            st.caption("Decay analytics warming up — same-day option-state snapshots abhi enough nahi hain.")
        st.caption(payload.get("phase9_note") or "")
        st.caption(payload.get("phase10_note") or "")

