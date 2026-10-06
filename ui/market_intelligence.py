"""Display/alert layer for the shadow-only One Brain Market Intelligence Engine."""
from __future__ import annotations

from typing import Any

import streamlit as st

from services.railway_live_client import post_railway_json


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _path_text(path: dict[str, Any] | None) -> str:
    path = path or {}
    up = _num(path.get("up"))
    down = _num(path.get("down"))
    rng = _num(path.get("range"))
    best = max((("UP", up), ("DOWN", down), ("RANGE", rng)), key=lambda item: item[1])
    return f"{best[0]} {best[1]:.0f}"


def _direction_icon(direction: str) -> str:
    return "⬆️" if direction == "BULLISH" else "⬇️" if direction == "BEARISH" else "↔️"


def render_market_intelligence(snapshot: Any) -> None:
    """Compact front-screen intelligence card; never recalculates market evidence."""
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    if not item:
        st.info("🧠 Market Intelligence warming up — next authoritative snapshot ka wait.")
        return

    st.subheader("🧠 Market Intelligence")
    st.caption(
        "Shadow mode · existing One Brain ko 0% decision weight · no extra broker/API call · "
        "scores evidence/path scores hain, calibrated probability nahi."
    )

    direction = str(item.get("direction") or "MIXED")
    status = str(item.get("system_status") or "WAIT")
    impulse = str(item.get("impulse_state") or "NORMAL")
    alignment = str(item.get("one_brain_alignment") or "NO CLEAR ALIGNMENT")

    with st.container(border=True):
        a, b, c, d = st.columns(4)
        a.metric("MARKET STATE", str(item.get("market_state") or "UNCERTAIN"))
        b.metric("DIRECTION", f"{_direction_icon(direction)} {direction}")
        c.metric("MOVE POTENTIAL", str(item.get("move_potential") or "LOW"))
        d.metric("STATUS", status)

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Bull Pressure", f"{_num(item.get('bull_pressure')):.0f}/100")
        p2.metric("Bear Pressure", f"{_num(item.get('bear_pressure')):.0f}/100")
        p3.metric("Expansion Pressure", f"{_num(item.get('expansion_pressure')):.0f}/100")
        velocity = item.get("pressure_velocity")
        velocity_text = "—" if velocity is None else f"{_num(velocity):+.0f}"
        p4.metric("Pressure Velocity", velocity_text)

        expansion = max(0.0, min(100.0, _num(item.get("expansion_pressure"))))
        st.progress(expansion / 100.0, text=f"⚡ Impulse Pressure Meter — {impulse}")

        if alignment == "STRONG EVIDENCE ALIGNMENT":
            st.success(f"🔥 **{alignment}** · One Brain + Market Intelligence same {direction} side")
        elif alignment == "ALIGNMENT WATCH":
            st.info(f"✅ **{alignment}** · direction same; entry confirmation alag se check hogi")
        elif alignment == "SYSTEM CONFLICT":
            st.warning("⚠️ **SYSTEM CONFLICT** · One Brain aur Market Intelligence opposite hain — chase mat karo")
        elif "BUILD" in impulse or "EXPANSION" in impulse:
            st.info(f"⚡ **{impulse}**")

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Breakout Quality", f"{_num(item.get('breakout_quality')):.0f}/100")
        q2.metric("Reversal Quality", f"{_num(item.get('reversal_quality')):.0f}/100")
        q3.metric("Coverage", f"{_num(item.get('evidence_coverage')):.0f}%")
        q4.metric("Conflict", str(item.get("evidence_conflict") or "HIGH"))

        r1, r2, r3 = st.columns(3)
        r1.metric("5m Outlook", _path_text(item.get("path_5m")))
        r2.metric("15m Outlook", _path_text(item.get("path_15m")))
        r3.metric("30m Outlook", _path_text(item.get("path_30m")))

        st.caption(
            f"Volatility: {item.get('volatility_state', 'NORMAL')} · "
            f"Structure: {item.get('structure_event', 'NONE')} · "
            f"Institutional Pressure: {item.get('institutional_pressure', 'UNCLEAR')} · "
            f"Fake-move risk: {item.get('fake_move_risk', 'MEDIUM')}"
        )
        st.caption("Invalidation: " + str(item.get("invalidation") or "Structure resolution pending"))

    with st.expander("Market Intelligence — expert calculation detail", expanded=False):
        rows = []
        for expert in item.get("experts") or ():
            if not isinstance(expert, dict):
                continue
            rows.append({
                "Expert": expert.get("name"),
                "Available": "YES" if expert.get("available") else "NO VOTE",
                "Bull": expert.get("bullish"),
                "Bear": expert.get("bearish"),
                "Range": expert.get("range_score"),
                "Reliability": round(_num(expert.get("reliability")) * 100),
                "Freshness": round(_num(expert.get("freshness")) * 100),
                "Why": " | ".join(str(x) for x in (expert.get("reasons") or ())[:2]),
            })
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        for reason in item.get("reasons") or ():
            st.caption("• " + str(reason))
        for caution in item.get("cautions") or ():
            st.caption("Caution: " + str(caution))


