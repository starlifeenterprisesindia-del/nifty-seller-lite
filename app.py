from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from config import CONFIG
from models import Credentials
from services.dhan_client import DhanClient
from services.instrument_master import InstrumentMaster
from services.snapshot_service import SnapshotService
from ui.components import (
    render_candles,
    render_core_evidence,
    render_feed_status,
    render_header,
    render_heavyweights,
    render_indicators,
    render_levels,
    render_market_session,
    render_option_chain,
    render_price_action,
    render_volume,
)


st.set_page_config(page_title=CONFIG.app_name, page_icon="📈", layout="wide")
st.title("📈 Nifty Seller Lite")
st.caption(
    "V0.5 Core Market Engine — Price Action, Support/Resistance, NIFTY Futures "
    "Volume and existing EMA/MACD/RSI connected through one snapshot. No strategy "
    "score or order placement yet."
)


def secret_value(name: str) -> str:
    try:
        if "dhan" in st.secrets and name in st.secrets["dhan"]:
            return str(st.secrets["dhan"][name])
    except Exception:
        pass
    return os.getenv(f"DHAN_{name.upper()}", "")


client_id = secret_value("client_id")
access_token = secret_value("access_token")

with st.sidebar:
    st.subheader("Connection")
    st.write(f"Version: `{CONFIG.version}`")
    st.write("Mode: **READ ONLY**")
    credentials_ready = bool(client_id and access_token)
    if credentials_ready:
        st.success("Dhan credentials found")
    else:
        st.error("Dhan credentials missing")
    st.caption("Credentials remain only in Streamlit Secrets under [dhan].")
    refresh = st.button(
        "Fetch Fresh Snapshot", type="primary", use_container_width=True
    )
    clear_cache = st.button("Clear instrument cache", use_container_width=True)
    if clear_cache:
        cache = Path("data/instrument_master.csv")
        if cache.exists():
            cache.unlink()
        st.success("Instrument cache cleared")

if not credentials_ready:
    st.code(
        '[dhan]\nclient_id = "YOUR_CLIENT_ID"\naccess_token = "YOUR_24_HOUR_ACCESS_TOKEN"',
        language="toml",
    )
    st.stop()

if "snapshot" not in st.session_state or refresh:
    try:
        with st.spinner(
            "Building one authoritative DhanHQ snapshot and core evidence..."
        ):
            credentials = Credentials(client_id=client_id, access_token=access_token)
            client = DhanClient(credentials)
            service = SnapshotService(
                client, InstrumentMaster(Path("data/instrument_master.csv"))
            )
            st.session_state.snapshot = service.build()
    except Exception as exc:
        st.error(f"Snapshot failed safely: {exc}")
        st.stop()

snapshot = st.session_state.snapshot
render_market_session(snapshot)
render_header(snapshot)

st.subheader("Core Market Evidence — No Strategy Decision Yet")
render_core_evidence(snapshot)

core_tabs = st.tabs(
    [
        "Price Action",
        "Support & Resistance",
        "Volume",
        "EMA / MACD / RSI",
        "Feed Integrity",
    ]
)
with core_tabs[0]:
    render_price_action(snapshot)
with core_tabs[1]:
    render_levels(snapshot)
with core_tabs[2]:
    render_volume(snapshot)
with core_tabs[3]:
    render_indicators(snapshot)
with core_tabs[4]:
    render_feed_status(snapshot)

st.subheader("Raw Market Data")
market_tabs = st.tabs(
    [
        "Candles & Futures Volume",
        "Option Chain",
        "Top-7 Quotes",
        "VIX & Future",
        "Snapshot JSON",
    ]
)
with market_tabs[0]:
    render_candles(snapshot)
with market_tabs[1]:
    render_option_chain(snapshot)
with market_tabs[2]:
    render_heavyweights(snapshot)
with market_tabs[3]:
    left, right = st.columns(2)
    left.write("**India VIX quote**")
    left.json(snapshot.vix_quote or {"status": "not resolved"})
    right.write("**Nearest NIFTY future quote**")
    right.json(snapshot.nifty_future_quote or {"status": "not resolved"})
with market_tabs[4]:
    st.json(snapshot.public_summary())

st.info(
    "Next big milestone: Options Intelligence — persistent intraday OI change, "
    "premium + OI + option volume flow, PCR, wall migration and Top-7 weighted contribution."
)
