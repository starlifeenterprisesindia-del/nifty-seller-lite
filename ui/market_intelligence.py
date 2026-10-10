"""Display/alert layer for the shadow-only One Brain Market Intelligence Engine.

The main screen intentionally shows one short market story.  Research/debug numbers stay
inside the expander.  This module never recalculates market evidence or fetches data.
"""
from __future__ import annotations

from typing import Any
from html import escape

import streamlit as st
from config import CONFIG

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
    integrity = item.get("pressure_integrity") if isinstance(item.get("pressure_integrity"), dict) else {}
    qstate = str(integrity.get("quality_state") or "UNVERIFIED")
    attack = str(integrity.get("move_attack_state") or "NORMAL")
    risk = str(integrity.get("move_risk_state") or "NORMAL")
    if qstate == "FLIP CONFIRMED":
        return "PRESSURE FLIP CONFIRMED ↻"
    if qstate == "FLIP WATCH":
        return "PRESSURE FLIP WATCH ↻"
    if qstate in {"ABSORPTION RISK", "BUILD-UP FAILED"}:
        return "PRESSURE REJECTED / FAKE RISK"
    if qstate == "EXHAUSTING":
        return "MOVE EXHAUSTING"
    if attack == "BREAK / EXPANSION":
        return "BREAK / EXPANSION ⚡"
    if attack == "ATTACK":
        return "BARRIER ATTACK ⚡"
    if risk == "HIGH":
        return "BIG MOVE RISK HIGH ⚡"
    if risk == "BUILDING":
        return "MOVE RISK BUILDING ⚡"
    if risk == "WATCH":
        return "MOVE WATCH"
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
    integrity = item.get("pressure_integrity") if isinstance(item.get("pressure_integrity"), dict) else {}
    quality_state = str(integrity.get("quality_state") or "UNVERIFIED")
    quality_score = _num(integrity.get("quality_score"))

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
  <div class="obmie-radar-sub">{message} · Pressure {pressure:.0f}/100 · Quality {quality_state} {quality_score:.0f}/100{extra}</div>
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
    integrity = item.get("pressure_integrity") if isinstance(item.get("pressure_integrity"), dict) else {}
    institutional = item.get("institutional_window") if isinstance(item.get("institutional_window"), dict) else {}
    zone = liquidity.get("primary_zone") if isinstance(liquidity, dict) else None
    target = _zone_text(zone if isinstance(zone, dict) else None)
    zone_role = str(liquidity.get("zone_role") or "LIQUIDITY ZONE")
    next_hunt = liquidity.get("next_hunt_zone") if isinstance(liquidity.get("next_hunt_zone"), dict) else None
    next_hunt_text = _zone_text(next_hunt) if next_hunt else "—"
    hunt_bias = str(liquidity.get("hunt_bias") or "UNCLEAR") if isinstance(liquidity, dict) else "UNCLEAR"
    hunt_strength = str(liquidity.get("hunt_strength") or "LOW") if isinstance(liquidity, dict) else "LOW"
    money = liquidity.get("money_concentration") if isinstance(liquidity.get("money_concentration"), dict) else {}
    money_bias = str(money.get("bias") or "UNCLEAR")
    money_score = max(_num(money.get("upside_score")), _num(money.get("downside_score"))) if money else 0.0
    sweep_outcome = str(liquidity.get("sweep_outcome") or "UNCLEAR") if isinstance(liquidity, dict) else "UNCLEAR"
    reach = str(liquidity.get("reach_state") or "UNCLEAR") if isinstance(liquidity, dict) else "UNCLEAR"
    alignment = str(item.get("one_brain_alignment") or "NO CLEAR ALIGNMENT")
    institutional = item.get("institutional_window") if isinstance(item.get("institutional_window"), dict) else {}
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
            (zone_role, target),
            ("HUNT", f"{hunt_bias} · {hunt_strength}"),
            ("MOVE PRESSURE", f"{pressure:.0f}/100 {velocity_marker}".strip()),
            ("PRESSURE QUALITY", f"{integrity.get('quality_state', 'UNVERIFIED')} · {_num(integrity.get('quality_score')):.0f}/100"),
            ("ONE BRAIN", one_brain_text),
            ("STATUS", str(item.get("system_status") or "WAIT")),
        ])

        if alignment == "STRONG EVIDENCE ALIGNMENT":
            st.success(f"🔥 STRONG EVIDENCE ALIGNMENT · {_direction_icon(direction)} {direction} · Target {target}")
        elif alignment == "SYSTEM CONFLICT":
            st.warning("⚠️ SYSTEM CONFLICT · One Brain aur Market Intelligence opposite hain — chase mat karo")
        elif str(liquidity.get("sweep_state") or "NONE") not in {"NONE", "UPSIDE TARGET TESTING", "DOWNSIDE TARGET TESTING"}:
            st.info(f"🔥 {liquidity.get('sweep_state')} · {sweep_outcome}")

        if institutional:
            iw_state = str(institutional.get("state") or "CLOSED").upper()
            iw_dir = str(institutional.get("direction") or "MIXED").upper()
            iw_score = _num(institutional.get("opportunity_score"))
            iw_ready = int(_num(institutional.get("gates_ready")))
            iw_missing = [str(x) for x in (institutional.get("missing_gates") or []) if str(x).strip()]
            iw_icon = "🟢" if iw_state == "STRONG" else "🔵" if iw_state == "OPEN" else "🟠" if iw_state == "FORMING" else "⚪"
            iw_path = institutional.get("path_clearance") if isinstance(institutional.get("path_clearance"), dict) else {}
            iw_opp = institutional.get("opposition_weakness") if isinstance(institutional.get("opposition_weakness"), dict) else {}
            iw_trigger = institutional.get("trigger_readiness") if isinstance(institutional.get("trigger_readiness"), dict) else {}
            iw_pressure = institutional.get("pressure_effectiveness") if isinstance(institutional.get("pressure_effectiveness"), dict) else {}
            st.markdown(
                f"**{iw_icon} Institutional Opportunity Window · {iw_state} · {_direction_icon(iw_dir)} {iw_dir}**\n\n"
                f"{iw_ready}/6 core gates · Opportunity {iw_score:.0f}/100 · "
                f"Path {iw_path.get('state', '—')} · Opposition {iw_opp.get('state', '—')} · "
                f"Trigger {iw_trigger.get('state', '—')} · Pressure {iw_pressure.get('state', '—')} · "
                f"💰 Liquidity Concentration {money_bias} {money_score:.0f}/100"
            )
            if iw_missing and iw_state not in {"OPEN", "STRONG"}:
                st.caption("Window missing: " + ", ".join(iw_missing[:3]) + ("…" if len(iw_missing) > 3 else ""))

        _render_story_grid([
            ("5m", _path_text(item.get("path_5m"))),
            ("15m", _path_text(item.get("path_15m"))),
            ("30m", _path_text(item.get("path_30m"))),
        ], outlook=True)
        sync = (getattr(snapshot, "metadata", {}) or {}).get("snapshot_integrity") or {}
        sync_text = ""
        if sync:
            sync_text = (
                f" · Data sync: {sync.get('state', '—')}"
                f" ({sync.get('core_live', 0)}/{sync.get('core_total', 0)} core)"
            )
        st.caption(
            f"Next hunt: {next_hunt_text} · After zone: {sweep_outcome} · Reach: {reach} · "
            f"Fake-risk: {integrity.get('fake_pressure_score', '—')} · Evidence: {_evidence_label(item)}"
            f"{sync_text} · scores evidence hain, calibrated probabilities nahi."
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
        z1, z2, z3, z4 = st.columns(4)
        z1.metric("Pressure Quality", f"{_num(integrity.get('quality_score')):.0f}/100")
        z2.metric("Attack State", str(integrity.get("move_attack_state") or "NORMAL"))
        z3.metric("Price Response", str(integrity.get("price_response_state") or "UNCONFIRMED"))
        z4.metric("Barrier", str(integrity.get("barrier_state") or "—"))
        st.caption(
            f"Early direction: {item.get('early_direction', item.get('direction', 'MIXED'))} · "
            f"15m context: {item.get('dominant_context', 'MIXED')} · "
            f"Context: {item.get('direction_context', 'MIXED')} · "
            f"Fast families: {int(_num(item.get('fast_confirmation_count')))}"
        )

        if institutional:
            st.markdown("**🏦 Institutional Opportunity Window — research detail**")
            i1, i2, i3, i4 = st.columns(4)
            i1.metric("Window", str(institutional.get("state") or "CLOSED"))
            i2.metric("Opportunity", f"{_num(institutional.get('opportunity_score')):.0f}/100")
            i3.metric("Core gates", f"{int(_num(institutional.get('gates_ready')))}/6")
            i4.metric("Data safety", str(institutional.get("data_safety_state") or "—"))
            gate_rows = []
            for key, label in (
                ("directional_edge", "Directional Edge"),
                ("opposition_weakness", "Opposition Weakness"),
                ("path_clearance", "Path Clearance"),
                ("participation_capacity", "Participation Capacity Proxy"),
                ("trigger_readiness", "Trigger Readiness"),
                ("pressure_effectiveness", "Pressure Effectiveness"),
            ):
                gate = institutional.get(key) if isinstance(institutional.get(key), dict) else {}
                gate_rows.append({
                    "Gate": label,
                    "Ready": "YES" if gate.get("passed") else "NO",
                    "Score": "—" if gate.get("score") is None else f"{_num(gate.get('score')):.1f}",
                    "State": gate.get("state") or "NO VOTE",
                    "Why": gate.get("reason") or "",
                })
            st.dataframe(gate_rows, hide_index=True, width="stretch")
            support = [str(x) for x in (institutional.get("supportive_signals") or []) if str(x).strip()]
            if support:
                st.caption("Supportive only: " + ", ".join(support))
            for caution in institutional.get("cautions") or ():
                st.caption("Institutional Window caution: " + str(caution))

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
            if money:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Liquidity Concentration", money_bias)
                m2.metric("Upside money proxy", f"{_num(money.get('upside_score')):.0f}/100")
                m3.metric("Downside money proxy", f"{_num(money.get('downside_score')):.0f}/100")
                m4.metric("Concentration confidence", f"{_num(money.get('confidence')):.0f}/100")
                primary_money = money.get("primary_zone") if isinstance(money.get("primary_zone"), dict) else None
                st.caption(
                    "Liquidity Concentration observable OI/OI-add/volume ka location proxy hai — "
                    "ye market direction prediction nahi. Hunt direction alag path/pressure signal hai; dono conflict bhi kar sakte hain."
                )
                if primary_money:
                    st.caption(
                        f"Strongest visible option concentration near {float(primary_money.get('strike') or 0):,.0f} · "
                        f"OI {float(primary_money.get('oi') or 0):,.0f} · "
                        f"OI add {float(primary_money.get('oi_change') or 0):,.0f} · "
                        f"Volume {float(primary_money.get('volume') or 0):,.0f}. "
                        "Ye exact rupee money/hidden stop quantity nahi hai."
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
                "Bull": f"{_num(expert.get('bullish')):.1f}", "Bear": f"{_num(expert.get('bearish')):.1f}", "Range": f"{_num(expert.get('range_score')):.1f}",
                "Reliability": f"{round(_num(expert.get('reliability')) * 100)}%",
                "Freshness": f"{round(_num(expert.get('freshness')) * 100)}%",
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
    def _audit(action: str, kind: str = "", direction: str = "", reason: str = "", score: float = 0.0) -> None:
        rows = list(st.session_state.get("smart_alert_audit_local", []))
        rows.append({
            "at": getattr(getattr(snapshot, "created_at", None), "isoformat", lambda: "")(),
            "action": action, "kind": kind, "direction": direction,
            "reason": reason, "score": round(float(score or 0.0), 1),
        })
        st.session_state.smart_alert_audit_local = rows[-500:]

    if not st.session_state.get("market_intelligence_alerts_enabled", True):
        _audit("SUPPRESSED", reason="Market Intelligence alerts disabled")
        return []
    item = (getattr(snapshot, "metadata", {}) or {}).get("market_intelligence") or {}
    # Alert processing runs independently from the screen renderer, so keep its
    # Institutional Window context local to this function as well.  Without this
    # local binding, the first live MI alert could raise NameError even though the
    # underlying Market Intelligence calculation had completed successfully.
    institutional = item.get("institutional_window") if isinstance(item.get("institutional_window"), dict) else {}
    alerts = [row for row in (item.get("alerts") or ()) if isinstance(row, dict)]
    if not alerts:
        return []

    expansion = _num(item.get("expansion_pressure"))
    coverage = _num(item.get("evidence_coverage"))
    alignment = str(item.get("one_brain_alignment") or "NO CLEAR ALIGNMENT")

    # STRONG-ONLY delivery. Every event remains inside the snapshot/replay journal;
    # Telegram is reserved for genuinely actionable escalation or important risk.
    integrity0 = item.get("pressure_integrity") if isinstance(item.get("pressure_integrity"), dict) else {}
    attack0 = str(integrity0.get("move_attack_state") or "").upper()
    critical_kinds = {
        "PRESSURE_FLIP_CONFIRMED", "SYSTEM_CONFLICT", "PRESSURE_ABSORBED",
        "BUILDUP_FAILED", "LIQUIDITY_SWEEP", "MOVE_EXHAUSTING",
    }
    filtered = []
    for candidate in alerts:
        kind0 = str(candidate.get("kind") or "")
        dir0 = str(candidate.get("direction") or "MIXED").upper()
        score0 = _num(candidate.get("score"), expansion)
        strong = False
        if kind0 in critical_kinds:
            strong = True
        elif kind0 == "INSTITUTIONAL_WINDOW_STRONG":
            strong = coverage >= CONFIG.mi_alert_strong_min_coverage
        elif kind0 == "INSTITUTIONAL_WINDOW_OPEN":
            strong = score0 >= 70 and coverage >= CONFIG.mi_alert_strong_min_coverage and expansion >= 60
        elif kind0 == "PRESSURE_VERIFIED":
            strong = (
                coverage >= CONFIG.mi_alert_strong_min_coverage
                and expansion >= CONFIG.mi_alert_strong_min_expansion
                and attack0 in {"ATTACK", "BREAK / EXPANSION", "BREAK/EXPANSION"}
            )
        elif kind0 == "MOVE_ATTACK":
            strong = coverage >= CONFIG.mi_alert_strong_min_coverage and expansion >= CONFIG.mi_alert_strong_min_expansion
        elif kind0 == "ONE_BRAIN_ALIGNMENT":
            strong = alignment == "STRONG EVIDENCE ALIGNMENT" and coverage >= 70 and expansion >= CONFIG.mi_alert_strong_min_expansion
        elif kind0 == "BIG_MOVE_PRECAUTION":
            strong = expansion >= 70 and coverage >= 70 and dir0 in {"BULLISH", "BEARISH"}
        # LIQUIDITY_HUNT_WATCH and ordinary watch/forming events remain screen/journal only.
        if strong:
            filtered.append(candidate)
        else:
            _audit("SUPPRESSED", kind0, dir0, "STRONG_ONLY delivery gate", score0)
    if not filtered:
        st.session_state.market_intelligence_alert_status = "Strong-only mode — watch events recorded, Telegram suppressed"
        return []

    rank = {
        "PRESSURE_FLIP_CONFIRMED": 110,
        "SYSTEM_CONFLICT": 106,
        "INSTITUTIONAL_WINDOW_STRONG": 103,
        "INSTITUTIONAL_WINDOW_OPEN": 99,
        "PRESSURE_ABSORBED": 104,
        "BUILDUP_FAILED": 102,
        "LIQUIDITY_SWEEP": 100,
        "MOVE_EXHAUSTING": 98,
        "PRESSURE_FLIP_WATCH": 96,
        "PRESSURE_VERIFIED": 94,
        "MOVE_ATTACK": 92,
        "ONE_BRAIN_ALIGNMENT": 90,
        "LIQUIDITY_HUNT_WATCH": 86,
        "BIG_MOVE_PRECAUTION": 80,
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

    # One primary alert per continuing market story. A genuine direction flip or
    # critical risk state can override; an ordinary OPEN→STRONG upgrade cannot.
    critical = kind in {
        "SYSTEM_CONFLICT", "BUILDUP_FAILED", "LIQUIDITY_SWEEP", "PRESSURE_ABSORBED",
        "PRESSURE_FLIP_CONFIRMED", "MOVE_EXHAUSTING",
    }
    direction_flip = direction in {"BULLISH", "BEARISH"} and last_dir in {"BULLISH", "BEARISH"} and direction != last_dir
    material_change = critical or direction_flip
    last_story = str(st.session_state.get("smart_alert_last_story") or "")
    story_key = f"{direction}:{target_key}"
    if age < CONFIG.mi_alert_story_cooldown_seconds and story_key == last_story and not material_change:
        st.session_state.market_intelligence_alert_status = "Strong-only story cooldown — evidence recorded, Telegram suppressed"
        _audit("SUPPRESSED", kind, direction, "Same continuing setup story", score)
        return []
    if age < 60 and critical and kind == last_kind and direction == last_dir:
        _audit("SUPPRESSED", kind, direction, "Duplicate critical alert <60s", score)
        return []

    liquidity = item.get("liquidity") if isinstance(item.get("liquidity"), dict) else {}
    integrity = item.get("pressure_integrity") if isinstance(item.get("pressure_integrity"), dict) else {}
    zone = liquidity.get("primary_zone") if isinstance(liquidity.get("primary_zone"), dict) else None
    target_text = _zone_text(zone)
    zone_role = str(liquidity.get("zone_role") or "LIQUIDITY ZONE")
    next_zone = liquidity.get("next_hunt_zone") if isinstance(liquidity.get("next_hunt_zone"), dict) else None
    next_target_text = _zone_text(next_zone) if next_zone else ""
    hunt_bias = str(liquidity.get("hunt_bias") or "UNCLEAR")
    hunt_strength = str(liquidity.get("hunt_strength") or "")
    sweep_state = str(liquidity.get("sweep_state") or "NONE")
    sweep_outcome = str(liquidity.get("sweep_outcome") or "UNCLEAR")

    # Reuse already-computed candle/Big-Player evidence in the same Telegram story.
    confirm_line = ""
    supportive = [str(x) for x in (integrity.get("supportive_signals") or []) if str(x).strip()]
    if supportive:
        confirm_line = "\nSupport: " + ", ".join(supportive[:2])
    else:
        try:
            confirm = combined_signal_alert(snapshot)
        except Exception:
            confirm = None
        if isinstance(confirm, dict) and not bool(confirm.get("conflict")):
            cdir = str(confirm.get("direction") or "MIXED").upper()
            names = str(confirm.get("names") or "").strip()
            if names and (direction == "MIXED" or cdir == direction):
                confirm_line = f"\nSupport: {names[:180]}"

    iw_line = ""
    if institutional:
        iw_state = str(institutional.get("state") or "CLOSED")
        iw_score = _num(institutional.get("opportunity_score"))
        iw_ready = int(_num(institutional.get("gates_ready")))
        if iw_state in {"OPEN", "STRONG"} or kind.startswith("INSTITUTIONAL_WINDOW"):
            iw_line = f"\nInstitutional Window: {iw_state} · {iw_ready}/6 gates · {iw_score:.0f}/100"

    if alignment in {"STRONG EVIDENCE ALIGNMENT", "ALIGNMENT WATCH", "DIRECTION ALIGNED"}:
        one_brain_text = "ALIGNED"
    elif alignment == "SYSTEM CONFLICT":
        one_brain_text = "CONFLICT"
    else:
        one_brain_text = "NO CLEAR ALIGNMENT"

    icon = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "🟠"
    target_line = "" if target_text == "NO CLEAR TARGET" else f"\n{zone_role.title()}: {target_text} · Hunt {hunt_bias} {hunt_strength}".rstrip()
    if next_target_text and next_target_text != "NO CLEAR TARGET":
        target_line += f" · Next {next_target_text}"
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
        "institutional_window_state": institutional.get("state") if institutional else None,
        "institutional_window_score": institutional.get("opportunity_score") if institutional else None,
        "institutional_window_gates": institutional.get("gates_ready") if institutional else None,
        "message": (
            f"🧠 ONE BRAIN MARKET INTELLIGENCE\n"
            f"{icon} {row.get('title', kind)} · {direction}\n"
            f"Move {expansion:.0f}/100 · Quality {integrity.get('quality_state', 'UNVERIFIED')} "
            f"{_num(integrity.get('quality_score')):.0f}/100 · Coverage {coverage:.0f}%"
            f"{target_line}{sweep_line}{confirm_line}{iw_line}\n"
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
        st.session_state.smart_alert_last_story = story_key
        st.session_state.market_intelligence_alert_status = "Telegram synced · STRONG ONLY"
        _audit("SENT", kind, direction, "Strong-only delivery passed", score)
        st.session_state.last_market_intelligence_alerts = [row]
        return [row]

    seen.add(fingerprint)
    st.session_state[seen_key] = list(seen)[-300:]
    st.session_state.market_intelligence_alert_status = "App only — Telegram gateway not configured"
    st.session_state.smart_alert_last_story = story_key
    _audit("APP_ONLY", kind, direction, "Strong-only delivery passed; no Telegram gateway", score)
    st.session_state.last_market_intelligence_alerts = [row]
    return []

