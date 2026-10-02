from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from analysis.advanced_options_display import build_phase8_payload


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _history_inputs(snapshot: Any, option_state_store: Any | None) -> tuple[list[float], list[dict[str, Any]]]:
    """Read saved IV context only when this panel is opened; never calls broker/API."""
    if option_state_store is None:
        return [], []
    session_summaries: list[dict[str, Any]] = []
    intraday: list[dict[str, Any]] = []
    try:
        session_summaries = option_state_store.load_iv_history(limit=60)
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
    historical = []
    for row in session_summaries:
        try:
            value = float(row.get("last"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            historical.append(value)
    return historical, intraday


def render_phase2_options_intelligence(snapshot: Any, option_state_store: Any | None = None) -> None:
    """Phase-8 volatility + Phase-2 options intelligence, display-only and on-open."""
    historical_iv, intraday_history = _history_inputs(snapshot, option_state_store)
    payload = build_phase8_payload(
        snapshot,
        historical_atm_iv=historical_iv,
        intraday_history=intraday_history,
    )
    st.caption(
        "⚡ Advanced Options + Phase-8 Volatility Intelligence · on-open/display-only · "
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

    tab1, tab2, tab3, tab4 = st.tabs(
        ["🔥 Unusual Activity", "💧 Liquidity Grade", "🌡️ IV / Straddle", "📈 Skew + IV Surface"]
    )
    with tab1:
        rows = payload["unusual_activity"]
        if rows:
            view = pd.DataFrame(rows).rename(columns={
                "strike": "Strike", "side": "Side", "classification": "Flow",
                "bias": "Bias", "oi_delta": "OI Δ", "volume_delta": "Volume Δ",
                "premium_delta": "Premium Δ", "score": "Activity Score", "tag": "Tag",
            })
            st.dataframe(view, use_container_width=True, hide_index=True)
            st.caption(
                "Relative anomaly score = OI + volume + premium movement + existing flow strength + ATM proximity. "
                "It is context, not an independent trade signal."
            )
        else:
            st.caption("Unusual activity warming up — matched intraday option-flow rows abhi enough nahi hain.")

    with tab2:
        rows = payload["liquidity"]
        if rows:
            view = pd.DataFrame(rows).rename(columns={
                "strike": "Strike", "side": "Side", "ltp": "LTP", "bid": "Bid", "ask": "Ask",
                "spread_pct": "Spread %", "oi": "OI", "volume": "Volume", "distance": "ATM Dist",
                "score": "Liquidity", "grade": "Grade",
            })
            st.dataframe(view, use_container_width=True, hide_index=True)
            st.caption(
                "Grade uses current bid-ask spread + relative OI + relative volume. Existing TradePlan liquidity logic is unchanged."
            )
        else:
            st.caption("Liquidity board unavailable because option-chain rows are missing/reference-only.")

    with tab3:
        a, b, c, d = st.columns(4)
        a.metric("ATM IV", _fmt(iv.get("atm_iv"), 2, "%"))
        b.metric("Chain median IV", _fmt(iv.get("chain_median_iv"), 2, "%"))
        c.metric("IV history", str(iv.get("history_sessions", 0)) + " sessions")
        d.metric("Volatility regime", str(regime.get("regime") or "—"))
        st.caption(iv.get("ivr_status") or "IV history unavailable")
        st.caption(
            "True IV Rank/Percentile fake nahi kiya gaya. Same option-state persistence ab har live session ka compact ATM-IV summary save karega; "
            ">=20 real sessions ke baad IVR/IVP READY hoga."
        )
        series = payload.get("intraday_iv") or []
        if series:
            chart = pd.DataFrame(series)
            chart["at"] = pd.to_datetime(chart["at"], errors="coerce")
            chart = chart.dropna(subset=["at", "atm_iv"]).set_index("at")
            if not chart.empty:
                st.markdown("**Intraday ATM IV trend**")
                st.line_chart(chart[["atm_iv"]], use_container_width=True)

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
            st.line_chart(plot, use_container_width=True)
            table = frame.rename(columns={
                "strike": "Strike", "moneyness_pct": "Moneyness %", "ce_iv": "CE IV",
                "pe_iv": "PE IV", "mid_iv": "Mid IV", "ce_delta": "CE Δ", "pe_delta": "PE Δ",
                "ce_oi": "CE OI", "pe_oi": "PE OI", "atm": "ATM",
            })
            st.markdown("**Current-expiry IV surface / strike matrix**")
            st.dataframe(table, use_container_width=True, hide_index=True)
            st.caption(
                "Ye current selected expiry ka strike × CE/PE IV surface hai. Multi-expiry surface ke liye extra expiry-chain fetch nahi kiya gaya, "
                "taaki Dhan rate limit aur One-Brain latency safe rahe."
            )
        else:
            st.caption("IV smile/surface unavailable — valid current-expiry IV rows nahi hain.")
