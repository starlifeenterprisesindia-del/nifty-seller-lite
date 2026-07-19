from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from analysis.position_guardian import create_trade_record
from config import CONFIG, IST_TIMEZONE
from models import Credentials, RiskProfile
from services.context_store import MarketContextStore
from services.dhan_client import DhanClient
from services.discipline_store import DisciplineStore
from services.instrument_master import InstrumentMaster
from services.option_state_store import OptionStateStore
from services.pdf_report import audit_pdf_filename, build_full_audit_pdf
from services.snapshot_service import SnapshotService
from ui.components import (
    render_candles,
    render_decision,
    render_evidence_matrix,
    render_execution_guard,
    render_core_evidence,
    render_feed_status,
    render_header,
    render_heavyweight_intelligence,
    render_heavyweights,
    render_indicators,
    render_levels,
    render_market_context,
    render_market_outlook,
    render_market_session,
    render_option_chain,
    render_option_flow_matrix,
    render_option_intelligence,
    render_option_windows,
    render_position_guardian,
    render_price_action,
    render_trade_plan,
    render_vix_context,
    render_volume,
    render_walls_and_pcr,
)


st.set_page_config(page_title=CONFIG.app_name, page_icon="📈", layout="wide")
st.title("📈 Nifty Seller Lite")
st.caption(
    "V2.6 Market Memory + Full Live Audit PDF — one canonical strategy brain uses "
    "bounded same-session signal memory, anti-flip confirmation, a conditional 5–15 "
    "minute outlook and an immutable PDF audit of the same snapshot. Read only; no order placement."
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
discipline_store = DisciplineStore(Path(CONFIG.discipline_state_path))


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
    refresh = st.button("Fetch Fresh Snapshot", type="primary", width="stretch")
    clear_instrument_cache = st.button("Clear instrument cache", width="stretch")
    clear_option_state = st.button("Clear today's option history", width="stretch")
    with st.expander("Risk & one-trade discipline", expanded=True):
        capital_rupees = st.number_input(
            "Trading capital ₹",
            min_value=10000.0,
            max_value=100000000.0,
            value=float(CONFIG.risk_default_capital),
            step=10000.0,
        )
        risk_pct = st.number_input(
            "Maximum trade risk %",
            min_value=0.1,
            max_value=2.0,
            value=float(CONFIG.risk_default_pct),
            step=0.1,
        )
        lot_size = st.number_input(
            "Current NIFTY lot size",
            min_value=1,
            max_value=500,
            value=int(CONFIG.risk_default_lot_size),
            step=1,
        )
        max_lots_cap = st.number_input(
            "Maximum lots cap",
            min_value=1,
            max_value=20,
            value=int(CONFIG.risk_default_max_lots),
            step=1,
        )
        target_capture_pct = st.number_input(
            "Target credit capture %",
            min_value=5.0,
            max_value=90.0,
            value=float(CONFIG.risk_default_target_capture_pct),
            step=5.0,
        )
        stop_loss_pct = st.number_input(
            "Spread loss trigger % of credit",
            min_value=10.0,
            max_value=200.0,
            value=float(CONFIG.risk_default_stop_loss_pct),
            step=5.0,
        )
        entry_start = st.time_input(
            "Entry window starts", value=CONFIG.risk_default_entry_start
        )
        entry_end = st.time_input(
            "No new entry after", value=CONFIG.risk_default_entry_end
        )
        forced_exit = st.time_input(
            "Compulsory exit by", value=CONFIG.risk_default_forced_exit
        )
        st.caption(
            "Defaults enforce one trade/day, one-lot cap and a conservative 0.5% "
            "risk budget. Lot size remains editable because exchange contracts can change."
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
        save_context = st.button("Save market context", width="stretch")
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

risk_profile = RiskProfile(
    capital_rupees=float(capital_rupees),
    risk_pct=float(risk_pct),
    lot_size=int(lot_size),
    max_lots_cap=int(max_lots_cap),
    target_capture_pct=float(target_capture_pct),
    stop_loss_pct=float(stop_loss_pct),
    entry_start=entry_start,
    entry_end=entry_end,
    forced_exit=forced_exit,
)
if not (risk_profile.entry_start <= risk_profile.entry_end < risk_profile.forced_exit):
    st.error("Risk times must follow: entry start ≤ entry end < compulsory exit.")
    st.stop()
profile_signature = (
    risk_profile.capital_rupees,
    risk_profile.risk_pct,
    risk_profile.lot_size,
    risk_profile.max_lots_cap,
    risk_profile.target_capture_pct,
    risk_profile.stop_loss_pct,
    risk_profile.entry_start.isoformat(),
    risk_profile.entry_end.isoformat(),
    risk_profile.forced_exit.isoformat(),
)
if st.session_state.get("risk_profile_signature") != profile_signature:
    st.session_state.risk_profile_signature = profile_signature
    st.session_state.pop("snapshot", None)

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
                discipline_store,
            )
            st.session_state.snapshot = service.build(risk_profile=risk_profile)
    except Exception as exc:
        st.error(f"Snapshot failed safely: {exc}")
        st.stop()

snapshot = st.session_state.snapshot
render_market_session(snapshot)
render_header(snapshot)

render_evidence_matrix(snapshot)
render_decision(snapshot)
render_market_outlook(snapshot)

st.subheader("Full Live Audit PDF")
st.caption(
    "The PDF freezes this exact authoritative snapshot for 5-minute and 15-minute live "
    "verification. It does not fetch data or recalculate the Final One-Brain Decision."
)
pdf_snapshot_key = st.session_state.get("audit_pdf_snapshot_id")
if pdf_snapshot_key != snapshot.snapshot_id:
    st.session_state.pop("audit_pdf_bytes", None)
    st.session_state.audit_pdf_snapshot_id = snapshot.snapshot_id

pdf_left, pdf_right = st.columns([1, 2])
with pdf_left:
    generate_pdf = st.button(
        "Generate Full Live Audit PDF",
        type="primary",
        width="stretch",
    )
if generate_pdf:
    try:
        with st.spinner("Building audit PDF from the current snapshot only..."):
            st.session_state.audit_pdf_bytes = build_full_audit_pdf(snapshot)
        st.success("Audit PDF generated for this snapshot")
    except Exception as exc:
        st.error(f"Audit PDF not generated: {exc}")
with pdf_right:
    if st.session_state.get("audit_pdf_bytes"):
        st.download_button(
            "Download Full Live Audit PDF",
            data=st.session_state.audit_pdf_bytes,
            file_name=audit_pdf_filename(snapshot),
            mime="application/pdf",
            width="stretch",
        )
    else:
        st.info("Generate the PDF after every important live-market checkpoint.")

execution_expanded = (
    snapshot.market_session.is_live and snapshot.decision.final_action != "WAIT"
)
with st.expander(
    "Protected Strike Planner, Execution Guard & Position Guardian",
    expanded=execution_expanded,
):
    render_trade_plan(snapshot)
    render_execution_guard(snapshot)
    render_position_guardian(snapshot)

    st.write("**Manual one-trade journal**")
    maximum_mark_lots = max(1, snapshot.execution_guard.allowed_lots)
    planned_lots = st.number_input(
        "Lots to record when trade is taken",
        min_value=1,
        max_value=maximum_mark_lots,
        value=1,
        step=1,
        disabled=snapshot.execution_guard.readiness != "ENTRY READY",
    )
    trade_col, target_col, sl_col = st.columns(3)
    with trade_col:
        mark_trade = st.button(
            "Mark current trade taken",
            disabled=(
                snapshot.execution_guard.readiness != "ENTRY READY"
                or snapshot.discipline_state.trades_taken >= 1
            ),
            width="stretch",
        )
    with target_col:
        mark_target = st.button(
            "Target / manual exit — lock day",
            disabled=(
                snapshot.discipline_state.trades_taken < 1
                or snapshot.discipline_state.last_outcome != "OPEN"
            ),
            width="stretch",
        )
    with sl_col:
        mark_sl = st.button(
            "SL hit — lock day",
            disabled=(
                snapshot.discipline_state.trades_taken < 1
                or snapshot.discipline_state.last_outcome != "OPEN"
            ),
            width="stretch",
        )
    try:
        if mark_trade:
            trade_record = create_trade_record(
                captured_at=snapshot.created_at,
                decision=snapshot.decision,
                trade_plan=snapshot.trade_plan,
                execution_guard=snapshot.execution_guard,
                lots=int(planned_lots),
                lot_size=snapshot.risk_profile.lot_size,
                spot=snapshot.nifty_quote.get("last_price"),
            )
            discipline_store.mark_trade(
                session_date=snapshot.created_at.date(),
                action=snapshot.decision.final_action,
                trade_record=trade_record,
            )
            st.session_state.pop("snapshot", None)
            st.rerun()
        if mark_target:
            discipline_store.mark_outcome(
                session_date=snapshot.created_at.date(),
                outcome="TARGET / MANUAL EXIT",
                exit_debit_points=snapshot.position_guardian.current_debit_points,
                realized_pnl_rupees=snapshot.position_guardian.unrealized_pnl_rupees,
                captured_at=snapshot.created_at,
            )
            st.session_state.pop("snapshot", None)
            st.rerun()
        if mark_sl:
            discipline_store.mark_outcome(
                session_date=snapshot.created_at.date(),
                outcome="SL HIT",
                exit_debit_points=snapshot.position_guardian.current_debit_points,
                realized_pnl_rupees=snapshot.position_guardian.unrealized_pnl_rupees,
                captured_at=snapshot.created_at,
            )
            st.session_state.pop("snapshot", None)
            st.rerun()
    except Exception as exc:
        st.error(f"Discipline journal not updated: {exc}")
    st.caption(
        "The journal is local to the current Streamlit deployment filesystem. It freezes "
        "the manually marked protected setup for monitoring, but never places, modifies or "
        "exits a broker order."
    )

with st.expander("Detailed Core Market Evidence", expanded=False):
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

with st.expander("Detailed Options Intelligence", expanded=False):
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

with st.expander("Raw Market Data & Snapshot JSON", expanded=False):
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
