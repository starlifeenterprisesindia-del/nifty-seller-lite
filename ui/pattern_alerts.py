import streamlit as st

from analysis.pattern_alerts import combined_signal_alert
from services.railway_live_client import RailwayDhanClient, post_railway_json


def process_combined_signal_alerts(snapshot, server_url="", server_key=""):
    """Send one deduplicated W/M+candle+Big-Player Telegram lane.

    This only reads evidence already present in the snapshot.  Keeping it outside
    the visual panel means closing/collapsing UI controls cannot silently disable
    Telegram delivery.
    """
    if not st.session_state.get("combined_signal_alerts_enabled", True):
        return None
    alert = combined_signal_alert(snapshot)
    if alert is None:
        return None
    ids = set(alert["pattern_ids"])
    key = f"combined_signal_seen_{snapshot.created_at.date()}"
    seen = set(st.session_state.get(key, []))
    if ids and ids.issubset(seen):
        return alert
    if server_url and server_key:
        try:
            post_railway_json(server_url, server_key, "/alerts/pattern", alert)
        except Exception:
            # Keep unsent ids out of local 'seen' so a later rerun can retry.
            st.session_state.combined_signal_alert_status = "Telegram delivery pending"
            return alert
    else:
        st.session_state.combined_signal_alert_status = "App only — Telegram gateway not configured"
        return alert
    st.session_state[key] = list(seen | ids)[-300:]
    st.session_state.combined_signal_alert_status = "Telegram synced"
    st.session_state.last_combined_signal_alert = alert
    return alert


def render_pattern_alerts(snapshot, server_url="", server_key=""):
    enabled = st.toggle(
        "Strong Candle / W-M / Big Player Alerts ON",
        value=st.session_state.get("combined_signal_alerts_enabled", True),
        key="combined_signal_alerts_enabled",
    )
    st.caption(
        "Ek hi alert lane: completed 3m W/M/candle + Big Player. Same signal refresh par repeat nahi hota; "
        "Big Player later confirm ho to sirf stronger upgrade alert aa sakta hai."
    )
    if not enabled:
        return
    alert = combined_signal_alert(snapshot)
    if alert is None:
        st.info("Abhi koi strong combined alert nahi — W/M, candle aur Big Player evidence monitor ho raha hai.")
    else:
        st.info(alert["message"])
    status = st.session_state.get("combined_signal_alert_status")
    if status:
        st.caption(str(status))

    st.markdown("**Alert Audit / History**")
    st.caption("Manual refresh only — isse live One Brain calculation ya broker feed par koi extra load nahi padta.")
    if not server_url or not server_key:
        st.caption("Railway gateway configured hone par delivery history yahan dikhegi.")
        return
    if st.button("Refresh Alert Audit", key="refresh_combined_alert_audit"):
        try:
            st.session_state.combined_alert_history = RailwayDhanClient(
                server_url, server_key, timeout_seconds=3
            )._post("/alerts/pattern-history", {"limit": 30}).get("alerts", [])
            st.session_state.pop("combined_alert_history_error", None)
        except Exception as exc:
            st.session_state.combined_alert_history_error = type(exc).__name__
    if st.session_state.get("combined_alert_history_error"):
        st.warning("Alert audit fetch nahi hua — live calculations unaffected hain.")
    rows = []
    for item in st.session_state.get("combined_alert_history", [])[:30]:
        bp = item.get("big_player") or {}
        rows.append({
            "Generated": str(item.get("generated_at") or item.get("captured_at") or "")[:19].replace("T", " "),
            "Type": item.get("kind", "—"),
            "Direction": item.get("direction", "—"),
            "NIFTY": item.get("nifty_ltp"),
            "BP": (f"{bp.get('stage','')} {bp.get('direction','')} {bp.get('score','—')}".strip() if bp else "—"),
            "Status": item.get("status", "—"),
            "Latency s": item.get("latency_seconds"),
            "Signal": item.get("names") or item.get("signature") or "—",
            "Conflict": "YES" if item.get("conflict") else "NO",
        })
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
