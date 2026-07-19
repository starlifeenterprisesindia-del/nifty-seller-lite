from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from models import MarketSnapshot


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
                "Status": "OK" if status.ok else "CAUTION",
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
            "3-minute candles are aggregated from Dhan 1-minute candles and anchored at 09:15 IST."
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
        "Foundation stage: day OI change is raw reference only. Intraday flow starts after snapshot persistence."
    )
    st.dataframe(snapshot.option_chain[available], use_container_width=True, hide_index=True)


def render_heavyweights(snapshot: MarketSnapshot) -> None:
    if not snapshot.heavyweight_quotes:
        st.info("Top-7 instruments were not resolved. Refresh instrument cache or check network access.")
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
                "Last": last,
                "Change %": change_pct,
                "Day High": ohlc.get("high"),
                "Day Low": ohlc.get("low"),
                "Volume": item.get("volume"),
                "Security ID": item.get("security_id"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
