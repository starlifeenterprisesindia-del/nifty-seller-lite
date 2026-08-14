from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

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
from services.housekeeping import run_housekeeping
from services.pdf_report import (
    audit_pdf_filename,
    build_full_audit_pdf,
    build_quick_market_pdf,
    quick_pdf_filename,
)
from services.snapshot_service import SnapshotService
from ui.components import (
    render_candles,
    render_decision,
    render_evidence_matrix,
    render_core_evidence,
    render_heavyweight_intelligence,
    render_heavyweights,
    render_indicators,
    render_levels,
    render_market_context,
    render_market_outlook,
    render_market_session,
    render_news_context,
    render_main_ai_market_view,
    render_option_chain,
    render_option_flow_matrix,
    render_option_intelligence,
    render_option_windows,
    render_price_action,
    render_vix_context,
    render_volume,
    render_walls_and_pcr,
    render_protected_candidates,
    render_big_player_activity,
)
from ui.premium_calculator import render_spot_premium_calculator
from ui.alerts import render_market_alerts


# Backward-compatible compact-level renderer. Older deployed ui/components.py files
# do not contain render_compact_barrier_map; do not let that single optional view
# helper crash the whole app during a partial GitHub upload.
try:
    from ui.components import render_compact_barrier_map
except ImportError:
    def render_compact_barrier_map(snapshot) -> None:
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
    @media(max-width:760px){.block-container{padding:.8rem .75rem 1.5rem}h1{font-size:1.85rem!important}}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("📈 Nifty Seller Lite")
st.caption(
    "Hinglish One-Brain market view"
)


def secret_value(name: str) -> str:
    try:
        if "dhan" in st.secrets and name in st.secrets["dhan"]:
            return str(st.secrets["dhan"][name])
    except Exception:
        pass
    return os.getenv(f"DHAN_{name.upper()}", "")


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

# Quiet housekeeping on every rerun. It prunes only temporary/raw market state older
# than 24h; FII/DII journal, manual discipline/trade state and learning summaries remain.
run_housekeeping(datetime.now(ZoneInfo(IST_TIMEZONE)))
state_store = OptionStateStore(Path(CONFIG.option_state_path))
cloud_journal = GitHubJsonJournal.from_mapping(
    cloud_journal_values(), timeout_seconds=CONFIG.market_context_cloud_timeout_seconds
)
context_store = MarketContextStore(
    Path(CONFIG.market_context_path),
    Path(CONFIG.market_context_mirror_path),
    Path(CONFIG.market_context_rescue_path),
    cloud_backend=cloud_journal,
)
discipline_store = DisciplineStore(Path(CONFIG.discipline_state_path))
news_service = MarketNewsService(Path(CONFIG.news_cache_path))


def optional_number(raw: str) -> float | None:
    value = str(raw or "").strip().replace(",", "")
    if not value:
        return None
    return float(value)


