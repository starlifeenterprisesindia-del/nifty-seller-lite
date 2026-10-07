import streamlit as st

from analysis.pattern_alerts import combined_signal_alert
from analysis.alert_audit import filter_alert_history, summarize_alert_history
from services.railway_live_client import RailwayDhanClient, post_railway_json


def _persistent_bool_toggle(label: str, key: str, default: bool) -> bool:
    """Keep alert preference even when this optional panel is temporarily hidden."""
    widget_key = f"__widget__{key}"
    if key not in st.session_state:
        st.session_state[key] = bool(default)
    if widget_key not in st.session_state:
        st.session_state[widget_key] = bool(st.session_state.get(key, default))
    value = bool(st.toggle(label, key=widget_key))
    st.session_state[key] = value
    return value


def process_combined_signal_alerts(snapshot, server_url="", server_key=""):
    """Background confirmation lane with Smart-Alert suppression.

    W/M, candle and Big-Player evidence still calculates/records normally.  When
    Market Intelligence alerts are enabled, Big-Player-only events stay background
    evidence and same-direction confirmations are suppressed for the shared 3-minute
    setup cooldown.  This changes delivery only, never One-Brain calculations.
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

    direction = str(alert.get("direction") or "MIXED").upper()
    pattern_ids = [str(x) for x in alert.get("pattern_ids") or []]
    big_only = bool(pattern_ids) and all(x.startswith("BIG:") for x in pattern_ids)
    mi_enabled = bool(st.session_state.get("market_intelligence_alerts_enabled", True))

    # In Smart mode, Big Player EARLY/CONFIRMED by itself is evidence, not another
    # Telegram notification.  It is merged into the next meaningful MI story.
    if mi_enabled and big_only and not bool(alert.get("conflict")):
        st.session_state.combined_signal_alert_status = "Background evidence — Big Player merged into Smart Alert"
        return alert

    current_ts = snapshot.created_at.timestamp()
    last_ts = float(st.session_state.get("smart_alert_last_sent_at") or 0.0)
    last_dir = str(st.session_state.get("smart_alert_last_direction") or "MIXED").upper()
    age = current_ts - last_ts if last_ts else 9999.0
    direction_flip = direction in {"BULLISH", "BEARISH"} and last_dir in {"BULLISH", "BEARISH"} and direction != last_dir

    # If Market Intelligence just sent the same setup, do not send a second candle/
    # Big-Player message.  Opposite-direction conflict/flip is still allowed.
    if mi_enabled and age < 180 and direction == last_dir and not bool(alert.get("conflict")):
        st.session_state.combined_signal_alert_status = "Smart cooldown — confirmation recorded, Telegram suppressed"
        return alert
    if mi_enabled and age < 60 and not direction_flip and not bool(alert.get("conflict")):
        return alert

    if server_url and server_key:
        try:
            post_railway_json(server_url, server_key, "/alerts/pattern", alert)
        except Exception:
            st.session_state.combined_signal_alert_status = "Telegram delivery pending"
            return alert
    else:
        st.session_state.combined_signal_alert_status = "App only — Telegram gateway not configured"
        return alert

    st.session_state[key] = list(seen | ids)[-300:]
    st.session_state.combined_signal_alert_status = "Telegram synced · Smart Alert mode"
    st.session_state.last_combined_signal_alert = alert
    st.session_state.smart_alert_last_sent_at = current_ts
    st.session_state.smart_alert_last_direction = direction
    st.session_state.smart_alert_last_kind = "PATTERN_CONFIRMATION"
    st.session_state.smart_alert_last_rank = 84
    st.session_state.smart_alert_last_score = 0.0
    return alert

def render_pattern_alerts(snapshot, server_url="", server_key=""):
    enabled = _persistent_bool_toggle(
        "Strong Candle / W-M / Big Player Alerts ON",
        "combined_signal_alerts_enabled",
        True,
    )
    st.caption(
        "Smart Alert mode: Big Player-only events background evidence rahenge; completed 3m confirmation "
        "sirf tab Telegram jayega jab recent Market Intelligence alert same setup ko already cover na kare."
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
    history = st.session_state.get("combined_alert_history", [])[:30]
    if history:
        summary = summarize_alert_history(history)
        a, b, c, d = st.columns(4)
        a.metric(
            "Delivery",
            f"{summary['delivery_rate_pct']:.1f}%" if summary['delivery_rate_pct'] is not None else "—",
            help="Fetched audit rows me SENT alerts ka percentage.",
        )
        b.metric(
            "Median latency",
            f"{summary['median_latency_seconds']:.2f}s" if summary['median_latency_seconds'] is not None else "—",
            help="Alert generated se Telegram delivery complete hone tak median time.",
        )
        c.metric(
            "P95 latency",
            f"{summary['p95_latency_seconds']:.2f}s" if summary['p95_latency_seconds'] is not None else "—",
            help="95% recorded deliveries is latency ke andar hain (sample dependent).",
        )
        d.metric(
            "Failed",
            summary['failed'],
            help="Delivery failures recorded in the fetched audit window.",
        )
        st.caption(
            f"≤3s: {summary['fast_count']} · >3s: {summary['slow_count']} · "
            f"Conflict rate: {summary['conflict_rate_pct'] if summary['conflict_rate_pct'] is not None else '—'}%"
        )

        f1, f2 = st.columns(2)
        status_filter = f1.selectbox(
            "Status filter", ("ALL", "SENT", "FAILED"), key="alert_audit_status_filter",
            help="Display-only filter; server history ko change nahi karta.",
        )
        direction_filter = f2.selectbox(
            "Direction filter", ("ALL", "BULLISH", "BEARISH"), key="alert_audit_direction_filter",
            help="Display-only filter.",
        )
        filtered = filter_alert_history(history, status=status_filter, direction=direction_filter)
    else:
        filtered = []

    rows = []
    for item in filtered:
        bp = item.get("big_player") or {}
        latency = item.get("latency_seconds")
        try:
            latency_flag = "SLOW" if float(latency) > 3.0 else "OK"
        except (TypeError, ValueError):
            latency_flag = "—"
        rows.append({
            "Generated": str(item.get("generated_at") or item.get("captured_at") or "")[:19].replace("T", " "),
            "Type": item.get("kind", "—"),
            "Direction": item.get("direction", "—"),
            "NIFTY": item.get("nifty_ltp"),
            "BP": (f"{bp.get('stage','')} {bp.get('direction','')} {bp.get('score','—')}".strip() if bp else "—"),
            "Status": item.get("status", "—"),
            "Latency s": latency,
            "Latency": latency_flag,
            "Signal": item.get("names") or item.get("signature") or "—",
            "Conflict": "YES" if item.get("conflict") else "NO",
        })
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    elif history:
        st.info("Selected filters me koi alert nahi mila.")
