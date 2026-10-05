from __future__ import annotations

import os
import time
import gc
from dataclasses import replace
from contextlib import contextmanager
from html import escape
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
from services.railway_live_client import RailwayDhanClient, fetch_railway_live_state
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


@contextmanager
def persistent_panel(label: str, key: str):
    """A rerun-safe replacement for expanders used in the auto-refreshing view."""
    is_open = st.toggle(label, value=False, key=key)
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
        st.dataframe(rows, hide_index=True, use_container_width=True)
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
if not st.session_state.get("shadow_journal_cloud_loaded", False):
    shadow_journal_store.load(refresh_cloud=True)
    st.session_state["shadow_journal_cloud_loaded"] = True
news_service = MarketNewsService(Path(CONFIG.news_cache_path))


def optional_number(raw: str) -> float | None:
    value = str(raw or "").strip().replace(",", "")
    if not value:
        return None
    return float(value)


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
    shadow_journal_enabled = st.toggle(
        "Auto Shadow Journal ON",
        value=True,
        key="auto_shadow_journal_enabled",
        help="Maximum 5 paper trades/day; no broker orders.",
    )
    auto_due = bool(st.session_state.pop("auto_snapshot_due", False))
    refresh_requested = st.button(
        "Fetch Fresh Snapshot", type="primary", width="stretch"
    )
    refresh = False
    if auto_due:
        # The scheduler has already enforced its selected interval.
        refresh = True
    elif refresh_requested:
        now_tick = datetime.now().timestamp()
        last_tick = float(st.session_state.get("last_snapshot_fetch_ts", 0.0))
        remaining = CONFIG.snapshot_min_refresh_seconds - (now_tick - last_tick)
        if remaining > 0:
            if refresh_requested:
                st.warning(f"Please wait {remaining:.1f}s before another Dhan snapshot.")
        else:
            refresh = True

    with st.expander("⏱️ Auto Snapshot", expanded=False):
        auto_enabled = st.toggle("Auto Snapshot ON", key="auto_snapshot_enabled")
        duration_minutes = st.selectbox(
            "Kitni der chale",
            (5, 15, 30, 60),
            index=1,
            format_func=lambda value: "1 hour" if value == 60 else f"{value} minute",
            key="auto_snapshot_duration_minutes",
            disabled=not auto_enabled,
        )
        interval_seconds = st.selectbox(
            "Har kitni der snapshot",
            (15, 30, 60),
            index=1,
            format_func=lambda value: (
                "1 minute" if value == 60 else f"{value} second"
            ),
            key="auto_snapshot_interval_seconds",
            disabled=not auto_enabled,
        )
        fast_monitor_enabled = st.toggle(
            "5-second Fast Live Monitor",
            value=True,
            key="fast_monitor_enabled",
            disabled=not auto_enabled,
        )
        st.caption(
            "Recommended: full snapshot 30s + Fast Monitor 5s. MAJOR MOVE par fast monitor priority full snapshot trigger karta hai."
        )
        if auto_enabled and "auto_snapshot_started_at" not in st.session_state:
            st.session_state.auto_snapshot_started_at = time.time()
        if not auto_enabled:
            st.session_state.pop("auto_snapshot_started_at", None)

        @st.fragment(run_every=interval_seconds if auto_enabled else None)
        def auto_snapshot_scheduler() -> None:
            if not st.session_state.get("auto_snapshot_enabled", False):
                st.caption("OFF — manual snapshot available hai")
                return
            now_value = time.time()
            started = float(
                st.session_state.get("auto_snapshot_started_at", now_value)
            )
            duration_seconds = int(
                st.session_state.get("auto_snapshot_duration_minutes", 15)
            ) * 60
            elapsed = max(0.0, now_value - started)
            if elapsed >= duration_seconds:
                st.session_state.auto_snapshot_enabled = False
                st.session_state.pop("auto_snapshot_started_at", None)
                st.success("Auto Snapshot duration poori — automatic OFF")
                st.rerun()

            current_snapshot = st.session_state.get("snapshot")
            market_live = bool(
                current_snapshot is not None
                and getattr(current_snapshot.market_session, "is_live", False)
            )
            remaining_run = max(0, round((duration_seconds - elapsed) / 60))
            if not market_live:
                st.caption(
                    f"PAUSED — market live nahi · {remaining_run} min duration baaki"
                )
                return
            last_fetch = max(
                float(st.session_state.get("last_snapshot_fetch_ts", now_value)),
                float(st.session_state.get("auto_snapshot_reserved_at", 0.0)),
            )
            interval = int(
                st.session_state.get(
                    "auto_snapshot_interval_seconds",
                    CONFIG.full_snapshot_default_seconds,
                )
            )
            next_in = max(0, round(interval - (now_value - last_fetch)))
            st.caption(
                f"ON · Agla snapshot ~{next_in}s · {remaining_run} min baaki"
            )
            if now_value - last_fetch >= interval:
                # Reserve the interval before requesting a full rerun. The scheduler
                # is rendered above the snapshot builder, so without this lock the
                # next full run would still see an overdue timer and rerun forever
                # before reaching service.build(). The real fetch timestamp replaces
                # this reservation immediately after a successful snapshot.
                st.session_state.auto_snapshot_reserved_at = now_value
                st.session_state.auto_snapshot_due = True
                st.rerun(scope="app")

        auto_snapshot_scheduler()
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