def process_market_intelligence_alerts(snapshot: Any, server_url: str = "", server_key: str = "") -> list[dict[str, Any]]:
    """Deliver at most one highest-value OB-MIE alert per fresh snapshot.

    No market/API fetch is performed. Direction-unclear expansion warnings are also
    supported by the dedicated Railway delivery endpoint.
    """
    if not st.session_state.get("market_intelligence_alerts_enabled", True):
        return []
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    alerts = [row for row in (item.get("alerts") or ()) if isinstance(row, dict)]
    if not alerts:
        return []

    # One alert per snapshot prevents pressure-watch + strong-build + alignment from
    # ringing three times at the same moment. Conflict/failed-build take precedence;
    # then alignment; then strong build-up; then ordinary pressure watch.
    rank = {
        "SYSTEM_CONFLICT": 100,
        "BUILDUP_FAILED": 95,
        "ONE_BRAIN_ALIGNMENT": 90,
        "STRONG_BUILDUP": 80,
        "PRESSURE_ACCELERATION": 65,
        "PRESSURE_WATCH": 60,
    }
    row = max(alerts, key=lambda x: rank.get(str(x.get("kind") or ""), 0))
    kind = str(row.get("kind") or "MIE")
    direction = str(row.get("direction") or "MIXED").upper()
    score_band = int(_num(row.get("score")) // 5 * 5)
    day = getattr(snapshot, "created_at").date().isoformat()
    fingerprint = f"{kind}:{direction}:{score_band}"
    seen_key = f"market_intelligence_seen_{day}"
    seen = set(st.session_state.get(seen_key, []))
    if fingerprint in seen:
        return []

    payload = {
        "captured_at": row.get("captured_at") or getattr(snapshot, "created_at").isoformat(),
        "alert_id": fingerprint,
        "kind": kind,
        "title": row.get("title", kind),
        "direction": direction if direction in {"BULLISH", "BEARISH"} else "MIXED",
        "nifty_ltp": row.get("nifty_ltp"),
        "score": row.get("score"),
        "coverage": item.get("evidence_coverage"),
        "alignment": item.get("one_brain_alignment"),
        "message": (
            f"🧠 ONE BRAIN MARKET INTELLIGENCE\n"
            f"{row.get('title', kind)} — {direction}\n"
            f"{row.get('message', '')}\n"
            f"Status: {item.get('system_status', 'WATCH')} · "
            f"Expansion {_num(item.get('expansion_pressure')):.0f}/100 · "
            f"Coverage {_num(item.get('evidence_coverage')):.0f}%\n"
            f"Alignment: {item.get('one_brain_alignment', 'NO CLEAR ALIGNMENT')}\n"
            "Shadow intelligence only — automatic order nahi lagaya gaya."
        ),
    }
    if server_url and server_key:
        try:
            post_railway_json(server_url, server_key, "/alerts/market-intelligence", payload)
        except Exception:
            st.session_state.market_intelligence_alert_status = "Telegram delivery pending"
            return []
        seen.add(fingerprint)
        st.session_state[seen_key] = list(seen)[-300:]
        st.session_state.market_intelligence_alert_status = "Telegram synced"
        st.session_state.last_market_intelligence_alerts = [row]
        return [row]

    st.session_state.market_intelligence_alert_status = "App only — Telegram gateway not configured"
    st.session_state.last_market_intelligence_alerts = [row]
    return []

