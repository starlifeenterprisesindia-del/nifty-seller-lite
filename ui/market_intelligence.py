"""Display/alert layer for the shadow-only One Brain Market Intelligence Engine.

The main screen intentionally shows one short market story.  Research/debug numbers stay
inside the expander.  This module never recalculates market evidence or fetches data.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from html import escape

import streamlit as st

from services.railway_live_client import post_railway_json
from analysis.pattern_alerts import combined_signal_alert


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
    early = str(item.get("early_direction") or direction)
    context = str(item.get("direction_context") or "MIXED")
    expansion = _num(item.get("expansion_pressure"))
    regime = str(item.get("market_state") or "UNCERTAIN")
    arrow = "↑" if early == "BULLISH" else "↓" if early == "BEARISH" else "↔"
    if context == "REVERSAL WATCH" and early in {"BULLISH", "BEARISH"}:
        return f"{early} REVERSAL WATCH {arrow}"
    if context == "COUNTERTREND IMPULSE" and early in {"BULLISH", "BEARISH"}:
        return f"{early} COUNTERTREND {arrow}"
    if context == "BREAKOUT WATCH" and early in {"BULLISH", "BEARISH"}:
        return f"{early} BREAKOUT WATCH {arrow}"
    if context == "EARLY PRESSURE" and early in {"BULLISH", "BEARISH"}:
        return f"{early} EARLY PRESSURE {arrow}"
    if direction == "MIXED" and early in {"BULLISH", "BEARISH"} and expansion >= 50:
        return f"{early} EARLY PRESSURE {arrow}"
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


def _story_value(value: Any) -> str:
    return escape(str(value or "—"))


def _render_story_grid(items: list[tuple[str, str]], *, outlook: bool = False) -> None:
    """Responsive, wrapping MI cards; never changes calculations."""
    cls = "obmie-outlook-grid" if outlook else "obmie-story-grid"
    cells = "".join(
        f'<div class="obmie-story-cell"><div class="obmie-label">{escape(label)}</div>'
        f'<div class="obmie-value">{_story_value(value)}</div></div>'
        for label, value in items
    )
    st.markdown(
        f"""
<style>
.obmie-story-grid, .obmie-outlook-grid {{
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 18px; width: 100%; margin: 2px 0 8px 0;
}}
.obmie-outlook-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 12px; }}
.obmie-story-cell {{ min-width: 0; padding: 2px 0; }}
.obmie-label {{
  font-size: clamp(.66rem, .74vw, .78rem); line-height: 1.2; font-weight: 800;
  letter-spacing: .02em; opacity: .82; margin-bottom: 5px;
}}
.obmie-value {{
  font-size: clamp(1.03rem, 1.48vw, 1.42rem); line-height: 1.14; font-weight: 720;
  white-space: normal; overflow-wrap: anywhere; word-break: normal; min-height: 1.3em;
}}
.obmie-outlook-grid .obmie-value {{ font-size: clamp(.98rem, 1.32vw, 1.28rem); }}
@media (max-width: 900px) {{
  .obmie-story-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; }}
  .obmie-outlook-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 10px; }}
  .obmie-value {{ font-size: clamp(1rem, 3.7vw, 1.26rem); }}
}}
@media (max-width: 560px) {{
  .obmie-story-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px 10px; }}
  .obmie-outlook-grid {{ grid-template-columns: 1fr; gap: 6px; }}
  .obmie-label {{ font-size: .66rem; }}
  .obmie-value {{ font-size: 1.02rem; line-height: 1.15; }}
}}
</style>
<div class="{cls}">{cells}</div>
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
    one_brain_text = (
        "✅ ALIGNED" if alignment in {"STRONG EVIDENCE ALIGNMENT", "ALIGNMENT WATCH", "DIRECTION ALIGNED"}
        else "⚠ CONFLICT" if alignment == "SYSTEM CONFLICT" else "— NO CLEAR VIEW"
    )

    st.subheader("🧠 Market Intelligence")
    with st.container(border=True):
        _render_story_grid([
            ("MARKET", _simple_market_story(item)),
            ("MOVE", _simple_move(item)),
            ("LIQUIDITY TARGET", target),
            ("HUNT", f"{hunt_bias} · {hunt_strength}"),
            ("AFTER TARGET", sweep_outcome),
            ("MOVE PRESSURE", f"{pressure:.0f}/100 {velocity_marker}".strip()),
            ("ONE BRAIN", one_brain_text),
            ("STATUS", str(item.get("system_status") or "WAIT")),
        ])

        if alignment == "STRONG EVIDENCE ALIGNMENT":
            st.success(f"🔥 STRONG EVIDENCE ALIGNMENT · {_direction_icon(direction)} {direction} · Target {target}")
        elif alignment == "SYSTEM CONFLICT":
            st.warning("⚠️ SYSTEM CONFLICT · One Brain aur Market Intelligence opposite hain — chase mat karo")
        elif str(liquidity.get("sweep_state") or "NONE") not in {"NONE", "UPSIDE TARGET TESTING", "DOWNSIDE TARGET TESTING"}:
            st.info(f"🔥 {liquidity.get('sweep_state')} · {sweep_outcome}")

        _render_story_grid([
            ("5m", _path_text(item.get("path_5m"))),
            ("15m", _path_text(item.get("path_15m"))),
            ("30m", _path_text(item.get("path_30m"))),
        ], outlook=True)
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
        st.caption(
            f"Early direction: {item.get('early_direction', item.get('direction', 'MIXED'))} · "
            f"15m context: {item.get('dominant_context', 'MIXED')} · "
            f"Context: {item.get('direction_context', 'MIXED')} · "
            f"Fast families: {int(_num(item.get('fast_confirmation_count')))}"
        )

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
                f"Sweep: {liquidity.get('sweep_state', 'NONE')} · Outcome: {sweep_outcome} · "
                f"Acceptance: {liquidity.get('acceptance_state', 'NONE')}"
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
                "Why": "; ".join(str(v) for v in (expert.get("reasons") or ())[:3]),
            })
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        for caution in item.get("cautions") or ():
            st.caption("Caution: " + str(caution))
        if isinstance(liquidity, dict):
            for caution in liquidity.get("cautions") or ():
                st.caption("Liquidity caution: " + str(caution))