if not credentials_ready:
    st.code(
        '[live_server]\nurl = "https://YOUR-SERVICE.up.railway.app"\napi_key = "YOUR_LIVE_API_KEY"',
        language="toml",
    )
    st.stop()

snapshot_built_now = False
snapshot_pipeline_started = time.perf_counter()

if "snapshot" not in st.session_state or refresh:
    try:
        with st.spinner(
            "Building one authoritative DhanHQ snapshot, core market evidence and option intelligence..."
        ):
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
                InstrumentMaster(Path("data/instrument_master.csv")),
                state_store,
                context_store,
                discipline_store,
                news_service=news_service,
            )
            previous_snapshot = st.session_state.get("snapshot")
            # Auto-snapshot cadence is measured from fetch START, not completion.
            # Otherwise an 8s build + 15s timer silently becomes ~23s between fresh
            # quotes.  This preserves the selected cadence without overlapping builds.
            snapshot_fetch_started_at = time.time()
            new_snapshot = service.build(risk_profile=risk_profile)
            if (
                previous_snapshot is not None
                and previous_snapshot.snapshot_id != new_snapshot.snapshot_id
                and previous_snapshot.created_at.date() == new_snapshot.created_at.date()
            ):
                st.session_state.previous_snapshot = previous_snapshot
            st.session_state.snapshot = new_snapshot
            st.session_state.last_snapshot_fetch_ts = snapshot_fetch_started_at
            st.session_state.last_snapshot_completed_ts = time.time()
            st.session_state.pop("auto_snapshot_reserved_at", None)
            snapshot_built_now = True
    except Exception as exc:
        st.session_state.pop("auto_snapshot_reserved_at", None)
        st.error(
            f"Snapshot failed safely: {exc}. Railway restart/health check karo; "
            "Fetch button baar-baar na dabayein."
        )
        st.stop()

snapshot = st.session_state.snapshot
previous_snapshot = st.session_state.get("previous_snapshot")
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
if _perf.get("pipeline_seconds") is not None:
    st.caption(
        f"⚙️ Processing {float(_perf['pipeline_seconds']):.2f}s · "
        f"snapshot {float(_perf.get('build_seconds') or 0.0):.2f}s · "
        f"slowest {_slowest or '—'} {_slowest_seconds:.2f}s"
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
                st.session_state.auto_snapshot_reserved_at = now_fast
                st.session_state.auto_snapshot_due = True
                st.rerun(scope="app")
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

# Alert delivery is independent of whether the visual panel is expanded. It reuses
# the existing snapshot only; no new broker/API market calculation is performed.
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
render_main_ai_market_view(
    view_snapshot,
    previous_view_snapshot,
    decision_reason_renderer=render_market_decision_reason_panel,
)

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
            "Automatic W/M + Candle + Big Player delivery background me same rahegi; "
            "neeche ke controls sirf view/configuration hain."
        )
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

        with persistent_panel("🛡️ Strategy & Strike Detail", "panel_strategy_detail_open") as panel_open:
            if panel_open:
                render_protected_candidates(view_snapshot)

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

        with persistent_panel("📈 Options Live Board", "panel_options_live_board_open") as panel_open:
            if panel_open:
                render_options_live_board(view_snapshot, state_store)

        with persistent_panel("🧭 15–30 Min + Timeframe Detail", "panel_timeframe_open") as panel_open:
            if panel_open:
                render_timeframe_outlook(view_snapshot, st.session_state.get("fast_live_impulse"))

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
                render_shadow_journal_status(shadow_entries, shadow_journal_store)
                render_auto_shadow_journal(
                    shadow_entries,
                    view_snapshot.created_at.date().isoformat(),
                    shadow_journal_store,
                )

        with persistent_panel("🧮 Spot-to-Premium Calculator", "panel_spot_premium_open") as panel_open:
            if panel_open:
                render_spot_premium_calculator(view_snapshot, state_store)

        with persistent_panel(
            "Compact Evidence — Diagnostic",
            "panel_compact_evidence_open",
        ) as panel_open:
            if panel_open:
                render_evidence_matrix(view_snapshot, previous_view_snapshot)

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
