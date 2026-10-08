from __future__ import annotations

import os
import sys
import time
import gc
import threading
from dataclasses import replace
from contextlib import contextmanager
from html import escape
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Compact GitHub package: pure-Python analysis/services/ui modules live in one zip.
_RUNTIME_BUNDLE = Path(__file__).with_name("nsl_runtime_v269.zip")
if _RUNTIME_BUNDLE.exists() and str(_RUNTIME_BUNDLE) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_BUNDLE))

import streamlit as st

from analysis.candles import aggregate_candles
from analysis.patterns import build_candle_pattern_library
from analysis.presentation_safety import (
    install_runtime_presentation_patches,
    prepare_snapshot_for_presentation,
)
from config import CONFIG, IST_TIMEZONE
from models import Credentials, RiskProfile
from services.context_store import MarketContextStore
from services.dhan_client import DhanClient
from services.discipline_store import DisciplineStore
from services.instrument_master import InstrumentMaster
from services.github_journal import GitHubJsonJournal
from services.option_state_store import OptionStateStore
from services.news_service import MarketNewsService
from services.shadow_journal import ShadowJournalStore, process_auto_shadow_journal
from services.housekeeping import run_housekeeping
from services.pdf_report import (
    audit_pdf_filename,
    build_full_audit_pdf,
    build_quick_market_pdf,
    build_support_bundle,
    quick_pdf_filename,
    support_bundle_filename,
)
from services.snapshot_service import SnapshotService
from services.live_monitor import (
    FastQuote,
    calculate_live_impulse,
    calculate_live_impulse_from_changes,
    fetch_fast_quotes,
    monitor_timestamp,
)
from services.railway_live_client import RailwayDhanClient, fetch_railway_live_state, fetch_railway_health
from ui.day_memory import (
    render_day_memory,
    render_evidence_download,
    sync_day_memory,
    record_final_day_memory,
    evidence_async_status,
)
from ui.components import (
    render_candles,
    render_evidence_matrix,
    render_core_evidence,
    render_heavyweight_intelligence,
    render_heavyweights,
    render_indicators,
    render_levels,
    render_market_context,
    render_market_session,
    render_news_context,
    render_main_ai_market_view,
    render_data_health,
    render_detailed_evidence,
    render_option_chain,
    render_option_flow_matrix,
    render_option_intelligence,
    render_options_live_board,
    render_option_windows,
    render_price_action,
    render_vix_context,
    render_volume,
    render_walls_and_pcr,
    render_protected_candidates,
    render_compact_status_bar,
)
from ui.premium_calculator import render_spot_premium_calculator
from ui.alerts import render_market_alerts
from ui.shadow_journal import (
    render_auto_shadow_journal,
    render_shadow_journal_download,
    render_shadow_journal_status,
)
from ui.pattern_alerts import render_pattern_alerts, process_combined_signal_alerts
from ui.timeframe_outlook import render_timeframe_outlook
from ui.ai_move_tracker import render_ai_move_tracker
from ui.rsi_reversal_setup import render_rsi_reversal_setup
from ui.presentation_helpers import public_mode_enabled
from ui.performance_diagnostics import render_performance_diagnostics
from ui.help_guide import render_help_guide
from ui.live_barrier_chart import render_live_barrier_chart
from ui.advanced_options_intelligence import render_phase2_options_intelligence
from ui.replay_review import render_phase3_replay
from ui.strategy_lab import render_phase4_strategy_lab, render_phase7_strategy_repair
from ui.validation_lab import render_phase5_validation_lab
from ui.market_intelligence import render_market_intelligence, render_move_radar, process_market_intelligence_alerts
from services.market_intelligence_export import build_market_intelligence_test_pack


_PROCESS_PERSIST_CONTROLS = {
    "auto_snapshot_enabled",
    "auto_snapshot_duration_minutes",
    "auto_snapshot_interval_seconds",
    "fast_monitor_enabled",
    "auto_shadow_journal_enabled",
    "combined_signal_alerts_enabled",
    "market_intelligence_alerts_enabled",
    "market_alert_sound_enabled",
}


def _remember_runtime_control(key: str, value: object) -> None:
    """Persist widget state safely across reruns; process-cache only global controls.

    Streamlit deletes widget-backed Session State when a widget is skipped during a
    rerun. The durable Session State key prevents panel collapse. Only operational
    controls are additionally copied to the process handoff; visual panel-open state
    remains per browser session so future viewer sessions cannot affect each other.
    """
    st.session_state[key] = value
    if key not in _PROCESS_PERSIST_CONTROLS:
        return
    handoff = globals().get("_RUNTIME_HANDOFF")
    if isinstance(handoff, dict):
        controls = handoff.setdefault("controls", {})
        if isinstance(controls, dict):
            controls[key] = value


def _persistent_toggle(label: str, key: str, *, default: bool = False, **kwargs) -> bool:
    """Toggle whose durable value survives runs in which the widget is not rendered."""
    widget_key = f"__widget__{key}"
    if key not in st.session_state:
        st.session_state[key] = bool(default)
    if widget_key not in st.session_state:
        st.session_state[widget_key] = bool(st.session_state.get(key, default))
    value = bool(st.toggle(label, key=widget_key, **kwargs))
    _remember_runtime_control(key, value)
    return value


def _persistent_selectbox(label: str, options, key: str, *, default, **kwargs):
    """Selectbox backed by a non-widget key so refresh reruns cannot reset it."""
    values = tuple(options)
    current = st.session_state.get(key, default)
    if current not in values:
        current = default
    widget_key = f"__widget__{key}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = current
    value = st.selectbox(label, values, key=widget_key, **kwargs)
    _remember_runtime_control(key, value)
    return value


@contextmanager
def persistent_panel(label: str, key: str):
    """Panel state that survives early/full reruns and temporary widget hiding."""
    is_open = _persistent_toggle(label, key, default=False)
    if is_open:
        with st.container(border=True):
            yield True
    else:
        yield False


def _fmt_compact_oi(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "OI —"
    if abs(number) >= 10_000_000:
        return f"OI {number / 10_000_000:.2f}Cr"
    if abs(number) >= 100_000:
        return f"OI {number / 100_000:.1f}L"
    if abs(number) >= 1_000:
        return f"OI {number / 1_000:.1f}K"
    return f"OI {number:.0f}"


def render_market_pulse_strip(snapshot) -> None:
    """Compact Big Player, Heavy Money and Option Flow cards for the main screen."""

    activity = getattr(snapshot, "big_player_activity", None)
    if activity is None:
        bp_value, bp_note = "UNAVAILABLE", "Fresh participation ka wait"
    else:
        direction = str(getattr(activity, "direction", "MIXED") or "MIXED")
        score = float(getattr(activity, "score", 0.0) or 0.0)
        confirm = int(getattr(activity, "confirmation_count", 0) or 0)
        total = int(getattr(activity, "confirmation_total", 0) or 0)
        bp_value = f"{direction} {score:.0f}/100"
        bp_note = f"Confirm {confirm}/{total} · {str(getattr(activity, 'state', '') or 'NORMAL')}"

    options = getattr(snapshot, "option_intelligence", None)
    ce_wall = getattr(options, "ce_wall", None) if options is not None else None
    pe_wall = getattr(options, "pe_wall", None) if options is not None else None
    if ce_wall is None and pe_wall is None:
        money_value, money_note = "UNAVAILABLE", "OI walls ka wait"
    else:
        ce_strike = getattr(ce_wall, "strike", None)
        pe_strike = getattr(pe_wall, "strike", None)
        ce_text = f"CE {float(ce_strike):,.0f}" if ce_strike is not None else "CE —"
        pe_text = f"PE {float(pe_strike):,.0f}" if pe_strike is not None else "PE —"
        money_value = f"{ce_text} · {pe_text}"
        notes = []
        if ce_wall is not None:
            notes.append(_fmt_compact_oi(getattr(ce_wall, "oi", None)))
        if pe_wall is not None:
            notes.append(_fmt_compact_oi(getattr(pe_wall, "oi", None)))
        money_note = " / ".join(notes) if notes else "Heavy Money / OI"

    if options is None:
        flow_value, flow_note = "UNAVAILABLE", "Option flow ka wait"
    else:
        flow_value = str(getattr(options, "market_bias", "") or "MIXED")
        confidence = float(getattr(options, "confidence", 0.0) or 0.0)
        persistence = str(getattr(options, "persistence", "") or "WARMING UP")
        flow_note = f"Conf {confidence:.0f}/100 · {persistence}"

    def card(css_name: str, label: str, value: str, note: str) -> str:
        return (
            f'<div class="pulse-card {css_name}">'
            f'<div class="pulse-label">{escape(label)}</div>'
            f'<div class="pulse-value">{escape(value)}</div>'
            f'<div class="pulse-note">{escape(note)}</div></div>'
        )

    html = (
        '<style>'
        '.pulse-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:7px 0 12px}'
        '.pulse-card{padding:10px 12px;border-radius:13px;border:1px solid rgba(127,127,127,.22);background:rgba(127,127,127,.04);min-width:0}'
        '.pulse-label{font-size:.72rem;font-weight:850;letter-spacing:.045em;opacity:.72;text-transform:uppercase}'
        '.pulse-value{font-size:1.02rem;font-weight:900;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
        '.pulse-note{font-size:.72rem;opacity:.70;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
        '.pulse-card.bp{border-color:rgba(59,130,246,.38);background:rgba(59,130,246,.07)}'
        '.pulse-card.money{border-color:rgba(245,158,11,.38);background:rgba(245,158,11,.07)}'
        '.pulse-card.flow{border-color:rgba(34,197,94,.38);background:rgba(34,197,94,.07)}'
        '@media(max-width:760px){.pulse-grid{grid-template-columns:1fr}.pulse-card{padding:9px 10px}}'
        '</style><div class="pulse-grid">'
        + card("bp", "Big Player", bp_value, bp_note)
        + card("money", "Heavy Money / OI", money_value, money_note)
        + card("flow", "Option Flow", flow_value, flow_note)
        + '</div>'
    )
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)