def process_market_intelligence_alerts(snapshot: Any, server_url: str = "", server_key: str = "") -> list[dict[str, Any]]:
    """Smart Telegram controller for OB-MIE.

    All calculations/events remain available in snapshot/journal.  Delivery is intentionally
    selective: one meaningful alert per setup, with material-change overrides only.
    """
    if not st.session_state.get("market_intelligence_alerts_enabled", True):
        return []
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    alerts = [row for row in (item.get("alerts") or ()) if isinstance(row, dict)]
    if not alerts:
        return []

    expansion = _num(item.get("expansion_pressure"))
    coverage = _num(item.get("evidence_coverage"))
    alignment = str(item.get("one_brain_alignment") or "NO CLEAR ALIGNMENT")

    # Mixed-direction acceleration is useful on-screen but usually too noisy for Telegram.
    filtered = []
    for candidate in alerts:
        kind0 = str(candidate.get("kind") or "")
        dir0 = str(candidate.get("direction") or "MIXED").upper()
        if dir0 == "MIXED" and kind0 in {"PRESSURE_ACCELERATION", "PRESSURE_WATCH"} and expansion < 75:
            continue
        if kind0 == "ONE_BRAIN_ALIGNMENT" and alignment == "ALIGNMENT WATCH" and (expansion < 60 or coverage < 70):
            continue
        filtered.append(candidate)
    if not filtered:
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
    row = max(filtered, key=lambda x: rank.get(str(x.get("kind") or ""), 0))
    kind = str(row.get("kind") or "MIE")
    direction = str(row.get("direction") or "MIXED").upper()
    current_rank = rank.get(kind, 0)
    score = _num(row.get("score"), expansion)
    score_band = int(score // 10 * 10)
    created_at = getattr(snapshot, "created_at")
    day = created_at.date().isoformat()
    target = row.get("liquidity_target") if isinstance(row.get("liquidity_target"), dict) else None
    target_key = _zone_text(target) if target else "none"
    fingerprint = f"{kind}:{direction}:{score_band}:{target_key}"
    seen_key = f"market_intelligence_seen_{day}"
    seen = set(st.session_state.get(seen_key, []))
    if fingerprint in seen:
        return []

    current_ts = created_at.timestamp()
    last_ts = _num(st.session_state.get("smart_alert_last_sent_at"), 0.0)
    last_dir = str(st.session_state.get("smart_alert_last_direction") or "MIXED").upper()
    last_kind = str(st.session_state.get("smart_alert_last_kind") or "")
    last_rank = int(_num(st.session_state.get("smart_alert_last_rank"), 0))
    last_score = _num(st.session_state.get("smart_alert_last_score"), 0.0)
    age = current_ts - last_ts if last_ts else 9999.0

    # Three-minute setup cooldown.  Only a material state change can break it.
    critical = kind in {"SYSTEM_CONFLICT", "BUILDUP_FAILED", "LIQUIDITY_SWEEP"}
    direction_flip = direction in {"BULLISH", "BEARISH"} and last_dir in {"BULLISH", "BEARISH"} and direction != last_dir
    strong_upgrade = (
        kind in {"ONE_BRAIN_ALIGNMENT", "STRONG_BUILDUP"}
        and current_rank > last_rank
        and score >= last_score + 12
    )
    material_change = critical or direction_flip or strong_upgrade
    if age < 180 and not material_change:
        st.session_state.market_intelligence_alert_status = "Smart cooldown — evidence recorded, Telegram suppressed"
        return []
    if age < 60 and critical and kind == last_kind and direction == last_dir:
        return []

    liquidity = item.get("liquidity") if isinstance(item.get("liquidity"), dict) else {}
    zone = liquidity.get("primary_zone") if isinstance(liquidity.get("primary_zone"), dict) else None
    target_text = _zone_text(zone)
    hunt_bias = str(liquidity.get("hunt_bias") or "UNCLEAR")
    hunt_strength = str(liquidity.get("hunt_strength") or "")
    sweep_state = str(liquidity.get("sweep_state") or "NONE")
    sweep_outcome = str(liquidity.get("sweep_outcome") or "UNCLEAR")

    # Reuse already-computed candle/Big-Player evidence in the same Telegram story.
    confirm_line = ""
    try:
        confirm = combined_signal_alert(snapshot)
    except Exception:
        confirm = None
    if isinstance(confirm, dict) and not bool(confirm.get("conflict")):
        cdir = str(confirm.get("direction") or "MIXED").upper()
        names = str(confirm.get("names") or "").strip()
        if names and (direction == "MIXED" or cdir == direction):
            confirm_line = f"\nConfirm: {names[:180]}"

    if alignment in {"STRONG EVIDENCE ALIGNMENT", "ALIGNMENT WATCH", "DIRECTION ALIGNED"}:
        one_brain_text = "ALIGNED"
    elif alignment == "SYSTEM CONFLICT":
        one_brain_text = "CONFLICT"
    else:
        one_brain_text = "NO CLEAR ALIGNMENT"

    icon = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "🟠"
    target_line = "" if target_text == "NO CLEAR TARGET" else f"\nTarget: {target_text} · Hunt {hunt_bias} {hunt_strength}".rstrip()
    sweep_line = "" if sweep_state in {"NONE", "UPSIDE TARGET TESTING", "DOWNSIDE TARGET TESTING"} else f"\nSweep: {sweep_state} · {sweep_outcome}"
    payload = {
        "captured_at": row.get("captured_at") or created_at.isoformat(),
        "alert_id": fingerprint,
        "kind": kind,
        "title": row.get("title", kind),
        "direction": direction if direction in {"BULLISH", "BEARISH"} else "MIXED",
        "nifty_ltp": row.get("nifty_ltp"),
        "score": row.get("score"),
        "coverage": item.get("evidence_coverage"),
        "alignment": alignment,
        "liquidity_bias": liquidity.get("hunt_bias"),
        "liquidity_target": zone,
        "sweep_outcome": liquidity.get("sweep_outcome"),
        "message": (
            f"🧠 ONE BRAIN MARKET INTELLIGENCE\n"
            f"{icon} {row.get('title', kind)} · {direction}\n"
            f"Move {expansion:.0f}/100 · Coverage {coverage:.0f}%"
            f"{target_line}{sweep_line}{confirm_line}\n"
            f"One Brain: {one_brain_text} · Status: {item.get('system_status', 'WATCH')}\n"
            "Precaution/shadow alert · automatic order nahi."
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
        st.session_state.smart_alert_last_sent_at = current_ts
        st.session_state.smart_alert_last_direction = direction
        st.session_state.smart_alert_last_kind = kind
        st.session_state.smart_alert_last_rank = current_rank
        st.session_state.smart_alert_last_score = score
        st.session_state.market_intelligence_alert_status = "Telegram synced · Smart Alert mode"
        st.session_state.last_market_intelligence_alerts = [row]
        return [row]

    seen.add(fingerprint)
    st.session_state[seen_key] = list(seen)[-300:]
    st.session_state.market_intelligence_alert_status = "App only — Telegram gateway not configured"
    st.session_state.last_market_intelligence_alerts = [row]
    return []

