import streamlit as st
from analysis.pattern_alerts import aligned_pattern_alert
from services.railway_live_client import post_railway_json


def render_pattern_alerts(snapshot, server_url="", server_key=""):
    enabled = st.toggle("Strong aligned candle / W-M alerts", value=True, key="strong_pattern_alerts")
    if not enabled:
        return
    alert = aligned_pattern_alert(snapshot)
    if alert is None:
        return
    st.info(alert["message"])
    ids = set(alert["pattern_ids"])
    key = f"pattern_seen_{snapshot.created_at.date()}"
    seen = set(st.session_state.get(key, []))
    if ids.issubset(seen):
        return
    if server_url and server_key:
        try:
            post_railway_json(server_url, server_key, "/alerts/pattern", alert)
        except Exception:
            st.caption("Pattern Telegram delivery pending; server update/connection required")
            return
    else:
        st.caption("App alert only; Telegram gateway not configured")
    st.toast(f"Strong aligned {alert['names']} — {alert['direction']}")
    st.session_state[key] = list(seen | ids)[-200:]
