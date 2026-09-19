from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from analysis.ai_move_tracker import (
    apply_completed_3m_close,
    barrier_status,
    new_prediction,
    update_prediction,
)
from config import CONFIG
from services.railway_live_client import fetch_railway_live_state, post_railway_json

IST = ZoneInfo("Asia/Kolkata")


def _status_icon(status: str) -> str:
    return {
        "ON TRACK": "🟢",
        "WEAKENING": "🟡",
        "STALLED": "🟠",
        "INVALIDATED": "🔴",
        "TARGET MET": "✅",
    }.get(status, "⚪")


def _maybe_start(snapshot) -> dict | None:
    current = st.session_state.get("ai_move_tracker_state")
    candidate = new_prediction(snapshot)
    if candidate is None:
        return current
    if not isinstance(current, dict):
        st.session_state.ai_move_tracker_state = candidate
        return candidate
    if current.get("closed"):
        # A fragment rerun can reuse the same frozen snapshot object. Never restart
        # a finished thesis from that old snapshot; wait for a genuinely new full
        # snapshot id before opening the next prediction.
        if current.get("snapshot_id") == candidate.get("snapshot_id"):
            return current
        st.session_state.ai_move_tracker_state = candidate
        return candidate
    # A genuine direction flip supersedes the old thesis. Same-direction full
    # snapshots do not reset the clock/barrier: that is the anti-goalpost rule.
    if current.get("direction") != candidate.get("direction"):
        st.session_state.ai_move_tracker_state = candidate
        return candidate
    return current


def _support_note(snapshot, state: dict) -> str:
    """Short explanation using only the already-built full snapshot."""
    simple = (getattr(snapshot, "metadata", {}) or {}).get("simple_brain") or {}
    current = str(simple.get("direction") or "MIXED").upper()
    expected = str(state.get("direction") or "").upper()
    blocks = simple.get("blocks") if isinstance(simple.get("blocks"), dict) else {}
    options = blocks.get("options") if isinstance(blocks.get("options"), dict) else {}
    participation = blocks.get("participation") if isinstance(blocks.get("participation"), dict) else {}
    notes: list[str] = []
    if current == expected:
        notes.append("One-Brain abhi bhi same direction support kar raha hai")
    elif current in {"UP", "DOWN"} and current != expected:
        notes.append("One-Brain direction flip hui — thesis weak")
    else:
        notes.append("One-Brain abhi mixed/transition hai")
    if options.get("available") is False:
        notes.append("Options flow unavailable")
    if participation.get("available") is False:
        notes.append("Participation unavailable")
    if state.get("barrier_confirmed"):
        notes.append("locked barrier ka 3m break confirm")
    return " · ".join(notes[:3])


@st.fragment(run_every=CONFIG.ai_move_tracker_interval_seconds)
def render_ai_move_tracker(snapshot, live_server_url: str, live_server_api_key: str) -> None:
    """Render/update one lightweight tracker using only Railway's cached live LTP."""
    if not bool(getattr(getattr(snapshot, "market_session", None), "is_live", False)):
        return
    state = _maybe_start(snapshot)
    if not isinstance(state, dict):
        return

    # A normal full snapshot already contains the latest *completed* 3m close.
    # Reuse it to confirm the locked barrier; no candle/API calculation is added.
    indicators = getattr(snapshot, "indicators", None)
    three_minute = getattr(indicators, "three_minute", None)
    completed_close = getattr(three_minute, "close", None) if three_minute is not None else None
    state = apply_completed_3m_close(
        state, completed_close=completed_close, observed_at=getattr(snapshot, "created_at", None)
    )
    st.session_state.ai_move_tracker_state = state

    # No broker/options/indicator calculation here. /live is the already-running
    # Railway WebSocket cache and this poll happens only every three minutes.
    if live_server_url and live_server_api_key and not state.get("closed"):
        try:
            remote = fetch_railway_live_state(live_server_url, live_server_api_key, timeout_seconds=2.0)
            if remote.connected and remote.nifty_ltp is not None:
                now = datetime.now(IST)
                state = update_prediction(state, current_price=remote.nifty_ltp, at=now)
                st.session_state.ai_move_tracker_state = state
                # One tiny Railway-only write every 3 minutes. No Dhan request and
                # no full history/report read; this preserves tracker outcomes for
                # later audit/calibration even if Streamlit restarts.
                try:
                    post_railway_json(
                        live_server_url,
                        live_server_api_key,
                        "/day-memory",
                        {"tracker": state, "report": False},
                        timeout_seconds=2.0,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    price = float(state.get("current_price") or state.get("start_price") or 0.0)
    bstate, bnote = barrier_status(state, price)
    status = str(state.get("status") or "STALLED")
    move = float(state.get("move_points") or 0.0)
    elapsed = float(state.get("elapsed_minutes") or 0.0)
    with st.container(border=True):
        st.markdown("### 🧠 AI Move Check")
        st.markdown(
            f"**{state.get('direction')} {float(state.get('confidence') or 0):.0f}% · "
            f"{_status_icon(status)} {status} · {move:+.1f} pts · {elapsed:.0f} min**"
        )
        barrier = state.get("barrier") if isinstance(state.get("barrier"), dict) else None
        if barrier:
            distance = (float(barrier['lower']) - price) if state.get('direction') == 'UP' else (price - float(barrier['upper']))
            distance = max(0.0, distance)
            st.caption(
                f"🧱 Barrier: {barrier.get('label')} {float(barrier['lower']):,.0f}–{float(barrier['upper']):,.0f} · "
                f"{bstate} · {distance:.0f} pts"
            )
            st.caption(f"Next: {bnote}")
        st.caption("Why: " + _support_note(snapshot, state))
        with st.expander("Move details", expanded=False):
            st.write(
                f"Start {float(state.get('start_price') or 0):,.2f} · Current {price:,.2f} · "
                f"MFE +{float(state.get('mfe_points') or 0):.1f} · MAE -{float(state.get('mae_points') or 0):.1f}"
            )
            inv = state.get("invalidation_price")
            target = state.get("target_price")
            st.caption(
                f"Target/first objective: {float(target):,.2f}" if target is not None else "Target: —"
            )
            if inv is not None:
                st.caption(f"Structural invalidation: {float(inv):,.2f}")
            checkpoints = state.get("checkpoints") or {}
            if checkpoints:
                st.caption(" · ".join(f"{k}: {float(v):+.1f} pts" for k, v in checkpoints.items()))
