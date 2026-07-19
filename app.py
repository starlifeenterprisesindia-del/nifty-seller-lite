from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from config import CONFIG, IST_TIMEZONE
from models import Credentials
from services.context_store import MarketContextStore
from services.dhan_client import DhanClient
from services.instrument_master import InstrumentMaster
from services.option_state_store import OptionStateStore
from services.snapshot_service import SnapshotService
from ui.components import (
    render_candles,
    render_decision,
    render_core_evidence,
    render_feed_status,
    render_header,
    render_heavyweight_intelligence,
    render_heavyweights,
    render_indicators,
    render_levels,
    render_market_context,
    render_market_session,
    render_option_chain,
    render_option_flow_matrix,
    render_option_intelligence,
    render_option_windows,
    render_price_action,
    render_trade_plan,
    render_vix_context,
    render_volume,
    render_walls_and_pcr,
)


st.set_page_config(page_title=CONFIG.app_name, page_icon="📈", layout="wide")
st.title("📈 Nifty Seller Lite")
st.caption(
    "V1.2 Hedged Strike Planner — the final one-brain decision now maps to protected "
    "CE/PE/Condor strike candidates using the same option-chain snapshot. Read only, "
    "no order placement, and every actionable setup keeps a mandatory hedge."
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
context_store = MarketContextStore(Path(CONFIG.market_context_path))


def optional_number(raw: str) -> float | None:
    value = str(raw or "").strip().replace(",", "")
    if not value:
        return None
    return float(value)


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
    with st.expander("FII/DII & verified event context"):
        context_date = st.date_input(
            "Session date", datetime.now(ZoneInfo(IST_TIMEZONE)).date()
        )
        fii_raw = st.text_input("FII cash net ₹ crore (optional)", value="")
        dii_raw = st.text_input("DII cash net ₹ crore (optional)", value="")
        event_level = st.selectbox(
            "Verified market event risk", ["NONE", "LOW", "MEDIUM", "HIGH"]
        )
        event_verified = st.checkbox("Risk/news personally verified", value=False)
        event_note = st.text_input("Short event note (optional)", value="")
        save_context = st.button("Save market context", use_container_width=True)
        st.caption(
            "Missing FII/DII is kept as missing, never converted to zero. "
            "Medium/high event risk is accepted only when verified."
        )
    if save_context:
        try:
            context_store.upsert(
                session_date=context_date,
                fii_cash_net=optional_number(fii_raw),
                dii_cash_net=optional_number(dii_raw),
                event_risk=event_level,
                event_note=event_note,
                verified=event_verified,
            )
            st.session_state.pop("snapshot", None)
            st.success("Market context saved")
        except Exception as exc:
            st.error(f"Context not saved: {exc}")
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
                context_store,
            )
            st.session_state.snapshot = service.build()
    except Exception as exc:
        st.error(f"Snapshot failed safely: {exc}")
        st.stop()

snapshot = st.session_state.snapshot
render_market_session(snapshot)
render_header(snapshot)

render_decision(snapshot)
render_trade_plan(snapshot)

st.subheader("Core Market Evidence")
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
        "FII/DII & Event Risk",
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
with option_tabs[5]:
    render_market_context(snapshot)

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
    "Decision-support only. Strategy scores are independent suitability percentages; "
    "WAIT is a separate uncertainty/risk need. Verify broker prices, spreads, margin, "
    "liquidity and hedge before any trade. The app never places orders."
)
