from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from config import CONFIG
from models import Credentials
from services.dhan_client import DhanClient
from services.instrument_master import InstrumentMaster
from services.option_state_store import OptionStateStore
from services.snapshot_service import SnapshotService
from ui.components import (
    render_candles,
    render_core_evidence,
    render_feed_status,
    render_header,
    render_heavyweight_intelligence,
    render_heavyweights,
    render_indicators,
    render_levels,
    render_market_session,
    render_option_chain,
    render_option_flow_matrix,
    render_option_intelligence,
    render_option_windows,
    render_price_action,
    render_vix_context,
    render_volume,
    render_walls_and_pcr,
)


st.set_page_config(page_title=CONFIG.app_name, page_icon="📈", layout="wide")
st.title("📈 Nifty Seller Lite")
st.caption(
    "V0.8 Options Intelligence — persistent intraday option flow, OI walls, PCR, "
    "Top-7 weighted contribution and VIX context connected to the existing core market "
    "engine through one authoritative snapshot. No CE/PE/Condor strategy decision yet."
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
state_store = OptionStateStore(Path(CONFIG.option_state_path))

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
    clear_instrument_cache = st.button(
        "Clear instrument cache", use_container_width=True
    )
    clear_option_state = st.button(
        "Clear today's option history", use_container_width=True
    )
    if clear_instrument_cache:
        cache = Path("data/instrument_master.csv")
        if cache.exists():
            cache.unlink()
        st.success("Instrument cache cleared")
    if clear_option_state:
        state_store.clear()
        st.session_state.pop("snapshot", None)
        st.success("Bounded option history cleared")

if not credentials_ready:
    st.code(
        '[dhan]\nclient_id = "YOUR_CLIENT_ID"\naccess_token = "YOUR_24_HOUR_ACCESS_TOKEN"',
        language="toml",
    )
    st.stop()

if "snapshot" not in st.session_state or refresh:
    try:
        with st.spinner(
            "Building one authoritative DhanHQ snapshot, core market evidence and option intelligence..."
        ):
            credentials = Credentials(client_id=client_id, access_token=access_token)
            client = DhanClient(credentials)
            service = SnapshotService(
                client,
                InstrumentMaster(Path("data/instrument_master.csv")),
                state_store,
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

st.subheader("Options Intelligence — Evidence Only")
render_option_intelligence(snapshot)
option_tabs = st.tabs(
    [
        "Premium + OI + Volume Flow",
        "1m / 3m / 5m Movement",
        "OI Walls, Clusters & PCR",
        "Top-7 Weighted Contribution",
        "VIX Context",
    ]
)
with option_tabs[0]:
    render_option_flow_matrix(snapshot)
with option_tabs[1]:
    render_option_windows(snapshot)
with option_tabs[2]:
    render_walls_and_pcr(snapshot)
with option_tabs[3]:
    render_heavyweight_intelligence(snapshot)
with option_tabs[4]:
    render_vix_context(snapshot)

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
    "Next big milestone: V1.0 Final One-Brain Decision — normalized CE Sell, PE Sell, "
    "Iron Condor and WAIT need scores only after live Options Intelligence continuity is verified."
)