def render_candle_pattern_library_shadow(snapshot) -> None:
    """Render the zero-vote candle library recorded in snapshot metadata."""
    st.caption(
        "🕯️ SHADOW / DISPLAY ONLY · One Brain weight 0 · Extra API calls 0 · "
        "On-demand + cached; panel band ho to core pipeline par zero work. "
        "Live validation ke baad hi kisi pattern ko score/decision me weight milega."
    )
    one = getattr(snapshot, "candles_1m", None)
    three = getattr(snapshot, "candles_3m", None)
    fifteen = getattr(snapshot, "candles_15m", None)
    if one is None or three is None or fifteen is None:
        st.info("Candle data unavailable.")
        return

    def _last_stamp(frame) -> str:
        try:
            return str(frame.iloc[-1]["timestamp"]) if frame is not None and not frame.empty else ""
        except Exception:
            return ""

    cache_key = (_last_stamp(one), _last_stamp(three), _last_stamp(fifteen), len(one), len(three), len(fifteen))
    if st.session_state.get("candle_library_cache_key") == cache_key:
        library = st.session_state.get("candle_library_cache") or {}
    else:
        five = aggregate_candles(one.drop(columns=["is_complete"], errors="ignore"), 5)
        library = build_candle_pattern_library(
            one, three, candles_5m=five, candles_15m=fifteen
        )
        st.session_state["candle_library_cache_key"] = cache_key
        st.session_state["candle_library_cache"] = library

    current = library.get("current") or {}
    cols = st.columns(3)
    for col, timeframe in zip(cols, ("3M", "5M", "15M")):
        items = list(current.get(timeframe) or [])
        if not items:
            col.metric(f"{timeframe} Pattern", "NONE")
            col.caption("Latest completed candle par qualified shadow pattern nahi.")
            continue
        top = max(items, key=lambda item: float(item.get("quality") or 0.0))
        col.metric(
            f"{timeframe} Pattern",
            str(top.get("name") or "PATTERN"),
            f"{str(top.get('direction') or 'NEUTRAL')} · Q {float(top.get('quality') or 0):.0f}/100",
        )
        note = str(top.get("note") or "")
        context = str(top.get("context") or "")
        if note or context:
            col.caption(" · ".join(part for part in (context, note) if part))

    recent = list(library.get("recent") or [])
    if recent:
        rows = []
        for item in recent:
            outcomes = item.get("outcomes") or {}
            detected = str(item.get("detected_at") or "")
            # Keep the table compact for laptop/mobile review.
            rows.append(
                {
                    "Time": detected[11:16] if len(detected) >= 16 else detected,
                    "TF": item.get("timeframe") or "",
                    "Pattern": item.get("name") or "",
                    "Dir": item.get("direction") or "",
                    "Quality": f"{float(item.get('quality') or 0):.0f}/100",
                    "Context": item.get("context") or "",
                    "+5m": outcomes.get("5m", "PENDING"),
                    "+15m": outcomes.get("15m", "PENDING"),
                    "+30m": outcomes.get("30m", "PENDING"),
                }
            )
        st.markdown("**Recent shadow detections + follow-through**")
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Current session me abhi koi library pattern record nahi hua.")

    with st.expander("Supported candle patterns", expanded=False):
        st.write(" · ".join(str(x) for x in (library.get("patterns_supported") or ())))
        st.caption(
            "Social-media pattern names ko textbook truth nahi maana gaya. "
            "3-bull + lower-wick setup ko geometry-based continuation setup ke roop me track kiya gaya hai."
        )

def render_compact_strategy_summary(snapshot) -> None:
    """Show one protected candidate summary without the full ranking table."""

    common = (getattr(snapshot, "metadata", {}) or {}).get("common_decision") or {}
    strategy = str(common.get("best_strategy") or "WAIT")
    status = (
        "ENTRY READY"
        if bool(common.get("entry_allowed"))
        else "REFERENCE ONLY"
        if not getattr(snapshot.market_session, "is_live", False)
        else str(common.get("entry_state") or "WAIT")
    )
    quality = common.get("plan_quality")
    risk = common.get("risk_per_lot_rupees")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Best Protected Setup", strategy)
    c2.metric("Status", status)
    c3.metric("Plan Quality", f"{float(quality):.0f}/100" if quality is not None else "—")
    c4.metric("Risk / lot", f"₹{float(risk):,.0f}" if risk is not None else "—")
    instruction = str(common.get("instruction") or "").strip()
    trigger = str(common.get("trigger") or "").strip()
    note = " · ".join(part for part in (instruction, f"Next: {trigger}" if trigger else "") if part)
    if note:
        st.caption("🛡️ " + note)


# Backward-compatible compact-level renderer. Older deployed ui/components.py files
# do not contain render_compact_barrier_map; do not let that single optional view
# helper crash the whole app during a partial GitHub upload.
try:
    from ui.components import render_compact_barrier_map
except ImportError:
    def render_compact_barrier_map(snapshot, previous_snapshot=None) -> None:
        item = getattr(snapshot, "barrier_map", None)
        if item is None:
            return

        def _level_value(level, fallback: str) -> tuple[str, str]:
            if level is None:
                return "—", fallback
            lower = getattr(level, "lower", None)
            upper = getattr(level, "upper", None)
            if lower is None or upper is None:
                value = "—"
            else:
                value = f"{float(lower):,.0f}–{float(upper):,.0f}"
            distance = getattr(level, "distance_points", None)
            strength = getattr(level, "strength", None)
            note_parts = []
            if distance is not None:
                note_parts.append(f"{float(distance):,.0f} pts")
            if strength is not None:
                note_parts.append(f"Bachne ki taakat {float(strength):.0f}")
            return value, " · ".join(note_parts) or fallback

        resistance, resistance_note = _level_value(
            getattr(item, "nearest_resistance", None), "Resistance unavailable"
        )
        support, support_note = _level_value(
            getattr(item, "nearest_support", None), "Support unavailable"
        )
        price = getattr(item, "current_price", None)
        spot = f"{float(price):,.2f}" if price is not None else "—"

        st.subheader("🧭 Nearest Levels")
        col1, col2, col3 = st.columns(3)
        col1.metric("Next Resistance", resistance, resistance_note)
        col2.metric("NIFTY Current", spot)
        col3.metric("Next Support", support, support_note)


install_runtime_presentation_patches()

st.set_page_config(page_title=CONFIG.app_name, page_icon="📈", layout="wide")


@st.cache_resource(show_spinner=False)
def _process_runtime_handoff() -> dict[str, object]:
    """Keep the last successful snapshot across ordinary Streamlit code reruns.

    Community Cloud usually hot-reloads Python edits without replacing the whole
    process.  A process-level handoff lets a new session render immediately from the
    last authoritative snapshot instead of rebuilding the full Railway/Dhan pipeline
    just because UI code changed.  A real process replacement simply starts empty.
    """
    return {"snapshot": None, "previous_snapshot": None, "controls": {}, "app_version": None}


_RUNTIME_HANDOFF = _process_runtime_handoff()
_runtime_controls = _RUNTIME_HANDOFF.get("controls")
if isinstance(_runtime_controls, dict):
    for _control_key, _control_value in _runtime_controls.items():
        if _control_key not in st.session_state:
            st.session_state[_control_key] = _control_value
if "snapshot" not in st.session_state and _RUNTIME_HANDOFF.get("snapshot") is not None:
    st.session_state.snapshot = _RUNTIME_HANDOFF["snapshot"]
    if _RUNTIME_HANDOFF.get("previous_snapshot") is not None:
        st.session_state.previous_snapshot = _RUNTIME_HANDOFF["previous_snapshot"]
    st.session_state["snapshot_restored_from_process_cache"] = True

# Deployment safety without reconnect regression: only a real process/version
# transition resets Auto Snapshot. A new browser websocket/session on the SAME app
# version must inherit the operational control instead of silently switching it OFF.
_process_version_changed = _RUNTIME_HANDOFF.get("app_version") != CONFIG.version
if _process_version_changed:
    _RUNTIME_HANDOFF["app_version"] = CONFIG.version
    _remember_runtime_control("auto_snapshot_enabled", False)
    for _key in (
        "__widget__auto_snapshot_enabled",
        "auto_snapshot_due",
        "auto_snapshot_reserved_at",
        "auto_snapshot_started_at",
        "auto_snapshot_next_due_at",
        "auto_snapshot_priority_requested_at",
    ):
        st.session_state.pop(_key, None)
