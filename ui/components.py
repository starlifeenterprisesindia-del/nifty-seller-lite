from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd
import streamlit as st

from models import MarketLevel, MarketSnapshot, TimeframeIndicators


def render_market_session(snapshot: MarketSnapshot) -> None:
    session = snapshot.market_session
    if session.is_live:
        st.success(f"🟢 {session.label} — {session.message}")
    else:
        st.warning(f"🟡 {session.label} — {session.message}")


def render_header(snapshot: MarketSnapshot) -> None:
    quote = snapshot.nifty_quote
    last_price = quote.get("last_price")
    ohlc = quote.get("ohlc") or {}
    previous_close = ohlc.get("close")
    change_pct = None
    if previous_close not in (None, 0) and last_price is not None:
        change_pct = (
            (float(last_price) - float(previous_close)) / float(previous_close) * 100
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "NIFTY",
        f"{float(last_price):,.2f}" if last_price is not None else "—",
        f"{change_pct:+.2f}%" if change_pct is not None else None,
    )
    c2.metric("Expiry", snapshot.expiry or "Unavailable")
    c3.metric("Snapshot", snapshot.snapshot_id[-8:])
    c4.metric("Created", snapshot.created_at.strftime("%H:%M:%S IST"))


def render_feed_status(snapshot: MarketSnapshot) -> None:
    rows = []
    for key, status in snapshot.feed_status.items():
        rows.append(
            {
                "Feed": key,
                "Available": "YES" if status.ok else "NO",
                "Use": status.use_state,
                "Age sec": status.age_seconds,
                "Message": status.message,
                "Source": status.source,
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_core_evidence(snapshot: MarketSnapshot) -> None:
    item = snapshot.core_evidence
    st.caption(
        "Core Market Evidence is not a CE/PE/Condor decision. It combines only the "
        "completed-candle price, indicator, level and NIFTY-futures-volume modules."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bullish Evidence", f"{item.bullish_score:.1f}%")
    c2.metric("Bearish Evidence", f"{item.bearish_score:.1f}%")
    c3.metric("Range / Mixed", f"{item.range_score:.1f}%")
    c4.metric("Evidence Confidence", f"{item.confidence:.1f}%")
    st.info(
        f"**Core state:** {item.market_state}  |  **Move stage:** {item.move_stage}  |  "
        f"**Status:** {item.status}"
    )
    left, right = st.columns(2)
    with left:
        st.write("**Main evidence**")
        for reason in item.reasons or ("No consolidated reason available",):
            st.write(f"• {reason}")
    with right:
        st.write("**Current blockers / cautions**")
        if item.blockers:
            for blocker in item.blockers:
                st.write(f"• {blocker}")
        else:
            st.write("• None in the core engine")


def _price_action_row(item: Any) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Structure": item.structure,
        "Current Event": item.event,
        "Move Stage": item.move_stage,
        "Last Swing High": item.last_swing_high,
        "Last Swing Low": item.last_swing_low,
        "Invalidation": item.invalidation_level,
        "ATR 14": item.atr14,
        "Bullish": item.bullish_score,
        "Bearish": item.bearish_score,
        "Range": item.range_score,
        "Confidence": item.confidence,
        "Status": item.status,
    }


def render_price_action(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.price_action
    st.caption(
        f"Cross-timeframe view: **{bundle.combined_state}** — {bundle.relationship} "
        f"(confidence {bundle.confidence:.1f}%)."
    )
    rows = [
        _price_action_row(bundle.three_minute),
        _price_action_row(bundle.fifteen_minute),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Price Action reasons"):
        st.json(
            {
                "3m": asdict(bundle.three_minute),
                "15m": asdict(bundle.fifteen_minute),
                "relationship": bundle.relationship,
            }
        )


def _level_row(level: MarketLevel | None, fallback: str) -> dict[str, Any]:
    if level is None:
        return {
            "Level": fallback,
            "Zone": "—",
            "Midpoint": None,
            "Distance": None,
            "Strength": None,
            "Status": "UNAVAILABLE",
            "Sources": "—",
        }
    return {
        "Level": f"{level.label} {level.side}",
        "Zone": f"{level.lower:,.2f}–{level.upper:,.2f}",
        "Midpoint": level.midpoint,
        "Distance": level.distance_points,
        "Strength": level.strength,
        "Status": level.status,
        "Sources": ", ".join(level.sources),
    }


def render_levels(snapshot: MarketSnapshot) -> None:
    item = snapshot.levels
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Upside Room",
        f"{item.upside_room:.1f} pts" if item.upside_room is not None else "—",
    )
    c2.metric(
        "Downside Room",
        f"{item.downside_room:.1f} pts" if item.downside_room is not None else "—",
    )
    c3.metric(
        "Opening Range High",
        f"{item.opening_range_high:,.2f}"
        if item.opening_range_high is not None
        else "—",
    )
    c4.metric(
        "Opening Range Low",
        f"{item.opening_range_low:,.2f}" if item.opening_range_low is not None else "—",
    )
    st.caption(
        f"Current position: **{item.current_position}** | Zone width: "
        f"{item.zone_width if item.zone_width is not None else '—'} points | Status: {item.status}"
    )
    rows = [
        _level_row(item.immediate_support, "Immediate Support"),
        _level_row(item.strong_support, "Strong Support"),
        _level_row(item.immediate_resistance, "Immediate Resistance"),
        _level_row(item.strong_resistance, "Strong Resistance"),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _volume_row(item: Any) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Current Volume": item.current_volume,
        "Time-Normalized Baseline": item.baseline_volume,
        "Relative Volume": item.relative_volume,
        "Volume State": item.volume_state,
        "Trend": item.volume_trend,
        "Price Candle": item.price_direction,
        "Move Support": item.move_support,
        "Baseline Samples": item.baseline_samples,
        "Confidence": item.confidence,
        "Status": item.status,
    }


def render_volume(snapshot: MarketSnapshot) -> None:
    item = snapshot.volume
    st.caption(
        "Volume uses the nearest NIFTY futures contract, not NIFTY index pseudo-volume. "
        "The baseline compares the same intraday time slot across prior sessions, with a "
        "recent-bar fallback only when necessary."
    )
    st.write(
        f"**Source:** {item.source} | **Overall view:** {item.overall_view} | "
        f"**Confidence:** {item.confidence:.1f}% | **Status:** {item.status}"
    )
    rows = [_volume_row(item.three_minute), _volume_row(item.fifteen_minute)]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_candles(snapshot: MarketSnapshot) -> None:
    tabs = st.tabs(
        [
            "NIFTY 3m",
            "NIFTY 15m",
            "NIFTY 1m",
            "Future 3m Volume",
            "Future 15m Volume",
        ]
    )
    with tabs[0]:
        st.caption(
            "3-minute candles are aggregated from Dhan 1-minute candles at 09:15 IST."
        )
        st.dataframe(
            snapshot.candles_3m.tail(30), use_container_width=True, hide_index=True
        )
    with tabs[1]:
        st.dataframe(
            snapshot.candles_15m.tail(30), use_container_width=True, hide_index=True
        )
    with tabs[2]:
        st.dataframe(
            snapshot.candles_1m.tail(30), use_container_width=True, hide_index=True
        )
    with tabs[3]:
        st.dataframe(
            snapshot.future_candles_3m.tail(30),
            use_container_width=True,
            hide_index=True,
        )
    with tabs[4]:
        st.dataframe(
            snapshot.future_candles_15m.tail(30),
            use_container_width=True,
            hide_index=True,
        )


def render_option_chain(snapshot: MarketSnapshot) -> None:
    if snapshot.option_chain.empty:
        st.warning("Option chain is unavailable in this snapshot.")
        return
    columns = [
        "strike",
        "side",
        "is_atm",
        "last_price",
        "oi",
        "day_oi_change",
        "volume",
        "previous_close_price",
        "day_price_change",
        "implied_volatility",
        "top_bid_price",
        "top_ask_price",
    ]
    available = [col for col in columns if col in snapshot.option_chain.columns]
    st.caption(
        "Raw option data only. Intraday OI-flow intelligence will arrive in the next big "
        "Options Intelligence milestone after snapshot persistence is implemented."
    )
    st.dataframe(
        snapshot.option_chain[available], use_container_width=True, hide_index=True
    )


def render_heavyweights(snapshot: MarketSnapshot) -> None:
    if not snapshot.heavyweight_quotes:
        st.info("Top-7 quotes are unavailable in this snapshot.")
        return
    rows: list[dict[str, Any]] = []
    for item in snapshot.heavyweight_quotes:
        ohlc = item.get("ohlc") or {}
        previous_close = ohlc.get("close")
        last = item.get("last_price")
        change_pct = None
        if previous_close not in (None, 0) and last is not None:
            change_pct = (
                (float(last) - float(previous_close)) / float(previous_close) * 100
            )
        rows.append(
            {
                "Symbol": item.get("symbol"),
                "Name": item.get("display_name"),
                "Last": last,
                "Change %": change_pct,
                "Day High": ohlc.get("high"),
                "Day Low": ohlc.get("low"),
                "Volume": item.get("volume"),
                "Security ID": item.get("security_id"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _indicator_row(item: TimeframeIndicators) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Close": item.close,
        "EMA 20": item.ema20,
        "EMA 50": item.ema50,
        "EMA State": item.ema_state,
        "MACD": item.macd,
        "Signal": item.macd_signal,
        "Histogram": item.macd_histogram,
        "MACD State": item.macd_state,
        "RSI 14": item.rsi14,
        "RSI State": item.rsi_state,
        "Status": item.status,
    }


def render_indicators(snapshot: MarketSnapshot) -> None:
    st.caption(
        "EMA/MACD/RSI use completed candles from the same authoritative snapshot. "
        "They remain evidence, not a standalone decision."
    )
    rows = [
        _indicator_row(snapshot.indicators.three_minute),
        _indicator_row(snapshot.indicators.fifteen_minute),
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Indicator JSON"):
        st.json(
            {
                "3m": asdict(snapshot.indicators.three_minute),
                "15m": asdict(snapshot.indicators.fifteen_minute),
            }
        )
