"""Display/alert layer for the shadow-only One Brain Market Intelligence Engine.

The main screen intentionally shows one short market story.  Research/debug numbers stay
inside the expander.  This module never recalculates market evidence or fetches data.
"""
from __future__ import annotations

from datetime import datetime
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
    up = _num(path.get("up")); down = _num(path.get("down")); rng = _num(path.get("range"))
    best = max((("↑ UP", up), ("↓ DOWN", down), ("↔ RANGE", rng)), key=lambda item: item[1])
    return f"{best[0]} {best[1]:.0f}"


def _direction_icon(direction: str) -> str:
    return "↑" if direction == "BULLISH" else "↓" if direction == "BEARISH" else "↔"


def _zone_text(zone: dict[str, Any] | None) -> str:
    zone = zone or {}
    lower = zone.get("lower"); upper = zone.get("upper")
    if lower is None or upper is None:
        return "NO CLEAR TARGET"
    return f"{_num(lower):,.0f}–{_num(upper):,.0f}"


def _evidence_label(item: dict[str, Any]) -> str:
    coverage = _num(item.get("evidence_coverage"))
    conflict = str(item.get("evidence_conflict") or "HIGH")
    if coverage >= 75 and conflict == "LOW":
        return "STRONG"
    if coverage >= 58 and conflict != "HIGH":
        return "GOOD"
    if coverage >= 42:
        return "LIMITED"
    return "WEAK"


def _simple_market_story(item: dict[str, Any]) -> str:
    direction = str(item.get("direction") or "MIXED")
    expansion = _num(item.get("expansion_pressure"))
    regime = str(item.get("market_state") or "UNCERTAIN")
    if direction == "BULLISH" and expansion >= 52:
        return "BULLISH BUILD-UP ↑"
    if direction == "BEARISH" and expansion >= 52:
        return "BEARISH BUILD-UP ↓"
    if direction == "BULLISH":
        return f"{regime} · BULLISH BIAS ↑"
    if direction == "BEARISH":
        return f"{regime} · BEARISH BIAS ↓"
    return f"{regime} · MIXED"


def _simple_move(item: dict[str, Any]) -> str:
    impulse = str(item.get("impulse_state") or "NORMAL")
    if "EXPANSION" in impulse or "HIGH PRESSURE" in impulse:
        return "EXPANSION PRESSURE HIGH ⚡"
    if "STRONG" in impulse:
        return "STRONG MOVE FORMING ⚡"
    if "MOVE BUILDING" in impulse:
        return "MOVE BUILDING ⚡"
    if "PRESSURE" in impulse:
        return "PRESSURE FORMING"
    return "NO STRONG BUILD-UP"