st.session_state["_loaded_app_version"] = CONFIG.version
st.markdown(
    """
    <style>
    .block-container{padding-top:1.25rem;padding-bottom:2rem;max-width:1600px}
    h1{font-size:2.35rem!important;margin-bottom:.15rem!important}
    h2{margin-top:1rem!important}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.65rem}
    @media(max-width:760px){.block-container{padding:.8rem .55rem 1.5rem}h1{font-size:1.75rem!important}.stMetric{padding:.25rem!important}[data-testid="stDataFrame"]{font-size:.82rem}}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("📈 Nifty Seller Lite")
st.caption(
    "Hinglish Current + Future Brain decision workspace"
)


def secret_value(name: str) -> str:
    try:
        if "dhan" in st.secrets and name in st.secrets["dhan"]:
            return str(st.secrets["dhan"][name])
    except Exception:
        pass
    return os.getenv(f"DHAN_{name.upper()}", "")


def live_server_value(name: str) -> str:
    try:
        if "live_server" in st.secrets and name in st.secrets["live_server"]:
            return str(st.secrets["live_server"][name])
    except Exception:
        pass
    return os.getenv(f"LIVE_SERVER_{name.upper()}", "")


def cloud_journal_values() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        if "fii_dii_cloud" in st.secrets:
            section = st.secrets["fii_dii_cloud"]
            for key in ("owner", "repo", "token", "path", "branch"):
                if key in section:
                    values[key] = str(section[key])
    except Exception:
        pass
    env_map = {
        "owner": "NSL_FII_DII_GITHUB_OWNER",
        "repo": "NSL_FII_DII_GITHUB_REPO",
        "token": "NSL_FII_DII_GITHUB_TOKEN",
        "path": "NSL_FII_DII_GITHUB_PATH",
        "branch": "NSL_FII_DII_GITHUB_BRANCH",
    }
    for key, env_name in env_map.items():
        if not values.get(key) and os.getenv(env_name):
            values[key] = str(os.getenv(env_name))
    return values


client_id = secret_value("client_id")
access_token = secret_value("access_token")
live_server_url = live_server_value("url")
live_server_api_key = live_server_value("api_key")

# Housekeeping is intentionally throttled. A Streamlit UI interaction or fast fragment
# must not rescan the data directory every few seconds. It still prunes only temporary/
# raw market state; FII/DII journal, manual discipline/trade state and learning stay.
_housekeeping_now = time.time()
if _housekeeping_now - float(st.session_state.get("last_housekeeping_ts", 0.0)) >= 1800:
    run_housekeeping(datetime.now(ZoneInfo(IST_TIMEZONE)))
    st.session_state.last_housekeeping_ts = _housekeeping_now
state_store = OptionStateStore(Path(CONFIG.option_state_path))
cloud_journal = GitHubJsonJournal.from_mapping(
    cloud_journal_values(), timeout_seconds=CONFIG.market_context_cloud_timeout_seconds
)
shadow_cloud_values = cloud_journal_values()
shadow_cloud_values["path"] = "shadow_journal.json"
shadow_cloud_journal = GitHubJsonJournal.from_mapping(
    shadow_cloud_values, timeout_seconds=CONFIG.market_context_cloud_timeout_seconds
)
context_store = MarketContextStore(
    Path(CONFIG.market_context_path),
    Path(CONFIG.market_context_mirror_path),
    Path(CONFIG.market_context_rescue_path),
    cloud_backend=cloud_journal,
)
discipline_store = DisciplineStore(Path(CONFIG.discipline_state_path))
shadow_journal_store = ShadowJournalStore(
    Path(CONFIG.shadow_journal_path), cloud_backend=shadow_cloud_journal
)
if not st.session_state.get("shadow_journal_local_loaded", False):
    # Cold-start rule: never block first paint on GitHub/network history.  Local
    # journal is enough to render; cloud merge happens in a daemon after the UI
    # process is alive.
    st.session_state.shadow_entries_cache = shadow_journal_store.load(refresh_cloud=False)
    st.session_state["shadow_journal_local_loaded"] = True

if not st.session_state.get("shadow_journal_cloud_sync_started", False):
    def _refresh_shadow_journal_cloud() -> None:
        try:
            shadow_journal_store.load(refresh_cloud=True)
        except Exception:
            # Cloud history is advisory/recovery only; startup must remain usable.
            return

    threading.Thread(
        target=_refresh_shadow_journal_cloud,
        daemon=True,
        name="shadow-journal-cloud-refresh",
    ).start()
    st.session_state["shadow_journal_cloud_sync_started"] = True
news_service = MarketNewsService(Path(CONFIG.news_cache_path))


@st.cache_resource
def _instrument_master_resource() -> InstrumentMaster:
    return InstrumentMaster(Path("data/instrument_master.csv"))


instrument_master = _instrument_master_resource()
# Hide the large Dhan instrument-master download behind normal app/backend warm-up.
# It is process-wide, atomic, and never enters the One-Brain scoring path.
instrument_master.prewarm_async()


def optional_number(raw: str) -> float | None:
    value = str(raw or "").strip().replace(",", "")
    if not value:
        return None
    return float(value)


risk_profile = RiskProfile(
    capital_rupees=float(CONFIG.risk_default_capital),
    risk_pct=float(CONFIG.risk_default_pct),
    lot_size=int(CONFIG.risk_default_lot_size),
    max_lots_cap=int(CONFIG.risk_default_max_lots),
    target_capture_pct=float(CONFIG.risk_default_target_capture_pct),
    stop_loss_pct=float(CONFIG.risk_default_stop_loss_pct),
    entry_start=CONFIG.risk_default_entry_start,
    entry_end=CONFIG.risk_default_entry_end,
    forced_exit=CONFIG.risk_default_forced_exit,
)


def _market_clock_expected_live() -> bool:
    """Clock-side live-session expectation, independent of the last snapshot.

    A previous CLOSED snapshot must not keep Auto Snapshot paused forever when the
    next trading session opens. This helper intentionally makes no broker/API call.
    """
    now_ist = datetime.now(ZoneInfo(IST_TIMEZONE))
    if now_ist.weekday() >= 5:
        return False
    return CONFIG.market_open <= now_ist.time() < CONFIG.market_close


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    value = max(0.0, float(seconds))
    if value < 60:
        return f"{value:.0f}s"
    minutes, secs = divmod(int(value), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _build_authoritative_snapshot_once() -> tuple[object | None, float, Exception | None]:
    """Build one full snapshot without forcing the rest of the page to rerender.

    Auto refresh calls this from a fragment. The existing page therefore stays on
    screen while Dhan/Railway processing runs; after success a short full rerun only
    repaints already-built state.
    """
    if st.session_state.get("snapshot_build_inflight", False):
        return None, 0.0, RuntimeError("snapshot build already in progress")
    st.session_state.snapshot_build_inflight = True
    started_wall = time.time()
    try:
        if railway_ready:
            client = RailwayDhanClient(
                live_server_url,
                live_server_api_key,
                timeout_seconds=max(8.0, CONFIG.request_timeout_seconds),
            )
        else:
            credentials = Credentials(client_id=client_id, access_token=access_token)
            client = DhanClient(credentials)
        service = SnapshotService(
            client,
            instrument_master,
            state_store,
            context_store,
            discipline_store,
            news_service=news_service,
        )
        previous_snapshot = st.session_state.get("snapshot")
        new_snapshot = service.build(risk_profile=risk_profile)
        new_snapshot.metadata["instrument_master_prewarm"] = instrument_master.prewarm_status()
        if (
            previous_snapshot is not None
            and previous_snapshot.snapshot_id != new_snapshot.snapshot_id
            and previous_snapshot.created_at.date() == new_snapshot.created_at.date()
        ):
            st.session_state.previous_snapshot = previous_snapshot
        st.session_state.snapshot = new_snapshot
        completed_at = time.time()
        elapsed = max(0.0, completed_at - started_wall)
        st.session_state.last_snapshot_started_ts = started_wall
        st.session_state.last_snapshot_fetch_ts = completed_at
        st.session_state.last_snapshot_completed_ts = completed_at
        st.session_state.last_snapshot_build_seconds = round(elapsed, 3)
        st.session_state.last_snapshot_refresh_error = ""
        st.session_state.last_snapshot_refresh_ok_at = completed_at
        _RUNTIME_HANDOFF["snapshot"] = new_snapshot
        _RUNTIME_HANDOFF["previous_snapshot"] = st.session_state.get("previous_snapshot")
        return new_snapshot, elapsed, None
    except Exception as exc:
        st.session_state.last_snapshot_refresh_error = f"{type(exc).__name__}: {exc}"
        return None, max(0.0, time.time() - started_wall), exc
    finally:
        st.session_state.snapshot_build_inflight = False


with st.sidebar:
    st.subheader("Connection")
    st.write(f"Version: `{CONFIG.version}`")
    railway_ready = bool(live_server_url and live_server_api_key)
    credentials_ready = railway_ready or bool(client_id and access_token)
    if railway_ready:
        st.success("Railway single-source Dhan gateway ready")
    elif credentials_ready:
        st.warning("Legacy direct Dhan mode — Railway recommended")
    else:
        st.error("Dhan credentials missing")
    shadow_journal_enabled = _persistent_toggle(
        "Auto Shadow Journal ON",
        "auto_shadow_journal_enabled",
        default=True,
        help="Maximum 5 paper trades/day; no broker orders.",
    )

    # Snapshot controls use durable state keys. Auto refresh is built inside a
    # fragment, so the main workspace remains mounted while processing happens.
    with st.expander("⏱️ Auto Snapshot", expanded=False):
        auto_enabled = _persistent_toggle(
            "Auto Snapshot ON",
            "auto_snapshot_enabled",
            default=False,
        )
        duration_minutes = _persistent_selectbox(
            "Kitni der chale",
            (0, 15, 30, 60),
            "auto_snapshot_duration_minutes",
            default=0,
            format_func=lambda value: (
                "Manual OFF tak (recommended)" if value == 0
                else "1 hour" if value == 60
                else f"{value} minute"
            ),
            disabled=not auto_enabled,
        )
        interval_seconds = _persistent_selectbox(
            "Har kitni der snapshot",
            (15, 30, 60),
            "auto_snapshot_interval_seconds",
            default=30,
            format_func=lambda value: (
                "1 minute" if value == 60 else f"{value} second"
            ),
            disabled=not auto_enabled,
        )
        fast_monitor_enabled = _persistent_toggle(
            "5-second Fast Live Monitor",
            "fast_monitor_enabled",
            default=True,
            disabled=not auto_enabled,
        )
        st.caption(
            "Recommended: full snapshot 30s + Fast Monitor 5s. Auto Snapshot ab "
            "processing/rerun par OFF nahi hota; 0 duration ka matlab manual OFF tak."
        )

        now_control = time.time()
        previous_interval = st.session_state.get("auto_snapshot_interval_seen")
        previous_duration = st.session_state.get("auto_snapshot_duration_seen")
        if auto_enabled:
            if "auto_snapshot_started_at" not in st.session_state:
                st.session_state.auto_snapshot_started_at = now_control
            if previous_duration is not None and int(previous_duration) != int(duration_minutes):
                # A newly selected timed run starts now; it never inherits an old
                # elapsed timer and switches itself off unexpectedly.
                st.session_state.auto_snapshot_started_at = now_control
            if (
                "auto_snapshot_next_due_at" not in st.session_state
                or previous_interval is not None
                and int(previous_interval) != int(interval_seconds)
            ):
                last_started = float(st.session_state.get("last_snapshot_started_ts", 0.0))
                base = max(now_control, last_started)
                st.session_state.auto_snapshot_next_due_at = base + int(interval_seconds)
            st.session_state.auto_snapshot_interval_seen = int(interval_seconds)
            st.session_state.auto_snapshot_duration_seen = int(duration_minutes)
        else:
            st.session_state.pop("auto_snapshot_started_at", None)
            st.session_state.pop("auto_snapshot_next_due_at", None)
            st.session_state.pop("auto_snapshot_priority_requested_at", None)

        # Five-second heartbeat reuses the same light cadence as Fast Monitor. It
        # performs no broker call until a full snapshot is actually due, so 30s
        # mode remains dependable without adding heavy critical-path work.
        auto_scheduler_every = 5.0 if auto_enabled else None

        @st.fragment(run_every=auto_scheduler_every)
        def auto_snapshot_scheduler() -> None:
            enabled = bool(st.session_state.get("auto_snapshot_enabled", False))
            if not enabled:
                st.caption("OFF — manual snapshot available hai")
                return

            current_snapshot = st.session_state.get("snapshot")
            snapshot_says_live = bool(
                current_snapshot is not None
                and getattr(current_snapshot.market_session, "is_live", False)
            )
            # Do not trust only the previous snapshot's session flag. A CLOSED
            # snapshot from yesterday would otherwise keep the scheduler paused
            # forever the next morning. The clock expectation wakes the scheduler
            # at the next regular NSE session without any extra API call.
            market_live = snapshot_says_live or _market_clock_expected_live()
            now_value = time.time()
            run_minutes = int(st.session_state.get("auto_snapshot_duration_minutes", 0) or 0)
            started = float(st.session_state.get("auto_snapshot_started_at", now_value))
            if run_minutes > 0 and now_value - started >= run_minutes * 60:
                _remember_runtime_control("auto_snapshot_enabled", False)
                st.session_state.pop("auto_snapshot_started_at", None)
                st.session_state.pop("auto_snapshot_next_due_at", None)
                st.success("Selected Auto Snapshot duration poori — automatic OFF")
                st.rerun(scope="app")

            if not market_live:
                st.caption(
                    "PAUSED — market closed hai; Auto Snapshot ON rahega aur next regular "
                    "market session me apne-aap resume hoga"
                )
                return

            interval = int(st.session_state.get("auto_snapshot_interval_seconds", 30) or 30)
            next_due = float(
                st.session_state.get("auto_snapshot_next_due_at", now_value + interval)
            )
            priority_at = float(
                st.session_state.get("auto_snapshot_priority_requested_at", 0.0) or 0.0
            )
            priority_due = priority_at > float(
                st.session_state.get("last_snapshot_started_ts", 0.0) or 0.0
            )
            seconds_left = max(0, int(round(next_due - now_value)))
            build_seconds = float(st.session_state.get("last_snapshot_build_seconds", 0.0) or 0.0)
            if build_seconds > interval:
                st.caption(
                    f"ON · {interval}s selected · last build {build_seconds:.1f}s; "
                    "effective cadence processing speed se limited hai"
                )
            else:
                st.caption(
                    f"ON · {interval}s cadence · next ~{seconds_left}s"
                    + (" · PRIORITY MOVE" if priority_due else "")
                )

            if st.session_state.get("snapshot_build_inflight", False):
                st.caption("Snapshot processing already running — overlap blocked")
                return
            if not priority_due and now_value < next_due:
                return

            build_started = time.time()
            with st.spinner("Refreshing snapshot in background — main page stays open…"):
                new_snapshot, elapsed, error = _build_authoritative_snapshot_once()
            if error is not None or new_snapshot is None:
                # Do not switch Auto Snapshot OFF on a transient feed/backend error.
                # Retry after a bounded delay instead of entering a rerun loop.
                st.session_state.auto_snapshot_next_due_at = time.time() + max(5, min(interval, 15))
                st.warning(
                    f"Auto refresh retry scheduled · {type(error).__name__ if error else 'busy'}"
                )
                return

            completed = time.time()
            # Start-to-start cadence when feasible; if processing itself is slower
            # than the selected interval, always give the UI a small quiet gap.
            fetched_is_live = bool(getattr(new_snapshot.market_session, "is_live", False))
            if _market_clock_expected_live() and not fetched_is_live:
                # Exchange holiday / server-side session disagreement: avoid hammering
                # the backend every 15/30 seconds while still checking periodically.
                st.session_state.auto_snapshot_next_due_at = completed + 300.0
            else:
                st.session_state.auto_snapshot_next_due_at = max(
                    build_started + interval, completed + 2.0
                )
            st.session_state.pop("auto_snapshot_priority_requested_at", None)
            st.success(f"Fresh snapshot ready · {elapsed:.1f}s")
            # This app rerun is now lightweight: the expensive snapshot already exists.
            st.rerun(scope="app")

        auto_snapshot_scheduler()

    # Manual refresh is also isolated in a fragment, so pressing it does not blank
    # or collapse the live workspace while the heavy snapshot is processing.
    @st.fragment
    def manual_snapshot_refresh() -> None:
        clicked = st.button("Fetch Fresh Snapshot", type="primary", width="stretch")
        if not clicked:
            return
        if st.session_state.get("snapshot_build_inflight", False):
            st.warning("Snapshot already processing — overlap blocked")
            return
        now_tick = time.time()
        last_tick = float(st.session_state.get("last_snapshot_started_ts", 0.0) or 0.0)
        remaining = CONFIG.snapshot_min_refresh_seconds - (now_tick - last_tick)
        if remaining > 0:
            st.warning(f"Please wait {remaining:.1f}s before another Dhan snapshot.")
            return
        with st.spinner("Refreshing snapshot — current page remains open…"):
            new_snapshot, elapsed, error = _build_authoritative_snapshot_once()
        if error is not None or new_snapshot is None:
            st.error(f"Snapshot failed safely: {error}")
            return
        if st.session_state.get("auto_snapshot_enabled", False):
            interval = int(st.session_state.get("auto_snapshot_interval_seconds", 30) or 30)
            st.session_state.auto_snapshot_next_due_at = max(
                now_tick + interval, time.time() + 2.0
            )
        if bool(getattr(new_snapshot.market_session, "is_live", False)):
            st.success(f"Fresh snapshot ready · {elapsed:.1f}s")
        else:
            st.info(
                f"Reference snapshot refreshed · {elapsed:.1f}s · market closed, "
                "isliye price/option values same reh sakte hain"
            )
        st.rerun(scope="app")

    manual_snapshot_refresh()
    clear_instrument_cache = False
    clear_option_state = False
    # Destructive maintenance is hidden from the normal trading UI. It can be
    # temporarily exposed by setting NSL_SHOW_MAINTENANCE=1 on the deployment.
    if os.getenv("NSL_SHOW_MAINTENANCE", "").strip() == "1":
        with st.expander("Advanced maintenance", expanded=False):
            st.warning("Maintenance only — normal trading me use mat karo.")
            confirm_cache = st.checkbox("Instrument cache reset confirm")
            clear_instrument_cache = st.button(
                "Reset instrument cache",
                width="stretch",
                disabled=not confirm_cache,
            )
            confirm_history = st.checkbox("Aaj ki bounded option history reset confirm")
            clear_option_state = st.button(
                "Reset today's option history",
                width="stretch",
                disabled=not confirm_history,
            )
    with st.expander("FII/DII — 15-session journal"):
        context_date = st.date_input(
            "Trading session date",
            datetime.now(ZoneInfo(IST_TIMEZONE)).date(),
            key="context_session_date",
        )
        context_key = context_date.isoformat()
        saved_context = context_store.get(context_date) or {}
        cloud_state = context_store.sync_status()
        if cloud_state.label == "CLOUD SYNC OK":
            st.success("FII/DII Storage: CLOUD + 3 LOCAL COPIES SAFE")
        elif cloud_state.label.startswith("CLOUD FAILED"):
            st.warning("FII/DII Storage: CLOUD FAILED · 3 LOCAL COPIES AVAILABLE")
        else:
            st.error("FII/DII Storage: LOCAL ONLY · REDEPLOY PAR DATA DELETE HO SAKTA HAI")
        st.caption(cloud_state.message)
        if not context_store.cloud_enabled:
            with st.expander("Permanent storage ON karne ka one-time setup", expanded=False):
                st.write(
                    "Streamlit ke **Manage app → Settings → Secrets** me existing `[dhan]` ko chhede bina "
                    "neeche wala section add karo. Token private data repo tak hi limited rakho."
                )
                st.code(
                    '[fii_dii_cloud]\n'
                    'owner = "YOUR_GITHUB_USERNAME"\n'
                    'repo = "nifty-seller-private-data"\n'
                    'token = "YOUR_FINE_GRAINED_TOKEN"\n'
                    'path = "fii_dii_15_sessions.json"\n'
                    'branch = "main"',
                    language="toml",
                )
                st.caption(
                    "Cloud ON hone ke baad save/update par 15-session journal private GitHub file me bhi auto-save hoga "
                    "aur nayi deployment par auto-restore hoga."
                )
        sync_context_now = st.button(
            "Sync cloud now",
            width="stretch",
            disabled=not context_store.cloud_enabled,
            key="sync_fii_dii_cloud_now",
        )

        def context_text(value: object) -> str:
            return "" if value is None else str(value)

        fii_raw = st.text_input(
            "FII cash net ₹ crore",
            value=context_text(saved_context.get("fii_cash_net")),
            key=f"fii_cash_{context_key}",
        )
        dii_raw = st.text_input(
            "DII cash net ₹ crore",
            value=context_text(saved_context.get("dii_cash_net")),
            key=f"dii_cash_{context_key}",
        )
        fii_futures_contracts_raw = st.text_input(
            "FII Index Futures contracts (optional; quantity, not ₹ crore)",
            value=context_text(saved_context.get("fii_index_futures_contracts")),
            key=f"fii_futures_contracts_{context_key}",
        )
        futures_c1, futures_c2 = st.columns(2)
        with futures_c1:
            fii_futures_long_raw = st.text_input(
                "FII Futures Long %",
                value=context_text(saved_context.get("fii_futures_long_pct")),
                key=f"fii_futures_long_{context_key}",
            )
        with futures_c2:
            fii_futures_short_raw = st.text_input(
                "FII Futures Short %",
                value=context_text(saved_context.get("fii_futures_short_pct")),
                key=f"fii_futures_short_{context_key}",
            )
        st.caption(
            "Example: HDFC Sky me FII Index Futures 2,66,925; Long 8.78%; Short 91.22% ho to "
            "266925, 8.78, 91.22 enter karo. Minus sign contracts par mat lagao; direction Long/Short % se niklegi."
        )
        event_options = ["NONE", "LOW", "MEDIUM", "HIGH"]
        saved_level = str(saved_context.get("event_risk") or "NONE").upper()
        event_level = st.selectbox(
            "Verified market event risk",
            event_options,
            index=event_options.index(saved_level)
            if saved_level in event_options
            else 0,
            key=f"event_level_{context_key}",
        )
        event_verified = st.checkbox(
            "Risk/news personally verified",
            value=bool(saved_context.get("verified", False)),
            key=f"event_verified_{context_key}",
        )
        event_note = st.text_input(
            "Short event note (optional)",
            value=str(saved_context.get("event_note") or ""),
            key=f"event_note_{context_key}",
        )
        save_context = st.button(
            "Save / update selected date",
            width="stretch",
            key=f"save_context_{context_key}",
        )
        saved_rows = list(reversed(context_store.load()))
        if saved_rows:
            st.dataframe(
                [
                    {
                        "Date": row.get("date"),
                        "FII cash": row.get("fii_cash_net"),
                        "DII cash": row.get("dii_cash_net"),
                        "FII fut contracts": row.get("fii_index_futures_contracts"),
                        "Long %": row.get("fii_futures_long_pct"),
                        "Short %": row.get("fii_futures_short_pct"),
                        "Event": row.get("event_risk", "NONE"),
                    }
                    for row in saved_rows
                ],
                width="stretch",
                hide_index=True,
            )

    if sync_context_now:
        try:
            context_store.sync_now()
            st.session_state.pop("snapshot", None)
            st.success("FII/DII cloud aur local journal sync ho gaye")
            st.rerun()
        except Exception as exc:
            st.warning(f"Cloud sync nahi hua; local backup safe hai: {exc}")
    if save_context:
        try:
            context_store.upsert(
                session_date=context_date,
                fii_cash_net=optional_number(fii_raw),
                dii_cash_net=optional_number(dii_raw),
                fii_index_futures_net=None,
                fii_index_futures_contracts=optional_number(fii_futures_contracts_raw),
                fii_futures_long_pct=optional_number(fii_futures_long_raw),
                fii_futures_short_pct=optional_number(fii_futures_short_raw),
                event_risk=event_level,
                event_note=event_note,
                verified=event_verified,
            )
            st.session_state.pop("snapshot", None)
            if context_store.cloud_enabled:
                sync_state = context_store.sync_status()
                if sync_state.label == "CLOUD SYNC OK":
                    st.success(
                        f"{context_date.isoformat()} FII/DII cloud + local me permanently save hua"
                    )
                else:
                    st.warning(
                        f"{context_date.isoformat()} local 3-copy me save hua; cloud sync failed — backup download kar lo"
                    )
            else:
                st.warning(
                    f"{context_date.isoformat()} local 3-copy me save hua, lekin redeploy-safe nahi. Permanent cloud setup ON karo."
                )
            st.rerun()
        except Exception as exc:
            st.error(f"Context not saved: {exc}")
    if clear_instrument_cache:
        cache = Path("data/instrument_master.csv")
        if cache.exists():
            cache.unlink()
        InstrumentMaster.clear_memory_cache()
        st.success("Instrument cache cleared")
    if clear_option_state:
        state_store.clear()
        st.session_state.pop("snapshot", None)
        st.success("Bounded option history cleared")

if not credentials_ready:
    st.code(
        '[live_server]\nurl = "https://YOUR-SERVICE.up.railway.app"\napi_key = "YOUR_LIVE_API_KEY"',
        language="toml",
    )
    st.stop()

snapshot_built_now = False
snapshot_pipeline_started = time.perf_counter()

# Deployment/startup gate: when Railway is being replaced by the same GitHub commit,
# do not launch the expensive snapshot pipeline against a backend that is still
# warming.  A tiny /health probe contains no Dhan market-data request.  The fragment
# retries independently and promotes to a full app rerun as soon as Railway is ready.
if "snapshot" not in st.session_state and railway_ready:
    startup_probe_interval = 3

    @st.fragment(run_every=startup_probe_interval)
    def _railway_startup_gate() -> None:
        try:
            health = fetch_railway_health(
                live_server_url,
                live_server_api_key,
                timeout_seconds=1.5,
            )
            if bool(health.get("ready")):
                st.session_state["railway_startup_ready"] = True
                st.success("Railway backend ready — market workspace loading…")
                st.rerun()
            else:
                st.info(
                    "Railway backend warm-up chal raha hai. App automatically retry "
                    "karegi; Fetch button baar-baar dabane ki zarurat nahi."
                )
        except Exception as exc:
            st.info(
                "Railway backend reconnect ho raha hai — automatic retry active. "
                f"({type(exc).__name__})"
            )

    if not st.session_state.get("railway_startup_ready", False):
        _railway_startup_gate()
        st.caption("Cold-start protection: heavy snapshot tabhi build hoga jab backend ready ho.")
        st.stop()

if "snapshot" not in st.session_state:
    with st.spinner(
        "Building first authoritative DhanHQ snapshot, core market evidence and option intelligence..."
    ):
        new_snapshot, _initial_elapsed, _initial_error = _build_authoritative_snapshot_once()
    if _initial_error is not None or new_snapshot is None:
        st.error(
            f"Snapshot failed safely: {_initial_error}. Railway restart/health check karo; "
            "Fetch button baar-baar na dabayein."
        )
        st.stop()
    snapshot_built_now = True

snapshot = st.session_state.snapshot
previous_snapshot = st.session_state.get("previous_snapshot")


def _attach_snapshot_integrity_diagnostic(snapshot):
    """Attach zero-weight feed coherence diagnostics from existing feed metadata."""
    metadata = getattr(snapshot, "metadata", {})
    if metadata.get("snapshot_integrity"):
        return
    try:
        from analysis.snapshot_integrity import build_snapshot_integrity
        metadata["snapshot_integrity"] = build_snapshot_integrity(snapshot)
    except Exception as exc:
        metadata["snapshot_integrity"] = {
            "state": "UNAVAILABLE",
            "reason": f"{type(exc).__name__}",
            "effect_on_one_brain": "NONE — DIAGNOSTIC ONLY",
        }


def _attach_market_intelligence_shadow(snapshot, previous_snapshot):
    """Attach OB-MIE after current One-Brain decisions, with fail-open isolation."""
    metadata = getattr(snapshot, "metadata", {})
    if metadata.get("market_intelligence") or metadata.get("market_intelligence_error"):
        return
    started = time.perf_counter()
    try:
        from analysis.market_intelligence import calculate_market_intelligence
        result = calculate_market_intelligence(snapshot, previous_snapshot)
        metadata["market_intelligence"] = result.to_dict()
        metadata["market_intelligence_engine"] = "analysis.market_intelligence.calculate_market_intelligence"
        metadata["market_intelligence_core_weight"] = 0
    except Exception as exc:
        # Fail-open by design: canonical One Brain remains authoritative.
        metadata["market_intelligence_error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        performance = metadata.setdefault("performance", {})
        performance["market_intelligence_seconds"] = round(time.perf_counter() - started, 5)


def _finalize_snapshot_once(snapshot, previous_snapshot):
    """Run the canonical Future/Common/Guard pipeline once per fresh snapshot.

    Streamlit reruns caused by opening controls must not recompute the brain, resend
    history, or re-register paper positions. A fresh SnapshotService result clears the
    marker naturally because it is a new object.
    """

    if snapshot.metadata.get("canonical_finalized"):
        cached = st.session_state.get("shadow_entries_cache")
        if isinstance(cached, list):
            return cached
        entries = shadow_journal_store.load(refresh_cloud=False)
        st.session_state.shadow_entries_cache = entries
        return entries

    finalize_started = time.perf_counter()
    from analysis.future_brain import calculate_future_brain
    from analysis.simple_brain import calculate_simple_brain
    from analysis.decision_workspace import build_common_decision
    from analysis.trade_plan import activate_plan_candidate
    from analysis.execution_guard import calculate_execution_guard

    # First pass is sent with the observation; the second pass adds any matching
    # Railway outcomes returned by the same sync call.
    snapshot.metadata["future_brain"] = calculate_future_brain(
        snapshot, previous_snapshot, []
    ).to_dict()
    # Fetch completed outcomes first, but do not record a half-built observation.
    sync_day_memory(
        snapshot, live_server_url, live_server_api_key, record_event=False
    )
    snapshot.metadata["future_brain"] = calculate_future_brain(
        snapshot,
        previous_snapshot,
        snapshot.metadata.get("learning_outcomes") or [],
    ).to_dict()

    # v2.48: one simple authority. Future Brain remains advisory and cannot create
    # a second hard WAIT gate.
    future_view = snapshot.metadata["future_brain"]
    previous_simple = (
        (getattr(previous_snapshot, "metadata", {}) or {}).get("simple_brain")
        if previous_snapshot is not None
        else None
    )
    snapshot.metadata["simple_brain"] = calculate_simple_brain(
        snapshot,
        future_view,
        previous_simple=previous_simple,
    )
    simple_view = snapshot.metadata["simple_brain"]
    # SnapshotService already built every protected CE/PE/Condor plan. Reuse that
    # bundle and only activate the Simple-Brain candidate; avoid an expensive second
    # strike-engine calculation on every Streamlit finalize pass.
    common_proposal = build_common_decision(snapshot)
    common_candidate = str(common_proposal.get("best_strategy") or "WAIT")
    snapshot.trade_plan = activate_plan_candidate(
        snapshot.trade_plan, common_candidate, snapshot.market_session
    )
    snapshot.execution_guard = calculate_execution_guard(
        decision=snapshot.decision,
        trade_plan=snapshot.trade_plan,
        market_session=snapshot.market_session,
        option_intelligence=snapshot.option_intelligence,
        price_action=snapshot.price_action,
        risk_profile=snapshot.risk_profile,
        discipline_state=snapshot.discipline_state,
        big_player=snapshot.big_player_activity,
        feed_status=snapshot.feed_status,
        as_of=snapshot.created_at,
        selected_setup_override=common_candidate,
        final_action_override=common_candidate,
        simple_brain=snapshot.metadata.get("simple_brain") or {},
    )
    snapshot.metadata["common_decision"] = build_common_decision(
        snapshot, execution_guard=snapshot.execution_guard
    )
    # Shadow intelligence is calculated only after the authoritative One-Brain
    # decision/guard pipeline is finished.  It has zero core weight and cannot
    # feed back into any decision above.
    _attach_market_intelligence_shadow(snapshot, previous_snapshot)
    _attach_snapshot_integrity_diagnostic(snapshot)
    snapshot.metadata["canonical_finalized"] = True

    # Evidence remains exact, but the Railway endpoint now returns only an ACK for
    # this frequent write; full history/report transfer stays on its 60-second TTL.
    record_final_day_memory(snapshot, live_server_url, live_server_api_key)
    entries = process_auto_shadow_journal(
        snapshot,
        shadow_journal_store,
        enabled=bool(shadow_journal_enabled),
    )

    # Re-register the paper book at most once per minute unless its content changes.
    # This preserves restart recovery without blocking every 15-second snapshot.
    if live_server_url and live_server_api_key and entries:
        paper_signature = tuple(
            (
                str(item.get("trade_id") or ""),
                str(item.get("status") or item.get("state") or ""),
                str(item.get("closed_at") or ""),
            )
            for item in entries
            if isinstance(item, dict)
        )
        now_sync = time.time()
        last_signature = st.session_state.get("paper_monitor_signature")
        last_sync = float(st.session_state.get("paper_monitor_sync_at", 0.0))
        paper_sync_due = paper_signature != last_signature or now_sync - last_sync >= 60.0
        if paper_sync_due:
            try:
                remote = RailwayDhanClient(
                    live_server_url, live_server_api_key, timeout_seconds=3
                )._post("/paper-monitor", {"entries": entries})
                if remote.get("entries") != entries:
                    entries = remote["entries"]
                    shadow_journal_store.save(entries, sync_cloud=False)
                st.session_state.paper_monitor_signature = paper_signature
                st.session_state.paper_monitor_sync_at = now_sync
            except Exception:
                st.warning(
                    "Paper server sync pending — local journal safe; background exit monitoring not confirmed."
                )

    performance = snapshot.metadata.setdefault("performance", {})
    performance["finalize_seconds"] = round(
        time.perf_counter() - finalize_started, 4
    )
    if snapshot_built_now:
        performance["pipeline_seconds"] = round(
            time.perf_counter() - snapshot_pipeline_started, 4
        )
    st.session_state.shadow_entries_cache = entries
    return entries


shadow_entries = _finalize_snapshot_once(snapshot, previous_snapshot)
# Idempotent safety for a restored/cached snapshot created before OB-MIE existed.
# Fresh snapshots already have this attached inside the canonical finalizer.
_attach_market_intelligence_shadow(snapshot, previous_snapshot)
_attach_snapshot_integrity_diagnostic(snapshot)
# Phase-11 latency history is session-local diagnostics only. It never feeds a
# market score, and keeping 120 points is enough for P50/P95 without unbounded RAM.
_perf_now = snapshot.metadata.setdefault("performance", {})
if snapshot_built_now and _perf_now.get("pipeline_seconds") is not None:
    _latency_history = list(st.session_state.get("pipeline_latency_history", []))
    _latency_history.append(float(_perf_now["pipeline_seconds"]))
    st.session_state.pipeline_latency_history = _latency_history[-120:]
snapshot.metadata["evidence_async_status"] = evidence_async_status()
# Presentation copy only: scores, strikes, final action and execution readiness remain
# authoritative. Deep-copy normalization is cached per immutable snapshot so opening a
# Streamlit panel does not clone all candle/option DataFrames again.
_view_key = (snapshot.snapshot_id, bool(snapshot.metadata.get("canonical_finalized")))
if st.session_state.get("view_snapshot_cache_key") != _view_key:
    st.session_state.view_snapshot_cache = prepare_snapshot_for_presentation(snapshot)
    st.session_state.view_snapshot_cache_key = _view_key
view_snapshot = st.session_state.view_snapshot_cache

if previous_snapshot is not None:
    _previous_view_key = (
        previous_snapshot.snapshot_id,
        bool(previous_snapshot.metadata.get("canonical_finalized")),
    )
    if st.session_state.get("previous_view_snapshot_cache_key") != _previous_view_key:
        st.session_state.previous_view_snapshot_cache = prepare_snapshot_for_presentation(
            previous_snapshot
        )
        st.session_state.previous_view_snapshot_cache_key = _previous_view_key
    previous_view_snapshot = st.session_state.previous_view_snapshot_cache
else:
    previous_view_snapshot = None

_perf = snapshot.metadata.get("performance") or {}
_stages = _perf.get("stages") or {}
_slowest = str(_perf.get("slowest_stage") or "")
_slowest_seconds = float(_stages.get(_slowest) or 0.0) if _slowest else 0.0

# Always-visible snapshot runtime strip. The old timing line disappeared whenever a
# restored/cached snapshot did not carry pipeline metadata, which made a working
# scheduler look broken. Keep operational state visible even with partial metadata.
_now_ist = datetime.now(ZoneInfo(IST_TIMEZONE))
_created_at = getattr(snapshot, "created_at", None)
if _created_at is not None:
    try:
        _created_ist = _created_at.astimezone(ZoneInfo(IST_TIMEZONE))
    except Exception:
        _created_ist = _created_at
    _snapshot_clock = _created_ist.strftime("%H:%M:%S")
    try:
        _snapshot_age = max(0.0, (_now_ist - _created_ist).total_seconds())
    except Exception:
        _snapshot_age = None
else:
    _snapshot_clock = "—"
    _snapshot_age = None

_auto_on = bool(st.session_state.get("auto_snapshot_enabled", False))
_auto_interval = int(st.session_state.get("auto_snapshot_interval_seconds", 30) or 30)
_snapshot_live = bool(getattr(snapshot.market_session, "is_live", False))
_clock_live = _market_clock_expected_live()
if _auto_on and (_snapshot_live or _clock_live):
    _next_due = float(st.session_state.get("auto_snapshot_next_due_at", time.time()) or time.time())
    _next_text = f"next ~{max(0, int(round(_next_due - time.time())))}s"
    _auto_text = f"{_auto_interval}s ON"
else:
    _next_text = "next session" if _auto_on else "manual"
    _auto_text = "PAUSED (CLOSED)" if _auto_on else "OFF"

_last_ok = float(st.session_state.get("last_snapshot_refresh_ok_at", 0.0) or 0.0)
_last_ok_text = (
    datetime.fromtimestamp(_last_ok, ZoneInfo(IST_TIMEZONE)).strftime("%H:%M:%S")
    if _last_ok > 0 else "—"
)
_build_text = (
    f"{float(st.session_state.get('last_snapshot_build_seconds', 0.0) or 0.0):.1f}s"
    if st.session_state.get("last_snapshot_build_seconds") is not None else "—"
)
_data_text = "LIVE" if _snapshot_live else "LAST DATA"
st.caption(
    f"🕒 Snapshot {_snapshot_clock} · age {_format_age(_snapshot_age)} · "
    f"Auto {_auto_text} ({_next_text}) · last refresh {_last_ok_text} · "
    f"build {_build_text} · {_data_text}"
)
if _perf.get("pipeline_seconds") is not None:
    _sync_diag = snapshot.metadata.get("snapshot_integrity") or {}
    _sync_suffix = (
        f" · sync {_sync_diag.get('state', '—')}"
        f" {_sync_diag.get('core_live', 0)}/{_sync_diag.get('core_total', 0)}"
        if _sync_diag else ""
    )
    st.caption(
        f"⚙️ Processing {float(_perf['pipeline_seconds']):.2f}s · "
        f"snapshot {float(_perf.get('build_seconds') or 0.0):.2f}s · "
        f"slowest {_slowest or '—'} {_slowest_seconds:.2f}s"
        f"{_sync_suffix}"
    )


@st.fragment(
    run_every=(
        CONFIG.fast_monitor_interval_seconds
        if st.session_state.get("auto_snapshot_enabled", False)
        and st.session_state.get("fast_monitor_enabled", True)
        else None
    )
)
def render_fast_live_monitor() -> None:
    if not st.session_state.get("auto_snapshot_enabled", False):
        return
    if not st.session_state.get("fast_monitor_enabled", True):
        return
    current = st.session_state.get("snapshot")
    if current is None or not getattr(current.market_session, "is_live", False):
        st.caption("⚡ Fast Monitor PAUSED — market live nahi")
        return
    try:
        remote = None
        if live_server_url and live_server_api_key:
            remote = fetch_railway_live_state(
                live_server_url, live_server_api_key, timeout_seconds=3.0
            )
        if remote is not None and remote.nifty_ltp is not None:
            rows = [
                FastQuote(
                    label="NIFTY Live",
                    last_price=remote.nifty_ltp,
                    baseline=getattr(current, "nifty_quote", {}).get("last_price"),
                    last_trade_time=remote.captured_at,
                )
            ]
            impulse = calculate_live_impulse_from_changes(
                {
                    5: remote.change_5s,
                    15: remote.change_15s,
                    30: remote.change_30s,
                    60: remote.change_60s,
                }
            )
            source = "Railway WebSocket"
        else:
            # Railway is the only live source when configured. Calling Dhan again
            # from Streamlit would duplicate traffic and recreate HTTP 429 bursts.
            if railway_ready:
                st.caption("⚡ Fast Monitor fallback — Railway tick ka wait; direct Dhan call roki gayi")
                return
            credentials = Credentials(client_id=client_id, access_token=access_token)
            rows = fetch_fast_quotes(DhanClient(credentials), current)
            source = "Legacy direct Dhan"
            captured_ts = time.time()
            history = list(st.session_state.get("fast_quote_history", []))
            impulse = calculate_live_impulse(rows, history, captured_ts=captured_ts)
            history.append(
                {
                    "captured_ts": captured_ts,
                    "prices": {
                        item.label: item.last_price
                        for item in rows
                        if item.last_price is not None
                    },
                }
            )
            st.session_state.fast_quote_history = [
                item
                for item in history
                if captured_ts - float(item.get("captured_ts", 0.0)) <= 180
            ][-40:]
        if not rows:
            st.caption("⚡ Fast Monitor — quote unavailable; full snapshot safe hai")
            return
        st.session_state.fast_live_impulse = impulse
        # Priority lane: a confirmed fast move should not wait for the next 15/30/60s
        # scheduled rebuild. It only requests a fresh full snapshot; the 5s monitor
        # never creates a trade action by itself.
        if impulse.state == "MAJOR MOVE CONFIRMED":
            now_fast = time.time()
            last_priority = float(st.session_state.get("last_priority_snapshot_ts", 0.0))
            last_full = float(st.session_state.get("last_snapshot_fetch_ts", 0.0))
            cooldown = max(CONFIG.snapshot_min_refresh_seconds, CONFIG.simple_priority_snapshot_cooldown_seconds)
            if now_fast - last_priority >= cooldown and now_fast - last_full >= CONFIG.snapshot_min_refresh_seconds:
                st.session_state.last_priority_snapshot_ts = now_fast
                # Do not force a full-app rerun from the 5s monitor. The lightweight
                # auto scheduler fragment will pick this up on its next heartbeat and
                # build the full snapshot while the current page remains mounted.
                st.session_state.auto_snapshot_priority_requested_at = now_fast
        with st.container(border=True):
            icon = (
                "🟢" if impulse.direction == "BULLISH"
                else "🔴" if impulse.direction == "BEARISH"
                else "🟡" if impulse.state != "STABLE"
                else "⚪"
            )
            st.markdown(
                f"{icon} **LIVE IMPULSE: {impulse.direction} — {impulse.state} "
                f"{impulse.score:.0f}/100**"
            )
            st.caption(
                f"⚡ {monitor_timestamp()} · {source} · Candle close ka wait nahi · "
                "early warning only, OI/volume confirmation parallel"
            )
            if impulse.reasons:
                st.caption(" | ".join(impulse.reasons))
            if impulse.premium_shock != "NONE":
                st.warning("⚡ ATM premium mein unusually fast change detect hua.")
            cols = st.columns(len(rows))
            for col, item in zip(cols, rows):
                value = f"{item.last_price:,.2f}" if item.last_price is not None else "—"
                delta = f"{item.change:+.2f}" if item.change is not None else None
                col.metric(item.label, value, delta)
    except Exception as exc:
        st.caption(f"⚡ Fast Monitor fallback — full snapshot safe hai ({exc})")


render_fast_live_monitor()

# Alert delivery is independent of whether the visual panel is expanded. Alert
# preferences use non-widget keys, so a processing rerun cannot silently switch them
# off when the optional controls are temporarily absent.
if "combined_signal_alerts_enabled" not in st.session_state:
    st.session_state.combined_signal_alerts_enabled = True
if "market_intelligence_alerts_enabled" not in st.session_state:
    st.session_state.market_intelligence_alerts_enabled = True
if "market_alert_sound_enabled" not in st.session_state:
    st.session_state.market_alert_sound_enabled = False
_remember_runtime_control(
    "combined_signal_alerts_enabled",
    bool(st.session_state.get("combined_signal_alerts_enabled", True)),
)
_remember_runtime_control(
    "market_intelligence_alerts_enabled",
    bool(st.session_state.get("market_intelligence_alerts_enabled", True)),
)
_remember_runtime_control(
    "market_alert_sound_enabled",
    bool(st.session_state.get("market_alert_sound_enabled", False)),
)
process_market_intelligence_alerts(snapshot, live_server_url, live_server_api_key)
process_combined_signal_alerts(snapshot, live_server_url, live_server_api_key)


def render_market_decision_reason_panel() -> None:
    """Display the existing decision evidence beside its Common Final Gate."""
    with persistent_panel(
        "Market Decision Ka Reason",
        "panel_decision_reason_open",
    ) as panel_open:
        if not panel_open:
            return
        render_core_evidence(view_snapshot)
        core_tabs = st.tabs(
            ["Price Action", "Support & Resistance", "Volume", "EMA / MACD / RSI"]
        )
        with core_tabs[0]:
            render_price_action(view_snapshot)
        with core_tabs[1]:
            render_levels(view_snapshot)
        with core_tabs[2]:
            render_volume(view_snapshot)
        with core_tabs[3]:
            render_indicators(view_snapshot)

render_compact_status_bar(view_snapshot)
render_move_radar(view_snapshot)
render_main_ai_market_view(
    view_snapshot,
    previous_view_snapshot,
    decision_reason_renderer=render_market_decision_reason_panel,
)
render_market_intelligence(view_snapshot)

# PRE-LIVE MAIN SCREEN (v2.64): keep only the highest-value live evidence visible.
# W/M + special candle remain prominent inside this compact nearest-level block.
render_compact_barrier_map(view_snapshot, previous_view_snapshot)
render_market_pulse_strip(view_snapshot)
render_compact_strategy_summary(view_snapshot)

# The chart is intentionally optional.  Keeping the iframe closed during ordinary
# auto-refresh removes the largest layout-shift source while the Brain keeps running.
with persistent_panel("📊 Show Live Chart — Simple / Advanced", "panel_live_chart_open") as panel_open:
    if panel_open:
        render_live_barrier_chart(view_snapshot)

# Alerts remain the same calculation/delivery lanes.  Automatic combined-alert
# processing runs above regardless of whether this visual hub is open.
with persistent_panel("🔔 Alerts", "panel_alerts_hub_open") as alerts_open:
    if alerts_open:
        st.caption(
            "Smart Alert mode: Market Intelligence primary Telegram voice hai; W/M + Candle + Big Player "
            "supportive evidence ke roop me calculate/record hote hain aur same story me merge hote hain."
        )
        _persistent_toggle(
            "Market Intelligence / Move Radar / Liquidity alerts ON",
            "market_intelligence_alerts_enabled",
            default=True,
            help="Big-move risk, pressure quality/fake-risk, move attack, liquidity hunt/sweep, One-Brain alignment/conflict aur build-up/flip alerts. Existing market snapshot hi reuse hota hai; extra broker call nahi.",
        )
        _mie_status = st.session_state.get("market_intelligence_alert_status")
        if _mie_status:
            st.caption("Market Intelligence alerts: " + str(_mie_status))
        with persistent_panel(
            "Strong Candle / W-M / Big Player Alerts",
            "panel_pattern_alerts_open",
        ) as panel_open:
            if panel_open:
                render_pattern_alerts(snapshot, live_server_url, live_server_api_key)
        with persistent_panel(
            "Manual CE/PE Premium Alert",
            "panel_market_alerts_open",
        ) as panel_open:
            if panel_open:
                render_market_alerts(
                    view_snapshot,
                    live_server_url=live_server_url,
                    live_server_api_key=live_server_api_key,
                )

# Preserve any alert preference changed inside the optional controls across websocket
# reconnects in the same Streamlit process.
_remember_runtime_control(
    "combined_signal_alerts_enabled",
    bool(st.session_state.get("combined_signal_alerts_enabled", True)),
)
_remember_runtime_control(
    "market_alert_sound_enabled",
    bool(st.session_state.get("market_alert_sound_enabled", False)),
)


# PRE-LIVE MAIN ROUTE TOOLS (v2.64.6): these five high-use live panels stay
# directly accessible on the main page.  Their calculations/renderers are unchanged;
# only the navigation level moved out of More Tools & Advanced.
with persistent_panel("🛡️ Strategy & Strike Detail", "panel_strategy_detail_open") as panel_open:
    if panel_open:
        render_protected_candidates(view_snapshot)

with persistent_panel("📈 Options Live Board", "panel_options_live_board_open") as panel_open:
    if panel_open:
        render_options_live_board(view_snapshot, state_store)

with persistent_panel("🧭 15–30 Min + Timeframe Detail", "panel_timeframe_open") as panel_open:
    if panel_open:
        render_timeframe_outlook(view_snapshot, st.session_state.get("fast_live_impulse"))

with persistent_panel("🧮 Spot-to-Premium Calculator", "panel_spot_premium_open") as panel_open:
    if panel_open:
        render_spot_premium_calculator(view_snapshot, state_store)

with persistent_panel("Compact Evidence — Diagnostic", "panel_compact_evidence_open") as panel_open:
    if panel_open:
        render_evidence_matrix(view_snapshot, previous_view_snapshot)

# Everything below is optional analysis/review.  The outer persistent toggle keeps
# its state across 15/30s reruns and makes the default live page short and stable.
with persistent_panel("⚙️ More Tools & Advanced", "panel_more_tools_open") as more_open:
    if more_open:
        with persistent_panel("🕯️ Candle Pattern Library — Shadow", "panel_candle_library_shadow_open") as panel_open:
            if panel_open:
                render_candle_pattern_library_shadow(view_snapshot)

        with persistent_panel("🎯 AI Move Tracker", "panel_ai_move_tracker_open") as panel_open:
            if panel_open:
                render_ai_move_tracker(view_snapshot, live_server_url, live_server_api_key)

        with persistent_panel("🎞️ One Brain Replay + Review", "panel_phase3_replay_open") as panel_open:
            if panel_open:
                render_phase3_replay(view_snapshot, live_server_url, live_server_api_key)

        with persistent_panel("🧪 Strategy Lab — Payoff + Greeks + What-If", "panel_phase4_strategy_lab_open") as panel_open:
            if panel_open:
                render_phase4_strategy_lab(view_snapshot)

        with persistent_panel("🛠️ Strategy Repair + Advanced Risk", "panel_phase7_strategy_repair_open") as panel_open:
            if panel_open:
                render_phase7_strategy_repair(view_snapshot)

        with persistent_panel("📊 Robustness Backtest — Actions + Walk-Forward", "panel_phase5_validation_lab_open") as panel_open:
            if panel_open:
                render_phase5_validation_lab(view_snapshot, live_server_url, live_server_api_key)

        with persistent_panel(
            "🎯 RSI Top–Bottom Setup — Alag Strategy",
            "panel_rsi_reversal_setup_open",
        ) as panel_open:
            if panel_open:
                render_rsi_reversal_setup(
                    snapshot,
                    previous_snapshot,
                    record_trade=discipline_store.mark_trade,
                )

        with persistent_panel("🧪 Auto Shadow Journal", "panel_shadow_journal_open") as panel_open:
            if panel_open:
                render_shadow_journal_status(shadow_entries, shadow_journal_store, view_snapshot)
                render_auto_shadow_journal(
                    shadow_entries,
                    view_snapshot.created_at.date().isoformat(),
                    shadow_journal_store,
                )

        with persistent_panel(
            "Advanced Options Evidence",
            "panel_advanced_options_open",
        ) as panel_open:
            if panel_open:
                render_option_intelligence(view_snapshot)
                render_phase2_options_intelligence(view_snapshot, state_store)
                option_tabs = st.tabs(
                    [
                        "Premium + OI + Volume Flow",
                        "1m / 3m / 5m Movement",
                        "OI Walls, Clusters & PCR",
                        "Top-9 Weighted Contribution",
                        "VIX Context",
                        "FII/DII & Event Risk",
                        "Live Market News",
                    ]
                )
                with option_tabs[0]:
                    render_option_flow_matrix(view_snapshot)
                with option_tabs[1]:
                    render_option_windows(view_snapshot)
                with option_tabs[2]:
                    render_walls_and_pcr(view_snapshot)
                with option_tabs[3]:
                    render_heavyweight_intelligence(view_snapshot)
                with option_tabs[4]:
                    render_vix_context(view_snapshot)
                with option_tabs[5]:
                    render_market_context(view_snapshot)
                with option_tabs[6]:
                    render_news_context(view_snapshot)

        with persistent_panel("🔬 Detailed Evidence", "panel_detailed_evidence_open") as panel_open:
            if panel_open:
                render_detailed_evidence(view_snapshot)

        with persistent_panel("⚙️ Performance Diagnostics", "panel_performance_diagnostics_open") as panel_open:
            if panel_open:
                render_performance_diagnostics(
                    snapshot,
                    refresh_interval_seconds=float(
                        st.session_state.get(
                            "auto_snapshot_interval_seconds",
                            CONFIG.full_snapshot_default_seconds,
                        )
                    ),
                    snapshot_built_now=snapshot_built_now,
                    pipeline_history=list(st.session_state.get("pipeline_latency_history", [])),
                )

        with persistent_panel("❓ One Brain Quick Guide", "panel_help_guide_open") as panel_open:
            if panel_open:
                render_help_guide()

        with persistent_panel("📚 Recorded Data + Calibration", "panel_day_memory_open") as panel_open:
            if panel_open:
                render_day_memory(snapshot, live_server_url, live_server_api_key)

with st.expander("🧰 Checks & Downloads Centre", expanded=False):

    pdf_snapshot_key = st.session_state.get("audit_pdf_snapshot_id")
    if pdf_snapshot_key != snapshot.snapshot_id:
        st.session_state.pop("audit_pdf_bytes", None)
        st.session_state.pop("quick_pdf_bytes", None)
        st.session_state.pop("support_bundle_bytes", None)
        st.session_state.audit_pdf_snapshot_id = snapshot.snapshot_id

    quick_col, full_col, support_col = st.columns(3)
    with quick_col:
        generate_quick_pdf = st.button(
            "Generate Quick Market Report", type="primary", width="stretch"
        )
        if generate_quick_pdf:
            try:
                st.session_state.pop("audit_pdf_bytes", None)
                st.session_state.pop("support_bundle_bytes", None)
                with st.spinner("Building 2-page quick report from current snapshot only..."):
                    st.session_state.quick_pdf_bytes = build_quick_market_pdf(
                        view_snapshot, previous_view_snapshot
                    )
                st.success("Quick Market Report ready")
            except Exception as exc:
                st.error(f"Quick report not generated: {exc}")
        if st.session_state.get("quick_pdf_bytes"):
            st.download_button(
                "Download Quick Market Report",
                data=st.session_state.quick_pdf_bytes,
                file_name=quick_pdf_filename(snapshot),
                mime="application/pdf",
                width="stretch",
            )

    with full_col:
        generate_pdf = st.button("Generate Complete Diagnostic PDF", width="stretch")
        if generate_pdf:
            try:
                st.session_state.pop("quick_pdf_bytes", None)
                st.session_state.pop("support_bundle_bytes", None)
                with st.spinner("Building full audit PDF from the current snapshot only..."):
                    st.session_state.audit_pdf_bytes = build_full_audit_pdf(
                        view_snapshot, previous_view_snapshot
                    )
                st.success("Complete Diagnostic PDF ready")
            except Exception as exc:
                st.error(f"Complete Diagnostic PDF not generated: {exc}")
        if st.session_state.get("audit_pdf_bytes"):
            st.download_button(
                "Download Complete Diagnostic PDF",
                data=st.session_state.audit_pdf_bytes,
                file_name=audit_pdf_filename(snapshot),
                mime="application/pdf",
                width="stretch",
            )

    with support_col:
        generate_bundle = st.button("Generate Support Bundle", width="stretch")
        if generate_bundle:
            try:
                st.session_state.pop("quick_pdf_bytes", None)
                st.session_state.pop("audit_pdf_bytes", None)
                with st.spinner("Building one credential-free support ZIP..."):
                    st.session_state.support_bundle_bytes = build_support_bundle(
                        view_snapshot, previous_view_snapshot, shadow_entries
                    )
                st.success("Support Bundle ready - update/diagnosis ke liye isi ZIP ko bhejein")
            except Exception as exc:
                st.error(f"Support Bundle not generated: {exc}")
        if st.session_state.get("support_bundle_bytes"):
            st.download_button(
                "Download Support Bundle ZIP",
                data=st.session_state.support_bundle_bytes,
                file_name=support_bundle_filename(snapshot),
                mime="application/zip",
                width="stretch",
            )

    st.divider()
    st.markdown("**Market Intelligence validation**")
    st.caption("One-click shadow test pack: Impulse/Move Radar + Liquidity Hunt/Sweep + Alignment ko 5/15/30m outcomes ke saath validate karta hai. Existing Railway Day Memory hi read hoti hai; live Dhan traffic aur One Brain decision path par zero effect.")
    if st.button("Prepare Market Intelligence Test Pack", key="prepare_market_intelligence_test_pack", width="stretch"):
        if not live_server_url or not live_server_api_key:
            st.info("Market Intelligence Test Pack ke liye Railway connection chahiye.")
        else:
            try:
                with st.spinner("Building Market Intelligence validation ZIP from already-recorded evidence..."):
                    raw_evidence = RailwayDhanClient(
                        live_server_url, live_server_api_key, timeout_seconds=60
                    ).download_bytes("/day-memory-export-file")
                    st.session_state.market_intelligence_test_pack_bytes = build_market_intelligence_test_pack(
                        raw_evidence, view_snapshot.public_summary()
                    )
                st.success("Market Intelligence Test Pack ready")
            except Exception as exc:
                st.error(f"Market Intelligence Test Pack not generated: {exc}")
    if st.session_state.get("market_intelligence_test_pack_bytes"):
        _mie_stamp = snapshot.created_at.strftime("%Y%m%d_%H%M%S")
        st.download_button(
            "Download Market Intelligence Test Pack ZIP",
            data=st.session_state.market_intelligence_test_pack_bytes,
            file_name=f"one_brain_market_intelligence_test_pack_{_mie_stamp}.zip",
            mime="application/zip",
            width="stretch",
        )

    st.divider()
    st.markdown("**Recorded data and backups**")
    evidence_col, journal_col = st.columns(2)
    with evidence_col:
        render_evidence_download(live_server_url, live_server_api_key)
    with journal_col:
        render_shadow_journal_download(
            shadow_entries, view_snapshot.created_at.date().isoformat(), shadow_journal_store
        )

    st.download_button(
        "Download FII/DII 15-session Backup JSON",
        data=context_store.export_bytes(),
        file_name="nifty_seller_lite_fii_dii_15_sessions.json",
        mime="application/json",
        width="stretch",
    )
    context_backup = st.file_uploader(
        "Restore FII/DII Journal Backup (JSON)",
        type=["json"],
        key="context_backup_upload",
    )
    if st.button(
        "Restore Uploaded FII/DII Backup",
        width="stretch",
        disabled=context_backup is None,
    ) and context_backup is not None:
        try:
            context_store.import_bytes(context_backup.getvalue())
            for key in list(st.session_state):
                if key.startswith(
                    (
                        "fii_cash_", "dii_cash_", "fii_futures_",
                        "fii_futures_contracts_", "fii_futures_long_",
                        "fii_futures_short_", "event_level_",
                        "event_verified_", "event_note_",
                    )
                ):
                    st.session_state.pop(key, None)
            st.session_state.pop("snapshot", None)
            st.success("15-session institutional journal restored")
            st.rerun()
        except Exception as exc:
            st.error(f"Backup not restored: {exc}")

    with st.expander("App, Railway and feed checks", expanded=False):
        st.write(f"App version: `{CONFIG.version}`")
        st.write(f"Presentation mode: `{'PUBLIC SAFE' if public_mode_enabled() else 'INTERNAL'}`")
        st.write(
            "Railway gateway: "
            + ("READY" if railway_ready else "NOT CONFIGURED / LEGACY MODE")
        )
        for name, status in view_snapshot.feed_status.items():
            st.caption(
                f"{name}: {getattr(status, 'use_state', '—')} · "
                f"{getattr(status, 'message', '')}"
            )
        diagnostics = snapshot.metadata.get("recording_diagnostics") or {}
        if diagnostics:
            st.caption(
                "Evidence recorder: "
                + ("AVAILABLE" if diagnostics.get("available") else "UNAVAILABLE")
                + f" · {diagnostics.get('recording_health') or diagnostics.get('error') or '—'}"
            )
    st.caption("Railway memory safety: ek time par sirf ek generated report RAM me rakhi jati hai; next snapshot par clear hoti hai.")
    gc.collect()

if os.getenv("NSL_SHOW_DEVELOPER_DATA", "").strip() == "1":
    with st.expander("Developer Raw Market Data (screen only)", expanded=False):
        market_tabs = st.tabs(
            [
                "Candles & Futures Volume",
                "Option Chain",
                "Top-9 Quotes",
                "VIX & Future",
                "Snapshot JSON",
            ]
        )
        with market_tabs[0]:
            render_candles(view_snapshot)
        with market_tabs[1]:
            render_option_chain(view_snapshot)
        with market_tabs[2]:
            render_heavyweights(view_snapshot)
        with market_tabs[3]:
            left, right = st.columns(2)
            left.write("**India VIX quote**")
            left.json(view_snapshot.vix_quote or {"status": "not resolved"})
            right.write("**Nearest NIFTY future quote**")
            right.json(view_snapshot.nifty_future_quote or {"status": "not resolved"})
        with market_tabs[4]:
            st.json(view_snapshot.public_summary())
