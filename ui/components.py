from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd
import streamlit as st

from models import MarketSnapshot, TimeframeIndicators


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
        change_pct = (float(last_price) - float(previous_close)) / float(previous_close) * 100
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


def render_candles(snapshot: MarketSnapshot) -> None:
    tabs = st.tabs(["3 Minute (derived)", "15 Minute (Dhan)", "1 Minute (source)"])
    with tabs[0]:
        st.caption(
            "3-minute candles are aggregated from Dhan 1-minute candles and anchored "
            "at 09:15 IST."
        )
        st.dataframe(snapshot.candles_3m.tail(30), use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(snapshot.candles_15m.tail(30), use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(snapshot.candles_1m.tail(30), use_container_width=True, hide_index=True)


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
        "Foundation stage: day OI change is raw reference only. Intraday flow starts "
        "after snapshot persistence."
    )
    st.dataframe(snapshot.option_chain[available], use_container_width=True, hide_index=True)


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
            change_pct = (float(last) - float(previous_close)) / float(previous_close) * 100
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
        "Read-only calculations use completed candles from the same snapshot. They do "
        "not produce a trading decision in this milestone."
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
