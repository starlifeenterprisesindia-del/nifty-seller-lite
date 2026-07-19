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
    render_feed_status,
    render_header,
    render_heavyweights,
    render_option_chain,
)


st.set_page_config(page_title=CONFIG.app_name, page_icon="📈", layout="wide")
st.title("📈 Nifty Seller Lite")
st.caption(
    "Milestone 1 — clean read-only DhanHQ snapshot foundation. No trading advice or order placement yet."
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
    st.caption("Set credentials in Streamlit Secrets under [dhan]. They are never written to files.")
    refresh = st.button("Fetch Fresh Snapshot", type="primary", use_container_width=True)
    clear_cache = st.button("Clear instrument cache", use_container_width=True)
    if clear_cache:
        cache = Path("data/instrument_master.csv")
        if cache.exists():
            cache.unlink()
        st.success("Instrument cache cleared")

if not credentials_ready:
    example = (
        '[dhan]\n'
        'client_id = \"YOUR_CLIENT_ID\"\n'
        'access_token = \"YOUR_24_HOUR_ACCESS_TOKEN\"'
    )
    st.code(example, language="toml")
    st.stop()

if "snapshot" not in st.session_state or refresh:
    try:
        with st.spinner("Building one authoritative DhanHQ snapshot..."):
            credentials = Credentials(client_id=client_id, access_token=access_token)
            client = DhanClient(credentials)
            service = SnapshotService(
                client,
                InstrumentMaster(Path("data/instrument_master.csv")),
            )
            st.session_state.snapshot = service.build()
    except Exception as exc:
        st.error(f"Snapshot failed safely: {exc}")
        st.stop()

snapshot = st.session_state.snapshot
render_header(snapshot)

st.subheader("Feed Integrity")
render_feed_status(snapshot)

st.subheader("Raw Market Data")
market_tabs = st.tabs(
    ["Candles", "Option Chain", "Top-7 Quotes", "VIX & Future", "Snapshot JSON"]
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
    "Next milestone: verify raw values against the broker, then add EMA/MACD/RSI in an isolated tested module."
)