def render_move_radar(snapshot: Any) -> None:
    """Always-visible precaution banner driven only by the precomputed MI result."""
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    if not item:
        return
    radar = item.get("move_radar") if isinstance(item.get("move_radar"), dict) else {}
    state = str(radar.get("state") or "NORMAL")
    visual = str(radar.get("visual") or "GREY").upper()
    blink = bool(radar.get("blink"))
    message = str(radar.get("message") or "No abnormal move build-up")
    liquidity = item.get("liquidity") if isinstance(item.get("liquidity"), dict) else {}
    target = _zone_text(liquidity.get("primary_zone") if isinstance(liquidity, dict) else None)
    pressure = _num(item.get("expansion_pressure"))

    palette = {
        "GREEN": ("#0b7a3e", "#e9f8ef", "🟢"),
        "RED": ("#b42318", "#fff0ef", "🔴"),
        "AMBER": ("#b54708", "#fff7e6", "🟠"),
        "GREY": ("#475467", "#f2f4f7", "⚪"),
    }
    border, background, dot = palette.get(visual, palette["GREY"])
    animation = "animation: obmiePulse 1.25s ease-in-out infinite;" if blink else ""
    extra = "" if target == "NO CLEAR TARGET" else f" · Target {target}"
    st.markdown(
        f"""
<style>
@keyframes obmiePulse {{
  0% {{ box-shadow: 0 0 0 0 rgba(180, 35, 24, 0.00); opacity: 1; }}
  50% {{ box-shadow: 0 0 0 7px rgba(180, 35, 24, 0.10); opacity: .78; }}
  100% {{ box-shadow: 0 0 0 0 rgba(180, 35, 24, 0.00); opacity: 1; }}
}}
.obmie-radar {{
  border: 2px solid {border}; background: {background}; border-radius: 12px;
  padding: 10px 14px; margin: 4px 0 10px 0; {animation}
}}
.obmie-radar-title {{ font-weight: 800; font-size: 1.03rem; color: {border}; }}
.obmie-radar-sub {{ font-size: .88rem; margin-top: 2px; }}
</style>
<div class="obmie-radar">
  <div class="obmie-radar-title">{dot} MOVE RADAR · {state}</div>
  <div class="obmie-radar-sub">{message} · Pressure {pressure:.0f}/100{extra}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_market_intelligence(snapshot: Any) -> None:
    """Simple user-facing story; detailed calculations remain hidden by default."""
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    if not item:
        st.info("🧠 Market Intelligence warming up — next authoritative snapshot ka wait.")
        return

    direction = str(item.get("direction") or "MIXED")
    liquidity = item.get("liquidity") if isinstance(item.get("liquidity"), dict) else {}
    zone = liquidity.get("primary_zone") if isinstance(liquidity, dict) else None
    target = _zone_text(zone if isinstance(zone, dict) else None)
    hunt_bias = str(liquidity.get("hunt_bias") or "UNCLEAR") if isinstance(liquidity, dict) else "UNCLEAR"
    hunt_strength = str(liquidity.get("hunt_strength") or "LOW") if isinstance(liquidity, dict) else "LOW"
    sweep_outcome = str(liquidity.get("sweep_outcome") or "UNCLEAR") if isinstance(liquidity, dict) else "UNCLEAR"
    reach = str(liquidity.get("reach_state") or "UNCLEAR") if isinstance(liquidity, dict) else "UNCLEAR"
    alignment = str(item.get("one_brain_alignment") or "NO CLEAR ALIGNMENT")
    pressure = _num(item.get("expansion_pressure"))
    velocity = item.get("pressure_velocity")
    velocity_marker = "↑↑" if velocity is not None and _num(velocity) >= 14 else "↑" if velocity is not None and _num(velocity) >= 5 else ""

    st.subheader("🧠 Market Intelligence")
    with st.container(border=True):
        a, b, c, d = st.columns(4)
        a.metric("MARKET", _simple_market_story(item))
        b.metric("MOVE", _simple_move(item))
        c.metric("LIQUIDITY TARGET", target)
        d.metric("HUNT", f"{hunt_bias} · {hunt_strength}")

        e, f, g, h = st.columns(4)
        e.metric("AFTER TARGET", sweep_outcome)
        f.metric("MOVE PRESSURE", f"{pressure:.0f}/100 {velocity_marker}".strip())
        g.metric("ONE BRAIN", "✅ ALIGNED" if alignment in {"STRONG EVIDENCE ALIGNMENT", "ALIGNMENT WATCH", "DIRECTION ALIGNED"} else "⚠ CONFLICT" if alignment == "SYSTEM CONFLICT" else "— NO CLEAR VIEW")
        h.metric("STATUS", str(item.get("system_status") or "WAIT"))

        if alignment == "STRONG EVIDENCE ALIGNMENT":
            st.success(f"🔥 STRONG EVIDENCE ALIGNMENT · {_direction_icon(direction)} {direction} · Target {target}")
        elif alignment == "SYSTEM CONFLICT":
            st.warning("⚠️ SYSTEM CONFLICT · One Brain aur Market Intelligence opposite hain — chase mat karo")
        elif str(liquidity.get("sweep_state") or "NONE") not in {"NONE", "UPSIDE TARGET TESTING", "DOWNSIDE TARGET TESTING"}:
            st.info(f"🔥 {liquidity.get('sweep_state')} · {sweep_outcome}")

        p1, p2, p3 = st.columns(3)
        p1.metric("5m", _path_text(item.get("path_5m")))
        p2.metric("15m", _path_text(item.get("path_15m")))
        p3.metric("30m", _path_text(item.get("path_30m")))
        st.caption(
            f"Reach: {reach} · Evidence: {_evidence_label(item)} · "
            f"Fake-move risk: {item.get('fake_move_risk', 'MEDIUM')} · "
            "scores evidence hain, calibrated probabilities nahi."
        )

    with st.expander("Advanced / Research detail", expanded=False):
        x1, x2, x3, x4 = st.columns(4)
        x1.metric("Bull Pressure", f"{_num(item.get('bull_pressure')):.0f}")
        x2.metric("Bear Pressure", f"{_num(item.get('bear_pressure')):.0f}")
        x3.metric("Expansion", f"{pressure:.0f}")
        x4.metric("Velocity", "—" if velocity is None else f"{_num(velocity):+.0f}")
        y1, y2, y3, y4 = st.columns(4)
        y1.metric("Breakout Quality", f"{_num(item.get('breakout_quality')):.0f}")
        y2.metric("Reversal Quality", f"{_num(item.get('reversal_quality')):.0f}")
        y3.metric("Coverage", f"{_num(item.get('evidence_coverage')):.0f}%")
        y4.metric("Conflict", str(item.get("evidence_conflict") or "HIGH"))

        if isinstance(liquidity, dict):
            st.markdown("**Liquidity / Stop-Cascade research**")
            l1, l2, l3, l4 = st.columns(4)
            l1.metric("Upside Hunt", f"{_num(liquidity.get('upside_hunt_pressure')):.0f}")
            l2.metric("Downside Hunt", f"{_num(liquidity.get('downside_hunt_pressure')):.0f}")
            l3.metric("Reach Score", f"{_num(liquidity.get('reach_score')):.0f}")
            l4.metric("Path Clearance", f"{_num(liquidity.get('path_clearance')):.0f}")
            ext = liquidity.get("extension_zone") if isinstance(liquidity.get("extension_zone"), dict) else None
            st.caption(
                f"Primary: {target} · Extension: {_zone_text(ext)} · "
                f"Sweep: {liquidity.get('sweep_state', 'NONE')} · Outcome: {sweep_outcome}"
            )
            for reason in liquidity.get("reasons") or ():
                st.caption("• " + str(reason))

        rows = []
        for expert in item.get("experts") or ():
            if not isinstance(expert, dict):
                continue
            rows.append({
                "Expert": expert.get("name"),
                "Available": "YES" if expert.get("available") else "NO VOTE",
                "Bull": expert.get("bullish"), "Bear": expert.get("bearish"), "Range": expert.get("range_score"),
                "Reliability": round(_num(expert.get("reliability")) * 100),
                "Freshness": round(_num(expert.get("freshness")) * 100),
                "Why": " | ".join(str(x) for x in (expert.get("reasons") or ())[:2]),
            })
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        for caution in item.get("cautions") or ():
            st.caption("Caution: " + str(caution))
        if isinstance(liquidity, dict):
            for caution in liquidity.get("cautions") or ():
                st.caption("Liquidity caution: " + str(caution))


def process_market_intelligence_alerts(snapshot: Any, server_url: str = "", server_key: str = "") -> list[dict[str, Any]]:
    """Deliver at most one highest-value alert per fresh snapshot, with cooldown."""
    if not st.session_state.get("market_intelligence_alerts_enabled", True):
        return []
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    alerts = [row for row in (item.get("alerts") or ()) if isinstance(row, dict)]
    if not alerts:
        return []

    rank = {
        "SYSTEM_CONFLICT": 100,
        "BUILDUP_FAILED": 98,
        "LIQUIDITY_SWEEP": 96,
        "ONE_BRAIN_ALIGNMENT": 92,
        "STRONG_BUILDUP": 88,
        "LIQUIDITY_HUNT_WATCH": 82,
        "PRESSURE_ACCELERATION": 76,
        "BIG_MOVE_PRECAUTION": 72,
        "PRESSURE_WATCH": 68,
    }
    row = max(alerts, key=lambda x: rank.get(str(x.get("kind") or ""), 0))
    kind = str(row.get("kind") or "MIE")
    direction = str(row.get("direction") or "MIXED").upper()
    score_band = int(_num(row.get("score")) // 5 * 5)
    created_at = getattr(snapshot, "created_at")
    day = created_at.date().isoformat()
    target = row.get("liquidity_target") if isinstance(row.get("liquidity_target"), dict) else None
    target_key = _zone_text(target) if target else "none"
    fingerprint = f"{kind}:{direction}:{score_band}:{target_key}"
    seen_key = f"market_intelligence_seen_{day}"
    seen = set(st.session_state.get(seen_key, []))
    if fingerprint in seen:
        return []

    # Routine pre-alerts get a 90-second cooldown. State changes/conflicts/sweeps can
    # bypass it because they represent materially new information.
    critical = kind in {"SYSTEM_CONFLICT", "BUILDUP_FAILED", "LIQUIDITY_SWEEP", "ONE_BRAIN_ALIGNMENT"}
    last_key = "market_intelligence_last_routine_alert_at"
    previous_ts = _num(st.session_state.get(last_key), 0.0)
    current_ts = created_at.timestamp()
    if not critical and previous_ts and current_ts - previous_ts < 90:
        return []

    liquidity = item.get("liquidity") if isinstance(item.get("liquidity"), dict) else {}
    zone = liquidity.get("primary_zone") if isinstance(liquidity.get("primary_zone"), dict) else None
    target_text = _zone_text(zone)
    target_line = "" if target_text == "NO CLEAR TARGET" else f"\nLiquidity target: {target_text} · Hunt {liquidity.get('hunt_bias', 'UNCLEAR')} {liquidity.get('hunt_strength', '')}"
    sweep_line = "" if str(liquidity.get("sweep_state") or "NONE") == "NONE" else f"\nSweep: {liquidity.get('sweep_state')} · {liquidity.get('sweep_outcome', 'UNCLEAR')}"
    payload = {
        "captured_at": row.get("captured_at") or created_at.isoformat(),
        "alert_id": fingerprint,
        "kind": kind,
        "title": row.get("title", kind),
        "direction": direction if direction in {"BULLISH", "BEARISH"} else "MIXED",
        "nifty_ltp": row.get("nifty_ltp"),
        "score": row.get("score"),
        "coverage": item.get("evidence_coverage"),
        "alignment": item.get("one_brain_alignment"),
        "liquidity_bias": liquidity.get("hunt_bias"),
        "liquidity_target": zone,
        "sweep_outcome": liquidity.get("sweep_outcome"),
        "message": (
            f"🧠 ONE BRAIN MARKET INTELLIGENCE\n"
            f"{row.get('title', kind)} — {direction}\n"
            f"{row.get('message', '')}\n"
            f"Status: {item.get('system_status', 'WATCH')} · Move pressure {_num(item.get('expansion_pressure')):.0f}/100 · Coverage {_num(item.get('evidence_coverage')):.0f}%"
            f"{target_line}{sweep_line}\n"
            f"One Brain: {item.get('one_brain_alignment', 'NO CLEAR ALIGNMENT')}\n"
            "Precaution/shadow intelligence — automatic order nahi lagaya gaya."
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
        if not critical:
            st.session_state[last_key] = current_ts
        st.session_state.market_intelligence_alert_status = "Telegram synced"
        st.session_state.last_market_intelligence_alerts = [row]
        return [row]

    seen.add(fingerprint)
    st.session_state[seen_key] = list(seen)[-300:]
    if not critical:
        st.session_state[last_key] = current_ts
    st.session_state.market_intelligence_alert_status = "App only — Telegram gateway not configured"
    st.session_state.last_market_intelligence_alerts = [row]
    return []