with st.sidebar:
    st.subheader("Connection")
    st.write(f"Version: `{CONFIG.version}`")
    credentials_ready = bool(client_id and access_token)
    if credentials_ready:
        st.success("Dhan credentials found")
    else:
        st.error("Dhan credentials missing")
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
            (5, 15, 30),
            index=1,
            format_func=lambda value: f"{value} minute",
            key="auto_snapshot_duration_minutes",
            disabled=not auto_enabled,
        )
        interval_seconds = st.selectbox(
            "Har kitni der snapshot",
            (60, 30),
            index=0,
            format_func=lambda value: "1 minute" if value == 60 else "30 second",
            key="auto_snapshot_interval_seconds",
            disabled=not auto_enabled,
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
                st.session_state.get("auto_snapshot_interval_seconds", 60)
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
        st.caption(
            "One row per trading date. Same date updates only that row; a new date adds a row. "
            "Latest 15 sessions local primary + mirror + rescue copies me save hote hain; cloud configured ho to private journal se auto-sync bhi hote hain. "
            "Blank value purane valid number ko erase nahi karti. Cash ₹ crore me hai; Index Futures contracts + Long/Short % me save hote hain."
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

        st.download_button(
            "Download 15-session backup JSON",
            data=context_store.export_bytes(),
            file_name="nifty_seller_lite_fii_dii_15_sessions.json",
            mime="application/json",
            width="stretch",
        )
        context_backup = st.file_uploader(
            "Restore journal backup (JSON)",
            type=["json"],
            key="context_backup_upload",
        )
        restore_context = st.button(
            "Restore uploaded backup",
            width="stretch",
            disabled=context_backup is None,
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
    if restore_context and context_backup is not None:
        try:
            context_store.import_bytes(context_backup.getvalue())
            for key in list(st.session_state):
                if key.startswith(
                    (
                        "fii_cash_",
                        "dii_cash_",
                        "fii_futures_",
                        "fii_futures_contracts_",
                        "fii_futures_long_",
                        "fii_futures_short_",
                        "event_level_",
                        "event_verified_",
                        "event_note_",
                    )
                ):
                    st.session_state.pop(key, None)
            st.session_state.pop("snapshot", None)
            st.success("15-session institutional journal restored")
            st.rerun()
        except Exception as exc:
            st.error(f"Backup not restored: {exc}")
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
                news_service=news_service,
            )
            previous_snapshot = st.session_state.get("snapshot")
            new_snapshot = service.build(risk_profile=risk_profile)
            if (
                previous_snapshot is not None
                and previous_snapshot.snapshot_id != new_snapshot.snapshot_id
                and previous_snapshot.created_at.date() == new_snapshot.created_at.date()
            ):
                st.session_state.previous_snapshot = previous_snapshot
            st.session_state.snapshot = new_snapshot
            st.session_state.last_snapshot_fetch_ts = datetime.now().timestamp()
            st.session_state.pop("auto_snapshot_reserved_at", None)
    except Exception as exc:
        st.error(f"Snapshot failed safely: {exc}")
        st.stop()

snapshot = st.session_state.snapshot
previous_snapshot = st.session_state.get("previous_snapshot")
# Presentation copy only: scores, strikes, final action and execution readiness remain
# authoritative. It normalizes contradictory labels/reasons for screen and PDF output.
view_snapshot = prepare_snapshot_for_presentation(snapshot)
previous_view_snapshot = (
    prepare_snapshot_for_presentation(previous_snapshot)
    if previous_snapshot is not None
    else None
)

render_market_session(view_snapshot)
render_main_ai_market_view(view_snapshot, previous_view_snapshot)
render_compact_barrier_map(view_snapshot)
with st.expander("🐘 Big Player Activity — Buying / Selling Alert", expanded=False):
    render_big_player_activity(view_snapshot)
render_protected_candidates(view_snapshot)
render_spot_premium_calculator(view_snapshot)

with st.expander("🔔 Heavy Activity + Manual Price Alerts", expanded=False):
    render_market_alerts(view_snapshot)

with st.expander("Compact Evidence + Next 5–15 Min Outlook", expanded=False):
    render_evidence_matrix(view_snapshot, previous_view_snapshot)
    render_market_outlook(view_snapshot)

with st.expander("Strategy Audit", expanded=False):
    render_decision(view_snapshot, audit_only=True)

with st.expander("Market Decision Ka Reason", expanded=False):
    render_core_evidence(view_snapshot)

    core_tabs = st.tabs(
        [
            "Price Action",
            "Support & Resistance",
            "Volume",
            "EMA / MACD / RSI",
        ]
    )
    with core_tabs[0]:
        render_price_action(view_snapshot)
    with core_tabs[1]:
        render_levels(view_snapshot)
    with core_tabs[2]:
        render_volume(view_snapshot)
    with core_tabs[3]:
        render_indicators(view_snapshot)

with st.expander("Advanced Options Evidence", expanded=False):
    render_option_intelligence(view_snapshot)
    option_tabs = st.tabs(
        [
            "Premium + OI + Volume Flow",
            "1m / 3m / 5m Movement",
            "OI Walls, Clusters & PCR",
            "Top-7 Weighted Contribution",
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

with st.expander("Download Reports", expanded=False):

    pdf_snapshot_key = st.session_state.get("audit_pdf_snapshot_id")
    if pdf_snapshot_key != snapshot.snapshot_id:
        st.session_state.pop("audit_pdf_bytes", None)
        st.session_state.pop("quick_pdf_bytes", None)
        st.session_state.audit_pdf_snapshot_id = snapshot.snapshot_id

    quick_col, full_col = st.columns(2)
    with quick_col:
        generate_quick_pdf = st.button(
            "Generate Quick Market Report", type="primary", width="stretch"
        )
        if generate_quick_pdf:
            try:
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
        generate_pdf = st.button("Generate Full Audit PDF", width="stretch")
        if generate_pdf:
            try:
                with st.spinner("Building full audit PDF from the current snapshot only..."):
                    st.session_state.audit_pdf_bytes = build_full_audit_pdf(
                        view_snapshot, previous_view_snapshot
                    )
                st.success("Full Audit PDF ready")
            except Exception as exc:
                st.error(f"Full Audit PDF not generated: {exc}")
        if st.session_state.get("audit_pdf_bytes"):
            st.download_button(
                "Download Full Audit PDF",
                data=st.session_state.audit_pdf_bytes,
                file_name=audit_pdf_filename(snapshot),
                mime="application/pdf",
                width="stretch",
            )

if os.getenv("NSL_SHOW_DEVELOPER_DATA", "").strip() == "1":
    with st.expander("Developer Raw Market Data (screen only)", expanded=False):
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
