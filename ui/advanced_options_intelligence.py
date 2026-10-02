from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from analysis.advanced_options_display import build_phase2_payload


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def render_phase2_options_intelligence(snapshot: Any) -> None:
    """On-open presentation analytics; never calls broker or rewrites the Brain."""
    payload = build_phase2_payload(snapshot)
    st.caption("⚡ Phase-2 Pro Intelligence · display-only · existing snapshot/cache · no extra broker call")

    straddle = payload["straddle"]
    iv = payload["iv"]
    cols = st.columns(5)
    cols[0].metric("ATM", _fmt(straddle.get("atm") or iv.get("atm"), 0))
    cols[1].metric("ATM Straddle", _fmt(straddle.get("combined_premium"), 2))
    cols[2].metric("ATM IV", _fmt(iv.get("atm_iv"), 2, "%"))
    cols[3].metric("PE-CE IV Skew", _fmt(straddle.get("iv_skew_pe_minus_ce"), 2))
    cols[4].metric("Liquidity rows", str(len(payload["liquidity"])))

    if straddle.get("status") == "READY":
        st.info(
            f"ATM {straddle['atm']:.0f}: CE ₹{straddle['ce_premium']:.2f} + PE ₹{straddle['pe_premium']:.2f} "
            f"= ₹{straddle['combined_premium']:.2f}. Spot ± premium proxy: "
            f"{straddle['lower_proxy']:.0f}–{straddle['upper_proxy']:.0f}. "
            "Ye expected-move guarantee nahi hai; sirf current ATM straddle premium context hai."
        )

    tab1, tab2, tab3 = st.tabs(["🔥 Unusual Activity", "💧 Liquidity Grade", "🌡️ IV / Straddle"])
    with tab1:
        rows = payload["unusual_activity"]
        if rows:
            view = pd.DataFrame(rows).rename(columns={
                "strike": "Strike", "side": "Side", "classification": "Flow",
                "bias": "Bias", "oi_delta": "OI Δ", "volume_delta": "Volume Δ",
                "premium_delta": "Premium Δ", "score": "Activity Score", "tag": "Tag",
            })
            st.dataframe(view, use_container_width=True, hide_index=True)
            st.caption("Relative anomaly score = OI + volume + premium movement + existing flow strength + ATM proximity. It is not a trade signal.")
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
            st.caption("Grade uses current bid-ask spread + relative OI + relative volume. Existing TradePlan liquidity logic is unchanged.")
        else:
            st.caption("Liquidity board unavailable because option-chain rows are missing/reference-only.")

    with tab3:
        a, b, c, d = st.columns(4)
        a.metric("ATM IV", _fmt(iv.get("atm_iv"), 2, "%"))
        b.metric("Chain median IV", _fmt(iv.get("chain_median_iv"), 2, "%"))
        c.metric("IV Rank", _fmt(iv.get("iv_rank"), 1))
        d.metric("IV Percentile", _fmt(iv.get("iv_percentile"), 1))
        st.caption(iv.get("ivr_status") or "IV history unavailable")
        st.caption(
            "True IV Rank/Percentile ko fake nahi kiya gaya. Ye tab READY hoga jab >=20 real historical ATM-IV sessions available honge. "
            "Current Phase-2 screen abhi current IV/skew/straddle context safely dikhata hai."
        )
