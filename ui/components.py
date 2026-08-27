from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from analysis.evidence_matrix import build_compact_evidence_matrix, build_module_impact_audit
from analysis.presentation_safety import (
    candidate_invalidation_text,
    display_main_blocker,
    market_rukh_display,
    normalized_news_display,
    safe_brain_hinglish_line,
)
from models import MarketLevel, MarketSnapshot, TimeframeIndicators
from services.summary_presenter import (
    best_existing_candidate,
    plan_leg_text,
    required_live_feed_state,
    snapshot_change_hinglish,
    snapshot_change_items,
)


def _barrier_sources(level: Any | None) -> str:
    if level is None or not level.sources:
        return "Data unavailable"
    return " + ".join(str(item) for item in level.sources[:4])


def _pressure_label(score: float) -> str:
    if score <= 25:
        return "Bahut kam"
    if score <= 40:
        return "Kam"
    if score < 60:
        return "Barabar takkar"
    if score < 75:
        return "Zyada"
    return "Bahut zyada"


def _barrier_verdict(level: Any) -> str:
    side = str(level.side).upper()
    margin = float(level.strength) - float(level.break_pressure)
    if margin >= 15:
        return "Resistance filhaal majboot" if side == "RESISTANCE" else "Support filhaal majboot"
    if margin <= -8:
        return "Resistance toot sakta hai" if side == "RESISTANCE" else "Support toot sakta hai"
    return "Takkar barabar—WAIT"


def _barrier_state_hinglish(level: Any) -> str:
    state = str(level.state).upper()
    mapping = {
        "TESTING": "ABHI TEST HO RAHA",
        "HOLDING / STRONG": "MAJBOOT / BACH RAHA",
        "HOLDING": "FILHAAL BACH RAHA",
        "WEAKENING / BREAK RISK": "KAMZOR / TOOTNE KA RISK",
        "APPROACHING": "MARKET PAAS AA RAHA",
        "AHEAD": "AAGE KA LEVEL",
        "FAR": "ABHI DOOR",
    }
    return mapping.get(state, state)


def _responsive_cards_html(cards: list[tuple[str, str, str, str]]) -> str:
    """Responsive replacement for wide dataframes on phone screens."""
    blocks = []
    for label, value, note, tone in cards:
        blocks.append(
            f'<div class="rfc {escape(tone)}">'
            f'<div class="rfc-label">{escape(label)}</div>'
            f'<div class="rfc-value">{escape(value)}</div>'
            f'<div class="rfc-note">{escape(note)}</div>'
            '</div>'
        )
    return (
        '<style>'
        '.rfc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:6px 0 12px}'
        '.rfc{min-width:0;border:1px solid rgba(127,127,127,.24);border-radius:12px;padding:10px;background:rgba(127,127,127,.045)}'
        '.rfc.green{border-color:rgba(34,197,94,.38);background:rgba(34,197,94,.08)}'
        '.rfc.amber{border-color:rgba(245,158,11,.38);background:rgba(245,158,11,.08)}'
        '.rfc.red{border-color:rgba(239,68,68,.38);background:rgba(239,68,68,.08)}'
        '.rfc-label{font-size:.75rem;font-weight:800;opacity:.76;text-transform:uppercase}'
        '.rfc-value{font-size:1.02rem;font-weight:900;margin:3px 0;overflow-wrap:anywhere}'
        '.rfc-note{font-size:.77rem;line-height:1.35;opacity:.82;overflow-wrap:anywhere}'
        '@media(max-width:760px){.rfc-grid{grid-template-columns:1fr}}'
        '</style><div class="rfc-grid">' + ''.join(blocks) + '</div>'
    )


def _barrier_level_html(level: Any | None, *, css_class: str, fallback_label: str) -> str:
    if level is None:
        return (
            f'<div class="bm-level {css_class} muted">'
            f'<div class="bm-tag">{escape(fallback_label)}</div>'
            '<div class="bm-zone">Unresolved</div></div>'
        )
    state = escape(_barrier_state_hinglish(level))
    sources = escape(_barrier_sources(level))
    strength_width = max(0.0, min(100.0, float(level.strength)))
    pressure_width = max(0.0, min(100.0, float(level.break_pressure)))
    confirmation = (
        f"{level.upper:,.0f} ke upar close"
        if str(level.side).upper() == "RESISTANCE"
        else f"{level.lower:,.0f} ke neeche close"
    )
    return (
        f'<div class="bm-level {css_class}">'
        f'<div class="bm-level-head"><span class="bm-tag">{escape(level.label)} · {escape(level.side.title())}</span>'
        f'<span class="bm-state">{state}</span></div>'
        f'<div class="bm-zone">{level.lower:,.0f}–{level.upper:,.0f}</div>'
        f'<div class="bm-bars">'
        f'<div><span>Na tootne ki majbooti</span><b>{level.strength:.0f}/100</b><div class="bm-track"><div class="bm-fill" style="width:{strength_width:.0f}%"></div></div></div>'
        f'<div><span>Tootne ka pressure</span><b>{level.break_pressure:.0f}/100 · {escape(_pressure_label(float(level.break_pressure)))}</b><div class="bm-track"><div class="bm-fill pressure" style="width:{pressure_width:.0f}%"></div></div></div>'
        f'</div>'
        f'<div class="bm-verdict">Faisla: {escape(_barrier_verdict(level))}</div>'
        f'<div class="bm-small">Confirm: {confirmation}</div>'
        f'<div class="bm-small">{level.distance_points:,.0f} pts door · Kyun: {sources}</div>'
        f'</div>'
    )


def render_compact_barrier_map(
    snapshot: MarketSnapshot,
    previous_snapshot: MarketSnapshot | None = None,
) -> None:
    item = snapshot.barrier_map

    def role_reversal_message() -> tuple[str, bool] | None:
        if previous_snapshot is None:
            return None
        previous = previous_snapshot.barrier_map
        candidates = tuple(
            (old, new, old_side, new_side)
            for old_side, old_levels, new_side, new_levels in (
                ("SUPPORT", (previous.nearest_support, previous.next_support), "RESISTANCE", (item.nearest_resistance, item.next_resistance)),
                ("RESISTANCE", (previous.nearest_resistance, previous.next_resistance), "SUPPORT", (item.nearest_support, item.next_support)),
            )
            for old in old_levels
            for new in new_levels
        )
        completed = snapshot.candles_1m.copy()
        if not completed.empty and "is_complete" in completed.columns:
            completed = completed[completed["is_complete"].fillna(False).astype(bool)]
        completed = completed.dropna(subset=["high", "low", "close"]).tail(12) if not completed.empty else completed
        for old, new, old_side, new_side in candidates:
            if old is None or new is None:
                continue
            lower = max(float(old.lower), float(new.lower))
            upper = min(float(old.upper), float(new.upper))
            if lower > upper:
                continue
            confirmed = False
            if new_side == "RESISTANCE":
                below_seen = False
                for _, candle in completed.iterrows():
                    close = float(candle["close"])
                    if close < lower:
                        if below_seen and float(candle["high"]) >= lower:
                            confirmed = True
                            break
                        below_seen = True
            else:
                above_seen = False
                for _, candle in completed.iterrows():
                    close = float(candle["close"])
                    if close > upper:
                        if above_seen and float(candle["low"]) <= upper:
                            confirmed = True
                            break
                        above_seen = True
            zone = f"{lower:,.0f}–{upper:,.0f}"
            if confirmed:
                return f"Confirmed Role Reversal: Broken {old_side.title()} → {new_side.title()} {zone}", True
            return f"Possible Role Reversal — Retest Pending: Broken {old_side.title()} → {new_side.title()} {zone}", False
        return None

    def pattern_level_text(
        signal: Any | None, fallback: str, *, show_possible_effect: bool = False
    ) -> tuple[str, str]:
        if signal is None or str(getattr(signal, "name", "")).upper() in {
            "",
            "NO VALID W/M",
            "NO IMPORTANT CANDLE",
        }:
            return fallback, "Abhi nearest level par valid signal nahi"
        name = str(signal.name)
        direction = str(getattr(signal, "direction", "NEUTRAL"))
        strength = str(getattr(signal, "strength", "NORMAL"))
        confidence = float(getattr(signal, "confidence", 0.0) or 0.0)
        level_value = getattr(signal, "level_value", None)
        level_label = str(getattr(signal, "level_label", "") or "Nearest level")
        level_text = (
            f"{level_label} {float(level_value):,.0f} ke paas"
            if level_value is not None
            else "Nearest level confirmation nahi"
        )
        stage = str(getattr(signal, "stage", "") or "")
        note = (
            f"{direction} · {strength} · Evidence quality {confidence:.0f}% · "
            f"{stage} · {level_text}"
        )
        if show_possible_effect:
            pattern_meaning = {
                "MORNING STAR": "Girawat ke baad upar palatne/bounce ka signal",
                "EVENING STAR": "Tezi ke baad neeche palatne ka signal",
                "BULL ENGULF": "Buyers ka zor; upar jaane ka signal",
                "BEAR ENGULF": "Sellers ka zor; neeche jaane ka signal",
                "HAMMER": "Neeche se recovery; upar bounce ka signal",
                "SHOOTING STAR": "Upar rejection; neeche pressure ka signal",
                "DOJI": "Buyer-seller barabar; direction clear nahi",
            }.get(name.upper())
            effect = (
                "Upar bounce/continuation"
                if direction.upper() == "BULLISH"
                else "Neeche rejection/pressure"
                if direction.upper() == "BEARISH"
                else "Indecision—level break ka wait"
            )
            if pattern_meaning:
                note += f" · Seedha matlab: {pattern_meaning}"
            note += (
                f" · Mumkin asar: {effect} · "
                f"Chance (signal evidence quality) {confidence:.0f}%"
            )
        return name, note

    def level_text(level: Any | None, fallback: str, *, side: str) -> tuple[str, str]:
        if level is None:
            spot_value = getattr(item, "current_price", None)
            remaining = getattr(item, "vix_expected_remaining_move_points", None)
            if spot_value is None:
                return "— · Data kam", "Fresh snapshot ka wait"
            step = max(25.0, float(remaining or 100.0) * 0.25)
            raw = float(spot_value) + step if side == "RESISTANCE" else float(spot_value) - step
            estimated = round(raw / 50.0) * 50.0
            return (
                f"Estimated zone {estimated:,.0f}",
                f"Agla pakka barrier nahi mila — Evidence quality kam. {fallback}",
            )
        return (
            f"{level.lower:,.0f}–{level.upper:,.0f}",
            f"Bachne ki taakat {level.strength:.0f} · Tootne ka pressure {level.break_pressure:.0f} ({_pressure_label(float(level.break_pressure))}) · {_barrier_verdict(level)}",
        )

    resistance, resistance_note = level_text(item.nearest_resistance, "Resistance unavailable", side="RESISTANCE")
    support, support_note = level_text(item.nearest_support, "Support unavailable", side="SUPPORT")
    overlap_text = None
    if item.nearest_resistance is not None and item.nearest_support is not None:
        overlap_lower = max(item.nearest_resistance.lower, item.nearest_support.lower)
        overlap_upper = min(item.nearest_resistance.upper, item.nearest_support.upper)
        if overlap_lower <= overlap_upper:
            overlap_text = f"{overlap_lower:,.0f}–{overlap_upper:,.0f}"
            resistance_note = "Compression/decision zone ka upper side · " + resistance_note
            support_note = "Compression/decision zone ka lower side · " + support_note
    spot = f"{item.current_price:,.2f}" if item.current_price is not None else "—"
    range_item = item.trading_range
    range_text = (
        f"{range_item.lower:,.0f}–{range_item.upper:,.0f}"
        if range_item.lower is not None and range_item.upper is not None
        else "Range unresolved"
    )
    patterns = getattr(snapshot, "patterns", None)
    wm_name, wm_note = pattern_level_text(
        getattr(patterns, "wm_3m", None), "NO VALID W/M"
    )
    main_candle = (
        getattr(patterns, "candle_3m", None)
    )
    candle_name, candle_note = pattern_level_text(
        main_candle,
        "NO IMPORTANT CANDLE",
        show_possible_effect=True,
    )
    if candle_name != "NO IMPORTANT CANDLE":
        main_direction = str(getattr(main_candle, "direction", "NEUTRAL")).upper()

        def confirmation_text(signal: Any | None) -> str:
            name = str(getattr(signal, "name", "") or "").upper()
            direction = str(getattr(signal, "direction", "NEUTRAL") or "NEUTRAL").upper()
            if name in {"", "NO IMPORTANT CANDLE"}:
                return "NA"
            if main_direction not in {"BULLISH", "BEARISH"}:
                return direction
            if direction == main_direction:
                return "YES"
            if direction in {"BULLISH", "BEARISH"}:
                return "CONFLICT"
            return "NO"

        candle_note += (
            f" · 5M context {confirmation_text(getattr(patterns, 'candle_5m', None))}"
            f" · 15M confirm {confirmation_text(getattr(patterns, 'candle_15m', None))}"
        )

    st.subheader("🧭 Nearest Levels + Core Market Evidence")
    html = (
        '<style>'
        '.cbm-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:4px 0 8px}'
        '.cbm{border-radius:13px;padding:12px;border:1px solid rgba(127,127,127,.24);background:rgba(127,127,127,.04)}'
        '.cbm.res{border-color:rgba(239,68,68,.42);background:rgba(239,68,68,.08)}'
        '.cbm.spot{border-color:rgba(59,130,246,.42);background:rgba(59,130,246,.08);text-align:center}'
        '.cbm.sup{border-color:rgba(34,197,94,.42);background:rgba(34,197,94,.08)}'
        '.cbm-l{font-size:.74rem;font-weight:800;opacity:.75;letter-spacing:.04em}'
        '.cbm-v{font-size:1.25rem;font-weight:900;margin-top:4px}'
        '.cbm-n{font-size:.72rem;opacity:.72;margin-top:4px;line-height:1.3}'
        '.cbm-patterns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 8px}'
        '.cbm.pattern{padding:9px 11px}.cbm.pattern .cbm-v{font-size:1rem}'
        '.cbm.wm{border-color:rgba(168,85,247,.38);background:rgba(168,85,247,.07)}'
        '.cbm.candle{border-color:rgba(245,158,11,.38);background:rgba(245,158,11,.07)}'
        '@media(max-width:760px){.cbm-grid,.cbm-patterns{grid-template-columns:1fr}.cbm{padding:10px}.cbm-v{font-size:1.12rem}.cbm.pattern{padding:8px 10px}}'
        '</style>'
        '<div class="cbm-grid">'
        f'<div class="cbm res"><div class="cbm-l">AGLI RUKAWAT</div><div class="cbm-v">{escape(resistance)}</div><div class="cbm-n">{escape(resistance_note)}</div></div>'
        f'<div class="cbm spot"><div class="cbm-l">NIFTY ABHI</div><div class="cbm-v">{escape(spot)}</div><div class="cbm-n">Range {range_item.confidence:.0f}/100 · {escape(range_item.breakout_bias)} · Confirmation tak WAIT</div></div>'
        f'<div class="cbm sup"><div class="cbm-l">AGLA SAHARA</div><div class="cbm-v">{escape(support)}</div><div class="cbm-n">{escape(support_note)}</div></div>'
        '</div><div class="cbm-patterns">'
        f'<div class="cbm pattern wm"><div class="cbm-l">3-MINUTE W/M @ NEAREST LEVEL</div><div class="cbm-v">{escape(wm_name)}</div><div class="cbm-n">{escape(wm_note)}</div></div>'
        f'<div class="cbm pattern candle"><div class="cbm-l">5-MINUTE CANDLE @ NEAREST LEVEL</div><div class="cbm-v">{escape(candle_name)}</div><div class="cbm-n">{escape(candle_note)}</div></div>'
        '</div>'
    )
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)
    if overlap_text:
        st.warning(
            f"Decision / Compression Zone: {overlap_text}. Is overlap ke andar support aur resistance alag signal nahi; "
            "clear 3-minute close ke baad hi break maana jayega."
        )
    reversal = role_reversal_message()
    if reversal is not None:
        message, confirmed = reversal
        if confirmed:
            st.success("🔄 **" + message + "**")
        else:
            st.warning("🔄 **" + message + "** · Retest ke bina confirmed barrier nahi.")
    st.caption(
        f"Probable range {range_text} · Confidence {range_item.confidence:.0f}/100 · "
        f"Speed {item.market_speed.state} {item.market_speed.score:.0f}/100. Full map detailed section me hai."
    )
    core = snapshot.core_evidence
    core_state = getattr(core, "market_state", getattr(core, "state", "UNRESOLVED"))
    core_range = float(getattr(core, "range_score", 0.0) or 0.0)
    _render_compact_cards(
        [
            ("Bullish Evidence", f"{core.bullish_score:.1f}/100", "Upar ke completed-candle signals"),
            ("Bearish Evidence", f"{core.bearish_score:.1f}/100", "Neeche ke completed-candle signals"),
            ("Range / Mixed", f"{core_range:.1f}/100", f"Core state {core_state}"),
            ("Core data coverage", f"{core.confidence:.1f}%", f"Status {core.status}; not win probability"),
        ]
    )
    st.info("🧠 **Barrier AI:** " + item.summary)
    with st.expander("Full R1/R2/S1/S2 barrier map", expanded=False):
        render_barrier_map(snapshot)


def render_barrier_map(snapshot: MarketSnapshot) -> None:
    item = snapshot.barrier_map
    st.subheader("🧭 Live Barrier + Range Map")
    st.caption(
        "Yeh top live road-map Support/Resistance, OI flow, price structure, volume, Top-9, "
        "market speed aur India VIX ko ek hi view me dikhata hai. Strength aur Break Pressure "
        "evidence scores hain — guaranteed probability nahi."
    )

    range_item = item.trading_range
    speed = item.market_speed
    range_text = (
        f"{range_item.lower:,.0f}–{range_item.upper:,.0f}"
        if range_item.lower is not None and range_item.upper is not None
        else "Unresolved"
    )
    remaining_move = (
        f"±{item.vix_expected_remaining_move_points:,.0f} pts"
        if item.vix_expected_remaining_move_points is not None
        else "—"
    )
    st.caption(
        f"Detail map: Range {range_text} | Confidence {range_item.confidence:.0f}/100 | "
        f"Speed {speed.state} {speed.score:.0f}/100 {speed.direction} | VIX remaining move {remaining_move}."
    )

    if speed.state == "DANGER":
        st.error(
            f"🚨 FAST MARKET DANGER — speed {speed.score:.0f}/100 ({speed.direction}). "
            "Fresh option selling me extra caution; barrier break fast ho sakta hai."
        )
    elif speed.state == "FAST":
        st.warning(
            f"⚡ Market FAST hai — speed {speed.score:.0f}/100 ({speed.direction}). "
            "Nearest barrier ki Break Pressure ko priority se dekho."
        )

    r2 = _barrier_level_html(item.next_resistance, css_class="res secondary", fallback_label="R2")
    r1 = _barrier_level_html(item.nearest_resistance, css_class="res primary", fallback_label="R1")
    s1 = _barrier_level_html(item.nearest_support, css_class="sup primary", fallback_label="S1")
    s2 = _barrier_level_html(item.next_support, css_class="sup secondary", fallback_label="S2")
    spot = f"{item.current_price:,.2f}" if item.current_price is not None else "—"
    range_state = escape(range_item.state)
    bias = escape(range_item.breakout_bias)
    range_pos = f"{range_item.position_pct:.0f}%" if range_item.position_pct is not None else "—"
    vix_daily = f"±{item.vix_expected_daily_move_points:,.0f} pts" if item.vix_expected_daily_move_points is not None else "—"
    vix_5 = f"{speed.vix_change_5m_pct:+.1f}%" if speed.vix_change_5m_pct is not None else "warming"
    vix_15 = f"{speed.vix_change_15m_pct:+.1f}%" if speed.vix_change_15m_pct is not None else "warming"
    volume_text = f"{speed.volume_ratio:.2f}x" if speed.volume_ratio is not None else "warming"

    barrier_html = (
        '<style>'
        '.bm-wrap{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;background:rgba(127,127,127,.035);margin:6px 0 10px}'
        '.bm-level{border-radius:12px;padding:10px 12px;margin:7px 0;border-left:6px solid}'
        '.bm-level.res{border-left-color:#d9534f;background:rgba(217,83,79,.08)}'
        '.bm-level.sup{border-left-color:#2e9d63;background:rgba(46,157,99,.08)}'
        '.bm-level.secondary{opacity:.84}'
        '.bm-level.primary{box-shadow:0 0 0 1px rgba(127,127,127,.10) inset}'
        '.bm-level.muted{opacity:.55}'
        '.bm-level-head{display:flex;justify-content:space-between;gap:12px;align-items:center}'
        '.bm-tag{font-size:.78rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase}'
        '.bm-state{font-size:.75rem;font-weight:700;opacity:.8}'
        '.bm-zone{font-size:1.45rem;font-weight:800;line-height:1.2;margin:3px 0 7px}'
        '.bm-bars{display:grid;grid-template-columns:1fr 1fr;gap:14px;font-size:.78rem}'
        '.bm-bars>div>span{opacity:.75;margin-right:6px}'
        '.bm-bars b{float:right}'
        '.bm-verdict{font-size:.86rem;font-weight:800;margin-top:8px}'
        '.bm-track{height:6px;border-radius:99px;background:rgba(127,127,127,.18);overflow:hidden;margin-top:4px}'
        '.bm-fill{height:100%;background:#6b7280;border-radius:99px}'
        '.bm-fill.pressure{background:#f59e0b}'
        '.bm-small{font-size:.78rem;opacity:.78;margin-top:7px}'
        '.bm-spot{margin:10px 0;padding:13px;border-radius:12px;text-align:center;border:2px solid rgba(80,130,255,.45);background:rgba(80,130,255,.08)}'
        '.bm-spot .price{font-size:1.55rem;font-weight:900}'
        '.bm-road{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px;font-size:.82rem}'
        '.bm-road>div{padding:8px;border-radius:9px;background:rgba(127,127,127,.07)}'
        '@media (max-width:900px){.bm-bars,.bm-road{grid-template-columns:1fr}}'
        '</style>'
        '<div class="bm-wrap">'
        + r2 + r1
        + f'<div class="bm-spot"><div>NIFTY CURRENT</div><div class="price">{spot}</div>'
          f'<div>{range_state} · Position {range_pos} · Break Bias {bias}</div></div>'
        + s1 + s2
        + '<div class="bm-road">'
          f'<div><b>India VIX Risk</b><br>{escape(item.vix_risk)} · Daily move {vix_daily}</div>'
          f'<div><b>VIX Speed</b><br>5m {vix_5} · 15m {vix_15}</div>'
          f'<div><b>Option Shock</b><br>{speed.option_shock_score:.0f}/100 · Volume {volume_text}</div>'
          '</div></div>'
    )
    # st.html is the correct Streamlit primitive for raw HTML/CSS. Using st.markdown
    # can make indented nested HTML appear as literal code on some deployments/themes.
    if hasattr(st, "html"):
        st.html(barrier_html)
    else:
        st.markdown(barrier_html, unsafe_allow_html=True)

    st.info("🧠 **Barrier Brain:** " + item.summary)
    if range_item.explanation:
        st.caption(range_item.explanation)
    if speed.reasons:
        st.caption("Speed reasons: " + " | ".join(speed.reasons))


def render_market_session(snapshot: MarketSnapshot) -> None:
    session = snapshot.market_session
    if session.is_live:
        st.success(f"🟢 {session.label} — {session.message}")
    else:
        st.warning(f"🟡 {session.label} — {session.message}")


def render_header(snapshot: MarketSnapshot) -> None:
    quote = snapshot.nifty_quote
    last_price = quote.get("last_price")
    ohlc = quote.get("ohlc") or {}
    previous_close = ohlc.get("close")
    change_pct = None
    if previous_close not in (None, 0) and last_price is not None:
        change_pct = (
            (float(last_price) - float(previous_close)) / float(previous_close) * 100
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "NIFTY",
        f"{float(last_price):,.2f}" if last_price is not None else "—",
        f"{change_pct:+.2f}%" if change_pct is not None else None,
    )
    c2.metric("Expiry", snapshot.expiry or "Unavailable")
    c3.metric("Snapshot", snapshot.snapshot_id[-8:])
    c4.metric("Created", snapshot.created_at.strftime("%H:%M:%S IST"))


def render_evidence_matrix(
    snapshot: MarketSnapshot,
    previous_snapshot: MarketSnapshot | None = None,
) -> None:
    st.subheader("All Features — Compact Evidence")
    st.caption(
        "11 compact rows same One-Brain ki Bull/Bear/Neutral evidence dikhati hain. "
        "Barrier existing Levels/OI/Volume ka synthesis hai—extra weight 0. Big Player "
        "confirmation/gate hai—extra direction weight 0; koi second brain nahi."
    )
    rows = build_compact_evidence_matrix(snapshot, previous_snapshot)
    previous_rows = (
        build_compact_evidence_matrix(previous_snapshot)
        if previous_snapshot is not None
        else []
    )
    previous_by_module = {str(row["Module"]): row for row in previous_rows}
    reference_name, impact_by_module = build_module_impact_audit(snapshot, rows)
    previous_impact_by_module = (
        build_module_impact_audit(previous_snapshot, previous_rows)[1]
        if previous_snapshot is not None
        else {}
    )
    impact_history_path = Path("data/last_one_brain_impact.json")
    if previous_snapshot is None:
        try:
            saved = json.loads(impact_history_path.read_text(encoding="utf-8"))
            if (
                str(saved.get("snapshot_id") or "") != str(snapshot.snapshot_id)
                and str(saved.get("date") or "") == snapshot.created_at.date().isoformat()
                and isinstance(saved.get("impacts"), dict)
            ):
                previous_impact_by_module = {
                    str(key): str(value)
                    for key, value in saved["impacts"].items()
                }
        except (OSError, ValueError, TypeError):
            pass
    try:
        impact_history_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = impact_history_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "date": snapshot.created_at.date().isoformat(),
                    "created_at": snapshot.created_at.isoformat(),
                    "impacts": impact_by_module,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, impact_history_path)
    except OSError:
        pass

    impact_pattern = re.compile(
        r"Abhi\s+([0-9]+(?:\.[0-9]+)?)\s+pts\s+(Bull|Bear|Range)",
        re.IGNORECASE,
    )
    risk_pattern = re.compile(
        r"(?:Asar\s+([+-]?\d+)/9|Risk\s+(\d+)/(?:9|12))",
        re.IGNORECASE,
    )

    def impact_with_last(module: str, current_text: str) -> str:
        """Append a compact, truthful last-snapshot contribution comparison."""

        current_match = impact_pattern.search(current_text)
        if current_match is None:
            current_risk = risk_pattern.search(current_text)
            if current_risk is None:
                return current_text
            current_value = float(current_risk.group(1) or current_risk.group(2))
            if not previous_impact_by_module:
                return f"{current_text} · Last: pehla snapshot"
            previous_text = previous_impact_by_module.get(module, "")
            previous_risk = risk_pattern.search(previous_text)
            if previous_risk is None:
                return f"{current_text} · Last NA"
            previous_value = float(previous_risk.group(1) or previous_risk.group(2))
            delta = current_value - previous_value
            arrow = "↑" if delta > 0.05 else "↓" if delta < -0.05 else "→"
            return f"{current_text} · Last {previous_value:+.0f} · Δ {delta:+.0f}{arrow}"
        if not previous_impact_by_module:
            return f"{current_text} · Last: pehla snapshot"
        previous_text = previous_impact_by_module.get(module, "")
        previous_match = impact_pattern.search(previous_text)
        if previous_match is None:
            return f"{current_text} · Last NA"
        current_points = float(current_match.group(1))
        previous_points = float(previous_match.group(1))
        current_direction = current_match.group(2).title()
        previous_direction = previous_match.group(2).title()
        if current_direction != previous_direction:
            return (
                f"{current_text} · Last {previous_points:.1f} {previous_direction}"
                f" → {current_direction}"
            )
        delta = current_points - previous_points
        arrow = "↑" if delta > 0.05 else "↓" if delta < -0.05 else "→"
        return f"{current_text} · Last {previous_points:.1f} · Δ {delta:+.1f}{arrow}"

    def score_text(value: Any, short: str) -> str:
        return f"{short}{float(value):.0f}" if value is not None else f"{short}—"

    def score_cell(value: Any, tone: str) -> str:
        if value is None:
            return "—"
        numeric = max(0.0, min(100.0, float(value)))
        return (
            f'<div class="evt-score {tone}"><i style="width:{numeric:.0f}%"></i>'
            f'<span>{numeric:.0f}%</span></div>'
        )

    def change_text(row: dict[str, Any]) -> str:
        previous = previous_by_module.get(str(row["Module"]))
        if previous is None:
            return "Pehla snapshot"
        deltas = []
        for key, short in (
            ("Bullish %", "Bull"),
            ("Bearish %", "Bear"),
            ("Neutral %", "Neutral"),
        ):
            current_value = row.get(key)
            previous_value = previous.get(key)
            if current_value is not None and previous_value is not None:
                deltas.append(f"{short}{float(current_value) - float(previous_value):+.0f}")
        if str(row["Module"]) == "NIFTY Top-9":
            current_move = snapshot.heavyweights.weighted_move_pct
            previous_move = previous_snapshot.heavyweights.weighted_move_pct
            if current_move is not None and previous_move is not None:
                elapsed = max(0, round((snapshot.created_at - previous_snapshot.created_at).total_seconds() / 60))
                return f"{elapsed}m {current_move - previous_move:+.3f}%"
        if deltas and any(item[-2:] not in {"+0", "-0"} for item in deltas):
            return "Δ " + " ".join(deltas)
        return "Koi badlav nahi" if row.get("Result") == previous.get("Result") else "Result badla"

    body = []
    detail_rows: list[dict[str, str]] = []
    for row in rows:
        confidence = (
            f"{float(row['Confidence %']):.0f}%"
            if row.get("Confidence %") is not None
            else "—"
        )
        change = change_text(row)
        module = str(row["Module"])
        impact = impact_with_last(
            module, impact_by_module.get(module, "—")
        )
        detail_rows.append(
            {
                "Module": module,
                "One-Brain weight/asar": impact,
                "Result": str(row.get("Result") or "—"),
                "Badlav": change,
            }
        )
        body.append(
            '<tr>'
            f'<td class="evt-module">{escape(str(row["Module"]))}</td>'
            f'<td class="evt-bull">{score_text(row.get("Bullish %"), "")}</td>'
            f'<td class="evt-bear">{score_text(row.get("Bearish %"), "")}</td>'
            f'<td class="evt-neutral">{score_text(row.get("Neutral %"), "")}</td>'
            f'<td class="evt-conf">{confidence}</td>'
            '</tr>'
        )
    table_html = (
        '<style>'
        '.evt-wrap{overflow:hidden;border:1px solid rgba(127,127,127,.24);border-radius:10px}'
        '.evt{width:100%;border-collapse:collapse;table-layout:fixed;font-size:.82rem}'
        '.evt th,.evt td{padding:8px;border-bottom:1px solid rgba(127,127,127,.20);text-align:left;vertical-align:middle;overflow-wrap:anywhere}'
        '.evt th{background:rgba(127,127,127,.09);font-weight:800}'
        '.evt tr:last-child td{border-bottom:0}'
        '.evt-module{width:40%;font-weight:800}.evt-bull,.evt-bear,.evt-neutral{width:13%;text-align:center}.evt-conf{width:21%;text-align:center}'
        '.evt-bull{color:#22c55e}.evt-bear{color:#ef4444}.evt-neutral{color:#a3a3a3}'
        '@media(max-width:760px){'
        '.evt{font-size:.68rem}.evt th,.evt td{padding:7px 3px}'
        '.evt-module{width:38%}.evt-bull,.evt-bear,.evt-neutral{width:14%}.evt-conf{width:20%}'
        '}'
        '</style><div class="evt-wrap"><table class="evt"><thead><tr>'
        '<th>Module</th><th class="evt-bull">Bull</th><th class="evt-bear">Bear</th><th class="evt-neutral">Neutral</th><th>Evidence quality</th>'
        '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
    )
    st.html(table_html) if hasattr(st, "html") else st.markdown(
        table_html, unsafe_allow_html=True
    )
    with st.expander("Weights, result aur last snapshot ka badlav", expanded=False):
        st.dataframe(pd.DataFrame(detail_rows), width="stretch", hide_index=True)
        st.caption(
            f"Current reference: {reference_name}. Barrier extra weight 0; Raw futures 10%; composite Big Player extra 0."
        )



def render_pre_touch_barriers(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.pre_touch_barriers
    st.subheader("Pre-Touch Support / Resistance — Early Warning")
    st.caption(
        "Price ke level touch karke wapas aane ka wait nahi. Yeh existing structure + "
        "Previous Day/Opening Range + CE/PE OI wall ko mila kar pehle se probable zone dikhata hai."
    )
    if bundle.status != "READY":
        st.info("Pre-touch S/R abhi available nahi hai.")
        return

    left, right = st.columns(2)
    with left:
        support = bundle.support
        if support is None:
            st.info("Neeche probable support resolve nahi hua.")
        else:
            st.metric(
                "Probable Support",
                f"{support.lower:,.0f}–{support.upper:,.0f}",
                f"{support.distance_points:.0f} pts door",
            )
            st.caption(
                f"Strength {support.strength:.0f}% | {support.proximity} | "
                + " + ".join(support.sources[:4])
            )
            st.info(support.message)
    with right:
        resistance = bundle.resistance
        if resistance is None:
            st.info("Upar probable resistance resolve nahi hua.")
        else:
            st.metric(
                "Probable Resistance",
                f"{resistance.lower:,.0f}–{resistance.upper:,.0f}",
                f"{resistance.distance_points:.0f} pts door",
            )
            st.caption(
                f"Strength {resistance.strength:.0f}% | {resistance.proximity} | "
                + " + ".join(resistance.sources[:4])
            )
            st.warning(resistance.message)


def _level_summary(level: Any | None, *, fallback: str) -> str:
    if level is None:
        return f"{fallback}: unresolved"
    return (
        f"{level.lower:,.0f}–{level.upper:,.0f} | Bachne ki taakat {level.strength:.0f}/100 | "
        f"Tootne ka pressure {level.break_pressure:.0f}/100 | {_barrier_verdict(level)}"
    )


def _compact_cards_html(cards: list[tuple[str, str, str]]) -> str:
    """Render small responsive cards that stay two-across on narrow phones."""

    blocks = []
    for label, value, note in cards:
        blocks.append(
            '<div class="mai-card">'
            f'<div class="mai-label">{escape(label)}</div>'
            f'<div class="mai-value">{escape(value)}</div>'
            f'<div class="mai-note">{escape(note)}</div>'
            '</div>'
        )
    return (
        '<style>'
        '.mai-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:4px 0 12px}'
        '.mai-card{min-width:0;border:1px solid rgba(127,127,127,.24);border-radius:12px;padding:10px 11px;background:rgba(127,127,127,.045)}'
        '.mai-label{font-size:.76rem;opacity:.72;margin-bottom:4px}'
        '.mai-value{font-size:1.18rem;font-weight:800;line-height:1.15;overflow-wrap:anywhere}'
        '.mai-note{font-size:.70rem;opacity:.68;margin-top:4px;line-height:1.25}'
        '@media (max-width:760px){.mai-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.mai-value{font-size:1.03rem}.mai-card{padding:9px}}'
        '</style><div class="mai-grid">' + ''.join(blocks) + '</div>'
    )


def _render_compact_cards(cards: list[tuple[str, str, str]]) -> None:
    html = _compact_cards_html(cards)
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)


def _plan_structure_text(plan: Any | None) -> str:
    if plan is None or not plan.available:
        return "Strike unresolved"
    if plan.is_buy:
        hedge = (
            f" · HEDGE SELL {plan_leg_text(plan.short_legs)}"
            if plan.short_legs
            else ""
        )
        return f"BUY {plan_leg_text(plan.long_legs)}{hedge}"
    short_text = plan_leg_text(plan.short_legs)
    hedge_text = plan_leg_text(plan.hedge_legs)
    return f"SELL {short_text} · HEDGE {hedge_text}"


def render_protected_candidates(snapshot: MarketSnapshot) -> None:
    """Rank all One-Brain strategies with strike and premium-value context."""

    evaluations = _decision_evaluations(snapshot)
    bundle = snapshot.trade_plan
    plan_map = {
        "CE BUY": bundle.ce_buy,
        "PE BUY": bundle.pe_buy,
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }
    selected = snapshot.decision.final_action.replace(" WITH HEDGE", "")
    leader = max(evaluations, key=lambda name: evaluations[name].score)

    st.subheader("🛡️ Best Strategy + Strike Value Table")
    st.caption(
        "CE BUY, CE SELL, PE BUY, PE SELL aur IRON CONDOR ka same One-Brain comparison. "
        "Fit suitability hai, guaranteed profit nahi. Har directional setup protected/hedged hai."
    )

    def _premium_value(plan: Any | None) -> tuple[str, str]:
        if plan is None or not plan.available:
            return "—", "UNAVAILABLE"
        if plan.is_buy:
            points = float(plan.estimated_debit_points or 0.0)
            premium = f"Debit {points:.2f} pts"
        else:
            points = float(plan.estimated_credit_points or 0.0)
            premium = f"Credit {points:.2f} pts"

        # Display-only value grade. The trade planner already selects strikes by
        # delta, liquidity, distance, spread and hedge efficiency; this grade does
        # not create a second strategy brain or promise a profit.
        quality = float(plan.quality_score or 0.0)
        if quality >= 75 and points >= 5:
            grade = "ACCHI VALUE"
        elif quality >= 58 and points >= 2:
            grade = "THEEK VALUE"
        else:
            grade = "KAMZOR VALUE"
        return premium, grade

    rows: list[dict[str, Any]] = []
    ranked = sorted(evaluations, key=lambda name: evaluations[name].score, reverse=True)
    for rank, name in enumerate(ranked, start=1):
        strategy = evaluations[name]
        plan = plan_map[name]
        premium, value_grade = _premium_value(plan)
        if not snapshot.market_session.is_live:
            status = "REFERENCE ONLY"
        elif snapshot.decision.final_action != "WAIT" and name == selected:
            status = "BEST • ENTRY READY" if plan.available else "BEST • STRIKE BLOCKED"
        elif snapshot.decision.final_action == "WAIT" and name == leader:
            status = "BEST AVAILABLE • WAIT"
        elif not plan.available:
            status = "BLOCKED"
        else:
            status = "ALTERNATIVE"
        decay_reasons = [reason for reason in plan.reasons if reason.startswith("Theta edge")]
        decay_edge = " | ".join(decay_reasons) if decay_reasons else "Theta check —"
        rows.append(
            {
                "Rank": rank,
                "Strategy": name,
                "Fit / Confidence": f"{strategy.score:.0f}%",
                "Strike + Hedge": _plan_structure_text(plan),
                "Premium": premium,
                "Decay Edge": decay_edge,
                "Value Quality": f"{value_grade} • {plan.quality_score:.0f}/100" if plan.available else value_grade,
                "Status": status,
            }
        )
    frame = pd.DataFrame(rows)

    def _strategy_style(row: pd.Series) -> list[str]:
        if str(row["Status"]).startswith("BEST"):
            return ["background-color: rgba(59,130,246,.18);font-weight:700"] * len(row)
        if row["Value Quality"].startswith("KAMZOR") or row["Status"] == "BLOCKED":
            return ["background-color: rgba(239,68,68,.08)"] * len(row)
        return [""] * len(row)

    styled = frame.style.apply(_strategy_style, axis=1)
    st.dataframe(styled, width="stretch", hide_index=True, row_height=42)
    if not snapshot.market_session.is_live:
        st.info("Market live nahi hai—strategy fits sirf frozen reference hain, fresh advice nahi.")
    elif snapshot.decision.final_action == "WAIT":
        st.info(
            "Final Action WAIT hai—table sirf reference ranking hai, koi entry confirmed nahi."
        )
    st.caption(
        "Decay Edge me SELL ka absolute theta hedge se zyada hona better hai. "
        "Value Quality liquidity + delta + premium + hedge quality par based hai. "
        "KAMZOR VALUE setup ko sirf reference samjho; order app kabhi khud place nahi karti."
    )


def _render_final_action_hero(snapshot: MarketSnapshot, feed_ok: bool) -> None:
    decision = snapshot.decision
    name, score, plan, is_selected = best_existing_candidate(snapshot)
    if decision.final_action == "WAIT":
        css_class = "wait"
        title = "WAIT"
        if not snapshot.market_session.is_live:
            subtitle = "REFERENCE ONLY — fresh strategy ranking band"
            structure = snapshot.market_session.message
        else:
            subtitle = "Koi entry confirmed nahi"
            structure = str(decision.blocker or "Evidence conflict / confirmation pending")
    else:
        css_class = "ready"
        title = decision.final_action
        subtitle = f"BEST selected · Brain fit {score:.1f}%"
        structure = _plan_structure_text(plan)
    live_text = "LIVE" if feed_ok else "LAST DATA"
    hero = (
        '<style>'
        '.brain-hero{border-radius:16px;padding:16px 18px;margin:4px 0 12px;border:1px solid rgba(127,127,127,.24)}'
        '.brain-hero.ready{background:rgba(34,197,94,.14);border-color:rgba(34,197,94,.42)}'
        '.brain-hero.wait{background:rgba(245,158,11,.13);border-color:rgba(245,158,11,.42)}'
        '.brain-hero-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}'
        '.brain-hero-label{font-size:.76rem;font-weight:800;letter-spacing:.06em;opacity:.76}'
        '.brain-hero-action{font-size:1.65rem;font-weight:900;line-height:1.15;margin-top:3px}'
        '.brain-hero-badge{font-size:.78rem;font-weight:800;padding:6px 9px;border-radius:99px;background:rgba(127,127,127,.13);white-space:nowrap}'
        '.brain-hero-sub{font-size:.92rem;font-weight:700;margin-top:8px}'
        '.brain-hero-structure{font-size:.82rem;opacity:.82;margin-top:5px;overflow-wrap:anywhere}'
        '@media(max-width:760px){.brain-hero{padding:13px}.brain-hero-action{font-size:1.35rem}.brain-hero-top{gap:8px}.brain-hero-badge{font-size:.70rem}}'
        '</style>'
        f'<div class="brain-hero {css_class}">'
        '<div class="brain-hero-top"><div>'
        '<div class="brain-hero-label">FINAL ONE-BRAIN</div>'
        f'<div class="brain-hero-action">{escape(title)}</div></div>'
        f'<div class="brain-hero-badge">{escape(live_text)}</div></div>'
        f'<div class="brain-hero-sub">{escape(subtitle)}</div>'
        f'<div class="brain-hero-structure">{escape(structure)}</div>'
        '</div>'
    )
    if hasattr(st, "html"):
        st.html(hero)
    else:
        st.markdown(hero, unsafe_allow_html=True)


def _pattern_compact_text(item: Any | None, *, fallback: str) -> tuple[str, str]:
    if item is None or item.status == "UNAVAILABLE":
        return fallback, "No reliable confirmation"
    if item.name in {"NO VALID W/M", "NO IMPORTANT CANDLE"}:
        return item.name, "Neutral / no important pattern"
    note_bits = [item.strength]
    if item.level_value is not None:
        note_bits.append(f"{item.level_label or 'Level'} {item.level_value:,.0f}")
    if item.neckline is not None:
        note_bits.append(f"NL {item.neckline:,.0f}")
    return item.name, " · ".join(note_bits)


def render_main_ai_market_view(
    snapshot: MarketSnapshot, previous_snapshot: MarketSnapshot | None = None
) -> None:
    """Simple top-screen view of the existing canonical MarketSnapshot."""

    barrier = snapshot.barrier_map
    feed_ok, _feed_text = required_live_feed_state(snapshot)
    direction, direction_score, direction_note = market_rukh_display(snapshot)
    spot = snapshot.nifty_quote.get("last_price")

    st.subheader("🧠 Main AI — Simple Trading View")

    critical_feeds = [
        snapshot.feed_status.get(key)
        for key in ("quotes", "candles", "option_chain", "future_volume", "vix")
    ]
    critical_feeds = [item for item in critical_feeds if item is not None]
    data_quality = (
        100.0
        * sum(item.ok and item.use_state not in {"UNAVAILABLE", "STALE"} for item in critical_feeds)
        / len(critical_feeds)
        if critical_feeds
        else 0.0
    )
    final_direction = str(snapshot.decision.market_direction or "RANGE").upper()

    def _evidence_direction(value: str) -> str:
        text = str(value or "").upper()
        if "BULL" in text or "BUYING" in text:
            return "BULLISH"
        if "BEAR" in text or "SELLING" in text:
            return "BEARISH"
        return "RANGE"

    # MIXED / RANGE / RANGE-MIXED are the same neutral direction for agreement.
    final_direction = _evidence_direction(final_direction)

    agreement_sources = (
        _evidence_direction(snapshot.core_evidence.market_state),
        _evidence_direction(snapshot.option_intelligence.market_bias),
        _evidence_direction(snapshot.price_action.combined_state),
        _evidence_direction(snapshot.big_player_activity.direction),
    )
    direction_agreement = round(
        100.0 * sum(item == final_direction for item in agreement_sources) / len(agreement_sources)
    )

    with st.container(border=True):
        _render_final_action_hero(snapshot, feed_ok)
        _render_compact_cards(
            [
                ("NIFTY", f"{float(spot):,.2f}" if spot is not None else "—", "Current / last available"),
                ("Structural Direction", direction, f"Completed evidence {direction_score:.0f}/100 · {direction_note}"),
                ("Feed Availability", f"{data_quality:.0f}%", "Feed ready ≠ prediction accuracy"),
                ("Direction Agreement", f"{direction_agreement:.0f}%", "Core · OI · price · big player"),
                (
                    "Entry Confidence" if snapshot.market_session.is_live else "Reference Confidence",
                    f"{snapshot.decision.decision_confidence:.0f}%",
                    snapshot.execution_guard.readiness,
                ),
            ]
        )

        st.info(
            "🧠 **AI samajh:** "
            + safe_brain_hinglish_line(snapshot, previous_snapshot)
        )
        if "greeks_quality" in snapshot.option_chain:
            invalid_greeks = int(snapshot.option_chain.greeks_quality.ne("READY").sum())
            if invalid_greeks:
                st.warning(f"Greeks check: {invalid_greeks} option rows unavailable/model mismatch. In rows se strike/hedge ranking band; OI/quotes alag usable hain. Broker values force-match nahi ki gayi.")

        patterns = getattr(snapshot, "patterns", None)
        wm_text, _wm_note = _pattern_compact_text(
            patterns.wm_3m if patterns is not None else None,
            fallback="NO VALID W/M",
        )
        candle_text, _candle_note = _pattern_compact_text(
            (
                patterns.candle_3m
                if patterns is not None
                else None
            ),
            fallback="NO IMPORTANT CANDLE",
        )

        inst = snapshot.institutional_context
        if inst.observations <= 0:
            inst_text = "MISSING"
        elif "FII SELLING / DII ABSORPTION" in inst.state:
            inst_text = "FII SELL · DII BUY"
        elif "FII BUYING / DII SELLING" in inst.state:
            inst_text = "FII BUY · DII SELL"
        elif "SUPPORT" in inst.state:
            inst_text = "NET SUPPORT"
        elif "PRESSURE" in inst.state:
            inst_text = "NET PRESSURE"
        else:
            inst_text = "MIXED"

        heavy = snapshot.heavyweights
        top7_move = (
            f"15m {heavy.recent_15m_move_pct:+.2f}%"
            if heavy.recent_15m_move_pct is not None
            else "15m WARMING UP"
        )
        news_display = normalized_news_display(snapshot.news_context)
        if news_display.status == "READY":
            news_text = f"{news_display.risk} RISK"
        else:
            news_text = "ZERO LIVE WEIGHT"
        st.caption(
            f"W/M: {wm_text} • Candle: {candle_text} • Top-9: {top7_move} • "
            f"FII/DII background: {inst_text} • News: {news_text}"
        )
        activity = getattr(snapshot, "big_player_activity", None)
        if activity is not None:
            activity_type = str(getattr(activity, "activity_type", ""))
            closing_flow = activity_type in {"SHORT COVERING", "LONG UNWINDING"}
            icon = "🟡" if closing_flow else "🟢" if activity.direction == "BUYING" else "🔴" if activity.direction == "SELLING" else "🟣"
            display_direction = activity_type if closing_flow else activity.direction
            confirmation_text = (
                "No danger confirmation"
                if activity.state == "NORMAL"
                else f"{activity.confirmation_count}/{activity.confirmation_total}"
            )
            activity_line = (
                f"{icon} **Big Player:** {activity.state} {display_direction} "
                f"{activity.score:.0f}/100 · {confirmation_text} "
                f"· Reversal {activity.reversal_risk} · Recent 3m flow"
            )
            if closing_flow and activity.score >= 60:
                st.warning(activity_line)
            elif activity.confirmation_count >= 2 and activity.score >= 75 and activity.direction == "SELLING":
                st.error(activity_line)
            elif activity.confirmation_count >= 2 and activity.score >= 75 and activity.direction == "BUYING":
                st.success(activity_line)
            elif activity.score >= 60:
                st.warning(activity_line)
            else:
                st.caption(activity_line)
            if closing_flow:
                st.caption("↳ " + activity.participant_explanation)

        resistance = _level_summary(barrier.nearest_resistance, fallback="R1")
        support = _level_summary(barrier.nearest_support, fallback="S1")
        st.caption(f"🔴 Resistance: {resistance}   |   🟢 Support: {support}")

        with st.expander("Last snapshot se badlav", expanded=False):
            changes = snapshot_change_items(snapshot, previous_snapshot)
            if changes:
                cards = [
                    (label, value, f"Badlav {delta}" if delta else "No comparable delta")
                    for label, value, delta in changes
                ]
                _render_compact_cards(cards)
            st.caption(snapshot_change_hinglish(snapshot, previous_snapshot))


def render_big_player_activity(snapshot: MarketSnapshot) -> None:
    item = getattr(snapshot, "big_player_activity", None)
    if item is None:
        return

    if item.price_shock_state != "NONE":
        shock_note = (
            "Badi price move mili, lekin heavy participation alag se confirm hona zaroori hai."
            if item.confirmation_count < 2
            else "Badi price move aur activity evidence dono mile."
        )
        st.warning(
            f"⚡ **{item.price_shock_state} — {item.price_shock_points or 0:.1f} points** · {shock_note}"
        )
    if item.frozen_after_close:
        st.info("🔒 **LAST LIVE ACTIVITY — REFERENCE ONLY** · Market close/CAS ke baad score freeze hai.")

    activity_type = str(getattr(item, "activity_type", "DIRECTIONAL ACTIVITY"))
    closing_flow = activity_type in {"SHORT COVERING", "LONG UNWINDING"}
    if closing_flow:
        direction_class, icon = "closing", "🟡"
        display_direction = activity_type
    elif item.direction == "BUYING":
        direction_class, icon = "buy", "🟢"
        display_direction = item.direction
    elif item.direction == "SELLING":
        direction_class, icon = "sell", "🔴"
        display_direction = item.direction
    else:
        direction_class, icon = "mixed", "🟣"
        display_direction = item.direction
    simple_state = {
        "NORMAL": "NORMAL ACTIVITY",
        "WATCH": "WATCH — MODERATE",
        "STRONG": "STRONG ACTIVITY",
        "VERY STRONG": "VERY STRONG ACTIVITY",
        "EXTREME ACTIVITY": "BAHUT TEZ HALCHAL",
        "ABSORPTION": "VOLUME BADA, PRICE RUKI",
        "FADING": "ZOR KAM HO RAHA",
    }.get(item.state, item.state)
    if item.confirmation_count < 2 and item.state not in {"NORMAL", "FADING"}:
        simple_state = "ACTIVITY WATCH — UNCONFIRMED"
    simple_direction = {
        "BUYING": "BUYING",
        "SELLING": "SELLING",
        "MIXED": "ABHI SAAF NAHI",
        "SHORT COVERING": "PURANE SELLER POSITION BAND KAR RAHE",
        "LONG UNWINDING": "PURANE BUYER POSITION BAND KAR RAHE",
    }.get(display_direction, display_direction)
    severity = (
        "extreme" if item.state == "EXTREME ACTIVITY" else
        "danger" if item.state == "VERY STRONG" else
        "strong" if item.state == "STRONG" else
        "watch" if item.state in {"WATCH", "ABSORPTION"} else
        "normal"
    )
    if item.confirmation_count < 2:
        severity = "watch"
    st.caption(f"Price response: {item.price_response} · Persistence is not activity magnitude")
    volume_text = f"{item.futures_volume_ratio:.2f}x" if item.futures_volume_ratio is not None else "—"
    oi_text = f"{item.futures_oi_change_pct:+.2f}%" if item.futures_oi_change_pct is not None else "—"
    confirmation_text = (
        "No large-activity confirmation"
        if item.state == "NORMAL"
        else f"{item.persistence} {item.confirmation_count}/{item.confirmation_total}"
    )
    if item.frozen_after_close:
        confirmation_text = "LAST LIVE · REFERENCE ONLY · " + confirmation_text
    html = (
        '<style>'
        '.bpa-hero{border:2px solid rgba(127,127,127,.28);border-radius:16px;padding:14px;margin:5px 0 12px;background:rgba(127,127,127,.05)}'
        '.bpa-hero.buy{border-color:#86efac;background:rgba(34,197,94,.07)}'
        '.bpa-hero.buy.strong{border-color:#22c55e;background:rgba(34,197,94,.13)}'
        '.bpa-hero.buy.danger,.bpa-hero.buy.extreme{border-color:#15803d;background:rgba(21,128,61,.18)}'
        '.bpa-hero.sell{border-color:#facc15;background:rgba(250,204,21,.07)}'
        '.bpa-hero.sell.strong{border-color:#f97316;background:rgba(249,115,22,.13)}'
        '.bpa-hero.sell.danger,.bpa-hero.sell.extreme{border-color:#ef4444;background:rgba(239,68,68,.17)}'
        '.bpa-hero.closing{border-color:#f59e0b;background:rgba(245,158,11,.13)}'
        '.bpa-hero.closing.danger,.bpa-hero.closing.extreme{box-shadow:0 0 0 3px rgba(245,158,11,.15),0 0 20px rgba(245,158,11,.20)}'
        '.bpa-hero.mixed{border-color:#a855f7;background:rgba(168,85,247,.09)}'
        '.bpa-hero.buy.extreme{box-shadow:0 0 0 3px rgba(21,128,61,.18),0 0 22px rgba(21,128,61,.24)}'
        '.bpa-hero.sell.extreme{box-shadow:0 0 0 3px rgba(239,68,68,.18),0 0 22px rgba(239,68,68,.25)}'
        '.bpa-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}'
        '.bpa-title{font-size:1.35rem;font-weight:900}.bpa-score{font-size:1.65rem;font-weight:950;white-space:nowrap}'
        '.bpa-sub{font-size:.85rem;font-weight:750;margin-top:5px;opacity:.85}'
        '.bpa-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-top:12px}'
        '.bpa-cell{border-radius:10px;padding:9px;background:rgba(127,127,127,.09);min-width:0}'
        '.bpa-label{font-size:.69rem;font-weight:800;opacity:.68;text-transform:uppercase}'
        '.bpa-value{font-size:.88rem;font-weight:850;margin-top:3px;overflow-wrap:anywhere}'
        '@media(max-width:760px){.bpa-head{display:block}.bpa-score{font-size:1.35rem;margin-top:6px}.bpa-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.bpa-title{font-size:1.12rem}}'
        '</style>'
        f'<div class="bpa-hero {direction_class} {severity}">'
        f'<div class="bpa-head"><div><div class="bpa-title">{icon} {escape(simple_state)} · {escape(simple_direction)}</div>'
        f'<div class="bpa-sub">{escape(confirmation_text)} · Reversal risk {escape(item.reversal_risk)} · {escape(item.time_window)}</div></div>'
        f'<div class="bpa-score">{item.score:.0f}/100</div></div>'
        '<div class="bpa-grid">'
        f'<div class="bpa-cell"><div class="bpa-label">Buying</div><div class="bpa-value">{item.buy_score:.0f}/100</div></div>'
        f'<div class="bpa-cell"><div class="bpa-label">Selling</div><div class="bpa-value">{item.sell_score:.0f}/100</div></div>'
        f'<div class="bpa-cell"><div class="bpa-label">Futures Volume</div><div class="bpa-value">{volume_text}</div></div>'
        f'<div class="bpa-cell"><div class="bpa-label">Futures OI</div><div class="bpa-value">{oi_text} · {escape(item.futures_setup)}</div></div>'
        '</div></div>'
    )
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)

    cards = [
        ("Seedha matlab", item.move_state, f"Pichhle candles ka move {item.move_points or 0:.1f} points; kam-se-kam {item.required_move_points:.0f} chahiye", "green" if "PAKKI" in item.move_state else "amber"),
        ("Players kya kar rahe", item.participant_explanation, "Price + futures OI ka seedha matlab", "amber" if closing_flow else "green" if item.direction == "BUYING" else "red" if item.direction == "SELLING" else "amber"),
        ("Ab kya dekhna hai", item.next_confirmation, "Iske baad badi halchal ko pakka maanenge", "amber"),
        ("Options ka saath", item.option_confirmation, "Options kis taraf zor dikha rahe", "green" if item.direction == "BUYING" else "red" if item.direction == "SELLING" else "amber"),
        ("Top-9 ka saath", item.top7_confirmation, "Sirf madadgar hai; final direction akela nahi banata", "green" if "BULL" in item.top7_confirmation else "red" if "BEAR" in item.top7_confirmation else "amber"),
        ("Level par kya hua", item.level_reaction, "Support/resistance ke paas reaction", "amber"),
        ("Din ka samay", item.time_window, "Samay sirf sensitivity badalta hai", "amber"),
    ]
    st.html(_responsive_cards_html(cards)) if hasattr(st, "html") else st.markdown(_responsive_cards_html(cards), unsafe_allow_html=True)
    if item.reasons:
        st.caption("Kyun: " + " | ".join(item.reasons))
    if item.cautions:
        st.caption("Caution: " + " | ".join(item.cautions))
    st.caption(
        "Exact institution ki pehchan nahi hoti. Yeh same One-Brain snapshot ka bounded activity evidence hai; "
        "2 alag observations ke bina badi halchal pakki nahi."
    )


def render_compact_protected_setup(snapshot: MarketSnapshot) -> None:
    """Show one compact protected setup row without duplicating the full planner."""

    st.subheader("Best Protected Setup — One-Brain Reference")
    st.caption(
        "Same Final One-Brain scores aur same option-chain snapshot. WAIT ho to candidate sirf reference hai; "
        "full planner neeche detail expander me rahega."
    )
    name, score, plan, is_selected = best_existing_candidate(snapshot)
    if plan is None or not plan.available:
        st.info("Protected CE/PE/Condor candidate abhi available nahi hai.")
        return

    evaluation = _decision_evaluations(snapshot).get(name)
    caution = (
        evaluation.cautions[0]
        if evaluation is not None and evaluation.cautions
        else "Koi extra caution nahi"
    )
    entry_allowed = snapshot.execution_guard.readiness == "ENTRY READY" and is_selected
    premium = (
        f"Credit {plan.estimated_credit_points:.2f} pts"
        if plan.estimated_credit_points is not None
        else f"Debit {plan.estimated_debit_points:.2f} pts"
        if plan.estimated_debit_points is not None
        else "Premium —"
    )
    _render_compact_cards(
        [
            ("Best / Reference Setup", name, f"Fit {score:.1f}% · {'Selected' if is_selected else 'Reference only'}"),
            ("Exact Structure", _plan_structure_text(plan), f"Quality {plan.quality_score:.0f}/100"),
            ("Premium / Max Risk", premium, f"Max risk {plan.max_risk_points:.2f} pts" if plan.max_risk_points is not None else "Max risk —"),
            ("Entry Allowed", "YES" if entry_allowed else "NO — WAIT", caution),
        ]
    )
    if snapshot.decision.final_action == "WAIT":
        st.info("Final Action WAIT hai — yeh strike pair sirf reference hai, entry signal nahi.")
    invalidation = candidate_invalidation_text(snapshot, name)
    if invalidation:
        st.caption("Invalidation: " + invalidation)


def render_best_protected_sells(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.trade_plan
    st.subheader("Best CE / PE Sell + Hedge")
    st.caption(
        "Yeh second brain nahi hai. Final One-Brain ke same option-chain snapshot se best short strike "
        "aur uske liye liquid farther-OTM hedge choose hota hai. Har fresh snapshot par update hota hai."
    )

    rows = []
    score_map = {
        "CE SELL": snapshot.decision.ce_sell.score,
        "PE SELL": snapshot.decision.pe_sell.score,
    }
    for plan in (bundle.ce_sell, bundle.pe_sell):
        short = plan.short_legs[0] if plan.short_legs else None
        hedge = plan.hedge_legs[0] if plan.hedge_legs else None
        rows.append(
            {
                "Setup": plan.name,
                "Brain score %": score_map.get(plan.name, 0.0),
                "Sell": f"{short.strike:,.0f} {short.side}" if short else "—",
                "Buy hedge": f"{hedge.strike:,.0f} {hedge.side}" if hedge else "—",
                "Credit pts": plan.estimated_credit_points,
                "Max risk pts": plan.max_risk_points,
                "Strike + hedge quality /100": plan.quality_score,
                "Status": plan.status,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    selected = snapshot.decision.final_action.replace(" WITH HEDGE", "")
    selected_plan = {"CE SELL": bundle.ce_sell, "PE SELL": bundle.pe_sell}.get(selected)
    if snapshot.decision.final_action == "WAIT":
        st.info("Brain ka final action WAIT hai — CE/PE strikes sirf reference hain, entry signal nahi.")
    elif selected_plan and selected_plan.available:
        short = selected_plan.short_legs[0]
        hedge = selected_plan.hedge_legs[0]
        st.success(
            f"Brain Pick: {selected} WITH HEDGE — SELL {short.strike:,.0f} {short.side} "
            f"+ BUY {hedge.strike:,.0f} {hedge.side} hedge | "
            f"Quality {selected_plan.quality_score:.0f}% | Est. credit {selected_plan.estimated_credit_points:.2f} pts"
        )
    else:
        st.warning("Brain ne directional setup choose kiya hai, lekin safe hedge wala valid spread abhi resolve nahi hua.")


def render_news_context(snapshot: MarketSnapshot) -> None:
    news = snapshot.news_context
    status = str(news.status).upper()
    risk = str(news.risk_level).upper()
    bias = str(news.bias).upper()
    if status == "READY":
        points = {"HIGH": 9, "MEDIUM": 5, "LOW": 2}.get(risk, 0)
    elif status == "OLD":
        points = min(2, {"HIGH": 9, "MEDIUM": 5, "LOW": 2}.get(risk, 0))
    else:
        points = 0
    severity = (
        "DANGEROUS" if risk == "HIGH" and status == "READY"
        else "CAUTION" if points >= 3 or status == "OLD"
        else "NORMAL" if points > 0
        else "NO LIVE NEWS"
    )
    signed = -points if bias == "BEARISH" else points if bias == "BULLISH" else 0
    age = (
        f"{news.newest_age_minutes:.0f}m"
        if news.newest_age_minutes is not None
        else "NA"
    )
    st.info(
        f"**News Indicator:** {bias} • {severity} • Asar {signed:+d}/9 • "
        f"{status} • Age {age}"
    )


def _decision_evaluations(snapshot: MarketSnapshot) -> dict[str, Any]:
    item = snapshot.decision
    return {
        "CE BUY": item.ce_buy,
        "PE BUY": item.pe_buy,
        "CE SELL": item.ce_sell,
        "PE SELL": item.pe_sell,
        "IRON CONDOR": item.iron_condor,
    }


def render_decision(
    snapshot: MarketSnapshot, *, audit_only: bool = False
) -> None:
    item = snapshot.decision
    evaluations = _decision_evaluations(snapshot)
    selected_name = item.final_action.replace(" WITH HEDGE", "")
    leader_name = max(evaluations, key=lambda name: evaluations[name].score)
    displayed_name = selected_name if selected_name in evaluations else leader_name
    displayed_score = evaluations[displayed_name].score

    if audit_only:
        st.subheader("Strategy Audit")
        bundle = snapshot.trade_plan
        plan_map = {
            "CE BUY": bundle.ce_buy,
            "PE BUY": bundle.pe_buy,
            "CE SELL": bundle.ce_sell,
            "PE SELL": bundle.pe_sell,
            "IRON CONDOR": bundle.iron_condor,
        }
        rows = []
        for name, strategy in evaluations.items():
            plan = plan_map.get(name)
            if plan is not None and plan.available:
                if plan.is_buy:
                    premium = (
                        f"Debit {plan.estimated_debit_points:.2f}"
                        if plan.estimated_debit_points is not None
                        else "—"
                    )
                else:
                    premium = (
                        f"Credit {plan.estimated_credit_points:.2f}"
                        if plan.estimated_credit_points is not None
                        else "—"
                    )
            else:
                premium = "—"

            pick = (
                "BEST"
                if item.final_action != "WAIT" and name == selected_name
                else "REFERENCE"
                if item.final_action == "WAIT" and name == leader_name
                else ""
            )
            rows.append(
                {
                    "Setup": name,
                    "Fit %": strategy.score,
                    "Structure": _plan_structure_text(plan),
                    "Premium": premium,
                    "Main caution": strategy.cautions[0] if strategy.cautions else "None",
                    "Status": strategy.status,
                    "_pick": pick,
                }
            )

        rows.sort(key=lambda row: float(row["Fit %"]), reverse=True)
        compact_rows = []
        for row in rows:
            pick = str(row["_pick"])
            status = "ENTRY" if pick == "BEST" else "BEST AVAILABLE • WAIT" if pick == "REFERENCE" else "AVOID"
            compact_rows.append(
                {
                    "Strategy": row["Setup"],
                    "Fit": f"{float(row['Fit %']):.0f}%",
                    "Structure": row["Structure"],
                    "Credit/Debit": row["Premium"],
                    "Status": status,
                }
            )
        st.dataframe(compact_rows, width="stretch", hide_index=True)
        if item.final_action == "WAIT":
            st.info(
                f"🟡 WAIT — {leader_name} best available setup hai, lekin confirmation complete nahi."
            )
        else:
            st.success(
                f"🟢 {item.final_action} · Fit {displayed_score:.1f}%"
            )
        return

    st.subheader("Final One-Brain Decision")
    st.caption(
        "Same AI Brain CE Buy, PE Buy, CE Sell, PE Sell aur Iron Condor ko compare karta hai. "
        "Sirf ek final action aata hai; scores suitability hain, guaranteed probability nahi."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Best Setup", item.final_action)
    c2.metric(
        "Brain Fit",
        f"{displayed_score:.1f}%" if item.final_action != "WAIT" else f"{displayed_score:.1f}% ref",
    )
    c3.metric("Decision Confidence", f"{item.decision_confidence:.1f}%")
    c4.metric("Signal State", item.signal_state)

    instant_note = (
        f" | Instant read: {item.instant_action}"
        if item.instant_action != item.final_action
        else ""
    )
    message = (
        f"FINAL ACTION: {item.final_action} | Execution: {item.execution_status} | "
        f"Hedge required: {'YES' if item.hedge_required else 'NO'}{instant_note}"
    )
    if item.final_action == "WAIT":
        st.warning(message)
    else:
        st.success(message)

    st.info("🧠 **Brain samjha raha hai:** " + safe_brain_hinglish_line(snapshot))

    left, right = st.columns(2)
    with left:
        st.write("**Top reasons**")
        for reason in item.reasons or ("No decisive evidence",):
            st.write(f"• {reason}")
    with right:
        st.write("**Main blocker**")
        st.write(f"• {display_main_blocker(snapshot)}")

    with st.expander("All 5 strategy scores & cautions", expanded=False):
        rows = []
        for strategy in (*evaluations.values(), item.wait_need):
            pick = (
                "BEST"
                if item.final_action != "WAIT" and strategy.name == selected_name
                else "REFERENCE LEADER"
                if item.final_action == "WAIT" and strategy.name == leader_name
                else ""
            )
            rows.append(
                {
                    "Setup": strategy.name,
                    "Fit / Need %": strategy.score,
                    "Pick": pick,
                    "Status": strategy.status,
                    "Key evidence": " | ".join(strategy.reasons),
                    "Cautions": " | ".join(strategy.cautions) or "None",
                }
            )
        frame = pd.DataFrame(rows)

        def _score_row(row: pd.Series) -> list[str]:
            if row["Pick"] == "BEST":
                return [
                    "background-color: rgba(34, 197, 94, 0.18); font-weight: 700"
                ] * len(row)
            if row["Pick"] == "REFERENCE LEADER":
                return ["background-color: rgba(245, 158, 11, 0.14)"] * len(row)
            return [""] * len(row)

        styled = frame.style.apply(_score_row, axis=1).format(
            {"Fit / Need %": "{:.1f}%"}, na_rep="—"
        )
        st.dataframe(styled, width="stretch", hide_index=True, row_height=44)

def render_market_outlook(snapshot: MarketSnapshot) -> None:
    item = snapshot.decision.outlook
    st.subheader("Next 5–15 Min Market Outlook")
    outlook_note = (
        " Session live confirm nahi hai, isliye fake-move score sirf reference hai; data/session block alag se entry rokta hai."
        if item.status == "REFERENCE ONLY"
        else ""
    )
    st.caption(
        "Conditional scenario weights from the same Final One-Brain Decision. "
        "They are not guaranteed price predictions. Koi path absolute 0%/100% nahi maana jata. "
        "Signal memory and fake-move risk single opposite snapshot se action flip hone se rokte hain."
        + outlook_note
    )
    trade_status = (
        "ENTRY READY"
        if snapshot.execution_guard.readiness == "ENTRY READY"
        else "DATA READY — TRADE WAIT"
    )
    cards = [
        ("Upar ka rasta", f"{item.bullish_path_pct:.1f}%", "Conditional, guarantee nahi", "green" if item.bullish_path_pct >= 55 else ""),
        ("Range ka rasta", f"{item.range_path_pct:.1f}%", f"Signal memory {item.signal_memory}", "amber" if item.range_path_pct >= 55 else ""),
        ("Neeche ka rasta", f"{item.bearish_path_pct:.1f}%", "Conditional, guarantee nahi", "red" if item.bearish_path_pct >= 55 else ""),
        ("Fake-move risk", f"{item.fake_move_risk:.1f}% · {item.fake_move_state}", trade_status, "red" if item.fake_move_risk >= 60 else ""),
    ]
    html = _responsive_cards_html(cards)
    st.html(html) if hasattr(st, "html") else st.markdown(html, unsafe_allow_html=True)
    st.caption(f"Break confirm/invalid: {item.invalidation_text} · Status: {trade_status}")
    if item.reasons:
        st.caption("Fake-move checks: " + " | ".join(item.reasons))


def _leg_label(legs: tuple[Any, ...], *, prefix: str = "") -> str:
    if not legs:
        return "—"
    text = " + ".join(f"{leg.strike:,.0f} {leg.side}" for leg in legs)
    return f"{prefix} {text}".strip()


def render_trade_plan(
    snapshot: MarketSnapshot, *, compact: bool = False, max_rows: int = 5
) -> None:
    bundle = snapshot.trade_plan
    evaluations = _decision_evaluations(snapshot)
    score_map = {name: float(item.score) for name, item in evaluations.items()}
    selected = bundle.selected_setup
    reference_leader = max(score_map, key=score_map.get)
    display_pick = selected if selected in score_map else reference_leader

    plan_map = {
        "CE BUY": bundle.ce_buy,
        "PE BUY": bundle.pe_buy,
        "CE SELL": bundle.ce_sell,
        "PE SELL": bundle.pe_sell,
        "IRON CONDOR": bundle.iron_condor,
    }
    ordered_names = sorted(
        plan_map, key=lambda name: score_map.get(name, 0.0), reverse=True
    )

    if compact:
        st.subheader("🎯 AI Strategy Planner — Top 3")
        st.caption(
            "Same One-Brain ke top setups. Fit suitability hai, guaranteed chance nahi. "
            "Green sirf selected BEST; WAIT me amber reference leader."
        )
        rows = []
        for name in ordered_names[: max(1, int(max_rows))]:
            plan = plan_map[name]
            if selected != "WAIT" and name == selected:
                pick = "BEST"
                status = "BEST"
            elif selected == "WAIT" and name == reference_leader:
                pick = "REFERENCE"
                status = "REFERENCE"
            else:
                pick = ""
                status = "AVAILABLE" if plan.available else "BLOCKED"
            rows.append(
                {
                    "Setup": name,
                    "Fit %": score_map.get(name, 0.0),
                    "Strike / Structure": _plan_structure_text(plan),
                    "Quality %": plan.quality_score if plan.available else None,
                    "Status": status,
                    "_pick": pick,
                }
            )
        frame = pd.DataFrame(rows)

        def _compact_row(row: pd.Series) -> list[str]:
            if row["_pick"] == "BEST":
                return [
                    "background-color: rgba(34, 197, 94, 0.20); font-weight: 700"
                ] * len(row)
            if row["_pick"] == "REFERENCE":
                return ["background-color: rgba(245, 158, 11, 0.14)"] * len(row)
            return [""] * len(row)

        styled = (
            frame.style.apply(_compact_row, axis=1)
            .hide(axis="columns", subset=["_pick"])
            .format(
                {
                    "Fit %": "{:.1f}%",
                    "Quality %": lambda value: "—"
                    if pd.isna(value)
                    else f"{value:.1f}%",
                },
                na_rep="—",
            )
        )
        st.dataframe(styled, width="stretch", hide_index=True, row_height=42)
        if selected == "WAIT":
            st.info(
                f"Final One-Brain WAIT hai. {reference_leader} sirf reference leader hai; entry approval nahi."
            )
        elif bundle.blocker != "None":
            st.warning(f"Planner blocker: {bundle.blocker}")
        return

    st.subheader("AI Strategy & Protected Strike Planner")
    st.caption(
        "Same Final One-Brain 5 setups compare karta hai. Planner decision dobara nahi banata; "
        "sirf selected setup ka liquid strike aur seller trade me mandatory hedge nikalta hai."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected Setup", selected)
    c2.metric(
        "Brain Fit",
        f"{score_map.get(display_pick, 0.0):.1f}%"
        + (" ref" if selected == "WAIT" else ""),
    )
    c3.metric("Expiry", bundle.expiry or "—")
    c4.metric("Spot", f"{bundle.spot:,.2f}" if bundle.spot is not None else "—")
    st.caption(f"Planner status: {bundle.status}")

    rows = []
    for name in ordered_names:
        plan = plan_map[name]
        breakeven = "—"
        if plan.lower_breakeven is not None and plan.upper_breakeven is not None:
            breakeven = (
                f"{plan.lower_breakeven:,.2f} to {plan.upper_breakeven:,.2f}"
            )
        elif plan.lower_breakeven is not None:
            breakeven = f"Lower {plan.lower_breakeven:,.2f}"
        elif plan.upper_breakeven is not None:
            breakeven = f"Upper {plan.upper_breakeven:,.2f}"

        if plan.is_buy:
            primary = _leg_label(plan.long_legs, prefix="BUY")
            protection = _leg_label(plan.short_legs, prefix="SELL")
            premium = (
                f"Debit {plan.estimated_debit_points:.2f}"
                if plan.estimated_debit_points is not None
                else "—"
            )
        else:
            primary = _leg_label(plan.short_legs, prefix="SELL")
            protection = _leg_label(plan.hedge_legs, prefix="BUY")
            premium = (
                f"Credit {plan.estimated_credit_points:.2f}"
                if plan.estimated_credit_points is not None
                else "—"
            )

        if selected != "WAIT" and name == selected:
            pick = "BEST"
            status = f"BEST · {plan.status}"
        elif selected == "WAIT" and name == reference_leader:
            pick = "REFERENCE"
            status = "REFERENCE BEST"
        else:
            pick = ""
            status = plan.status
        rows.append(
            {
                "Setup": name,
                "Primary leg(s)": primary,
                "Protection": protection,
                "Premium": premium,
                "Max risk pts": plan.max_risk_points,
                "Breakeven / Range": breakeven,
                "Brain fit %": score_map.get(name, 0.0),
                "Leg quality %": plan.quality_score,
                "Status": status,
                "_pick": pick,
            }
        )

    frame = pd.DataFrame(rows)

    def _planner_row(row: pd.Series) -> list[str]:
        if row["_pick"] == "BEST":
            return [
                "background-color: rgba(34, 197, 94, 0.20); font-weight: 700"
            ] * len(row)
        if row["_pick"] == "REFERENCE":
            return ["background-color: rgba(245, 158, 11, 0.14)"] * len(row)
        return [""] * len(row)

    styled = (
        frame.style.apply(_planner_row, axis=1)
        .hide(axis="columns", subset=["_pick"])
        .format(
            {
                "Max risk pts": lambda value: "—"
                if pd.isna(value)
                else f"{value:.2f}",
                "Brain fit %": "{:.1f}%",
                "Leg quality %": "{:.1f}%",
            },
            na_rep="—",
        )
    )
    st.dataframe(styled, width="stretch", hide_index=True, row_height=40)

    chosen = plan_map.get(selected)
    if chosen and chosen.available:
        st.write("**Selected-plan evidence**")
        for reason in chosen.reasons or ("No candidate reason available",):
            st.write(f"• {reason}")
    elif selected == "WAIT":
        st.info(
            f"Final action WAIT hai. {reference_leader} {score_map[reference_leader]:.1f}% "
            "sirf reference leader hai, green approval nahi."
        )
    if bundle.blocker != "None":
        st.warning(f"Planner blocker: {bundle.blocker}")


def render_execution_guard(snapshot: MarketSnapshot) -> None:
    item = snapshot.execution_guard
    profile = snapshot.risk_profile
    state = snapshot.discipline_state
    st.subheader("Execution Guard & One-Trade Discipline")
    st.caption(
        "This is not a second strategy brain. It applies signal persistence, fresh-feed, "
        "entry-window, protected-risk budget and one-trade/day rules to the already "
        "selected final action. It never places or exits an order."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entry Readiness", item.readiness)
    c2.metric("Signal Persistence", item.signal_state)
    c3.metric("Risk Budget", f"₹{item.risk_budget_rupees:,.0f}")
    c4.metric("Allowed Lots", str(item.allowed_lots))

    message = (
        f"Setup: {item.selected_setup} | Entry window: {item.entry_window} | "
        f"Compulsory exit: {item.forced_exit_time} | "
        f"One-trade state: {state.last_outcome or 'NOT USED'}"
    )
    if item.readiness == "ENTRY READY":
        st.success(message)
    elif item.readiness == "WATCH":
        st.info(message)
    else:
        st.warning(message)

    risk_rows = [
        {
            "Capital ₹": profile.capital_rupees,
            "Risk %": profile.risk_pct,
            "Risk budget ₹": item.risk_budget_rupees,
            "Lot size": profile.lot_size,
            "Risk / lot ₹": item.risk_per_lot_rupees,
            "Budget lots": item.max_lots_by_budget,
            "Lot cap": item.max_lots_cap,
            "Allowed lots": item.allowed_lots,
            "Target capture pts": item.target_capture_points,
            "Target option value pts": item.target_exit_debit_points,
            "Target ₹": item.target_profit_rupees,
            "SL trigger pts": item.stop_loss_points,
            "SL option value pts": item.stop_exit_debit_points,
            "SL ₹": item.stop_loss_rupees,
        }
    ]
    st.dataframe(pd.DataFrame(risk_rows), width="stretch", hide_index=True)

    low = (
        f"Below {item.spot_invalidation_low:,.2f}"
        if item.spot_invalidation_low is not None
        else "—"
    )
    high = (
        f"Above {item.spot_invalidation_high:,.2f}"
        if item.spot_invalidation_high is not None
        else "—"
    )
    st.caption(
        f"Spot invalidation guide — downside: **{low}** | upside: **{high}**. "
        "Premium-based triggers are estimates; verify broker bid/ask and fills."
    )

    left, right = st.columns(2)
    with left:
        st.write("**Guard evidence**")
        for reason in item.reasons or ("No positive readiness evidence",):
            st.write(f"• {reason}")
    with right:
        st.write("**Guard blockers**")
        for blocker in item.blockers or ("None",):
            st.write(f"• {blocker}")


def render_position_guardian(snapshot: MarketSnapshot) -> None:
    item = snapshot.position_guardian
    st.subheader("Position Guardian — Manual Trade Monitor")
    st.caption(
        "This monitor starts only after you manually mark a protected trade taken. "
        "It tracks the exact stored legs from the same full option-chain snapshot and "
        "shows deterministic target, SL, spot-invalidation and time-exit alerts. It "
        "cannot place, modify or exit a broker order."
    )

    if item.status == "IDLE":
        st.info(
            "No open trade is recorded. Position monitoring will start after an ENTRY READY setup is manually marked taken."
        )
        return

    is_buy = item.action in {"CE BUY", "PE BUY"}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Guardian Instruction", item.instruction)
    c2.metric(
        "Current Option Value" if is_buy else "Current Combo Debit",
        f"{item.current_debit_points:.2f} pts"
        if item.current_debit_points is not None
        else "—",
    )
    c3.metric(
        "P&L Estimate",
        f"₹{item.unrealized_pnl_rupees:,.0f}"
        if item.unrealized_pnl_rupees is not None
        else "—",
        f"{item.unrealized_pnl_points:+.2f} pts"
        if item.unrealized_pnl_points is not None
        else None,
    )
    c4.metric(
        "Target Progress",
        f"{item.target_progress_pct:.1f}%"
        if item.target_progress_pct is not None
        else "—",
    )

    entry_label = "Entry debit" if is_buy else "Entry credit"
    summary = (
        f"Action: {item.action or '—'} | Expiry: {item.expiry or '—'} | "
        f"Lots: {item.lots} × {item.lot_size} | {entry_label}: "
        f"{item.entry_credit_points:.2f} pts"
        if item.entry_credit_points is not None
        else f"Action: {item.action or '—'} | Expiry: {item.expiry or '—'} | Lots: {item.lots} × {item.lot_size}"
    )
    if item.status in {"EXIT ALERT", "TARGET ALERT"}:
        st.warning(summary)
    elif item.status == "DATA BLOCKED":
        st.error(summary)
    elif item.status == "CLOSED":
        st.info(summary)
    else:
        st.success(summary)

    rule_rows = [
        {
            "Entry spot": item.entry_spot,
            "Current spot": item.current_spot,
            "Target option value": item.target_exit_debit_points,
            "SL option value": item.stop_exit_debit_points,
            "Spot invalidation low": item.spot_invalidation_low,
            "Spot invalidation high": item.spot_invalidation_high,
            "Compulsory exit": item.forced_exit_time,
            "Status": item.status,
        }
    ]
    st.dataframe(pd.DataFrame(rule_rows), width="stretch", hide_index=True)

    if item.legs:
        leg_rows = [
            {
                "Role": leg.role,
                "Side": leg.side,
                "Strike": leg.strike,
                "Entry price": leg.entry_price,
                "Current close price": leg.current_price,
                "P&L contribution pts": leg.pnl_contribution_points,
                "Status": leg.status,
            }
            for leg in item.legs
        ]
        st.dataframe(pd.DataFrame(leg_rows), width="stretch", hide_index=True)

    left, right = st.columns(2)
    with left:
        st.write("**Guardian evidence**")
        for reason in item.reasons or ("No active guardian reason",):
            st.write(f"• {reason}")
    with right:
        st.write("**Guardian blockers**")
        for blocker in item.blockers or ("None",):
            st.write(f"• {blocker}")


def render_feed_status(snapshot: MarketSnapshot) -> None:
    rows = []
    for key, status in snapshot.feed_status.items():
        rows.append(
            {
                "Feed": key,
                "Available": "YES" if status.ok else "NO",
                "Use": status.use_state,
                "Age sec": status.age_seconds,
                "Message": status.message,
                "Source": status.source,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_data_health(snapshot: MarketSnapshot) -> None:
    """Compact display-only trust label; it never changes One-Brain scores."""
    statuses = snapshot.feed_status or {}
    critical = [statuses.get(key) for key in ("quotes", "candles", "option_chain", "future_volume", "vix")]
    critical = [item for item in critical if item is not None]
    unavailable = [item for item in critical if not item.ok or item.use_state == "UNAVAILABLE"]
    delayed = [item for item in critical if item.use_state in {"STALE", "CAUTION", "DELAYED"}]
    if not snapshot.market_session.is_live:
        label, detail, kind = "LAST DATA", "Market live nahi — sirf reference", "warning"
    elif unavailable:
        label, detail, kind = "DATA KAM", ", ".join(item.name for item in unavailable), "error"
    elif delayed:
        label, detail, kind = "DELAYED", ", ".join(item.name for item in delayed), "warning"
    elif critical:
        ages = [item.age_seconds for item in critical if item.age_seconds is not None]
        age_text = f" · max age {max(ages):.0f}s" if ages else ""
        label, detail, kind = "FRESH", "Critical feeds ready" + age_text, "success"
    else:
        label, detail, kind = "BROKER SE MATCH CHECK", "Freshness details available nahi", "warning"
    getattr(st, kind)(f"📡 **Data Health: {label}** — {detail}")


def render_core_evidence(snapshot: MarketSnapshot) -> None:
    item = snapshot.core_evidence
    st.dataframe(
        [{
            "Market": f"{item.market_state} {item.range_score:.0f}%",
            "Bullish": f"{item.bullish_score:.0f}%",
            "Bearish": f"{item.bearish_score:.0f}%",
            "Evidence quality": f"{item.confidence:.0f}%",
        }],
        width="stretch",
        hide_index=True,
    )
    st.info(
        f"**Kyon:** {(item.reasons or ('Mixed evidence',))[0]}  •  "
        f"**Savdhani:** {(item.blockers or ('Koi major caution nahi',))[0]}"
    )


def _price_action_row(item: Any) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Structure": item.structure,
        "Current Event": item.event,
        "Move Stage": item.move_stage,
        "Last Swing High": item.last_swing_high,
        "Last Swing Low": item.last_swing_low,
        "Invalidation": item.invalidation_level,
        "ATR 14": item.atr14,
        "Bullish": item.bullish_score,
        "Bearish": item.bearish_score,
        "Range": item.range_score,
        "Confidence": item.confidence,
        "Status": item.status,
    }


def render_price_action(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.price_action
    st.caption(
        f"Cross-timeframe view: **{bundle.combined_state}** — {bundle.relationship} "
        f"(confidence {bundle.confidence:.1f}%)."
    )
    rows = []
    for item in (bundle.three_minute, bundle.fifteen_minute):
        rows.append({
            "Time": item.timeframe,
            "Structure": item.structure,
            "Current move": item.event,
            "Invalidation": item.invalidation_level,
            "Evidence quality": f"{item.confidence:.0f}%",
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _level_row(level: MarketLevel | None, fallback: str) -> dict[str, Any]:
    if level is None:
        return {
            "Level": fallback,
            "Zone": "—",
            "Midpoint": None,
            "Distance": None,
            "Strength": None,
            "Status": "UNAVAILABLE",
            "Sources": "—",
        }
    return {
        "Level": f"{level.label} {level.side}",
        "Zone": f"{level.lower:,.2f}–{level.upper:,.2f}",
        "Midpoint": level.midpoint,
        "Distance": level.distance_points,
        "Strength": level.strength,
        "Status": level.status,
        "Sources": ", ".join(level.sources),
    }


def render_levels(snapshot: MarketSnapshot) -> None:
    item = snapshot.levels
    rows = []
    for level, fallback in (
        (item.immediate_support, "S1"), (item.strong_support, "S2"),
        (item.immediate_resistance, "R1"), (item.strong_resistance, "R2"),
    ):
        raw = _level_row(level, fallback)
        rows.append({
            "Level": raw["Level"], "Zone": raw["Zone"],
            "Distance": raw["Distance"], "Strength": raw["Strength"], "Status": raw["Status"],
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _volume_row(item: Any) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Current Volume": item.current_volume,
        "Time-Normalized Baseline": item.baseline_volume,
        "Relative Volume": item.relative_volume,
        "Volume State": item.volume_state,
        "Trend": item.volume_trend,
        "Price Candle": item.price_direction,
        "Move Support": item.move_support,
        "Baseline Samples": item.baseline_samples,
        "Confidence": item.confidence,
        "Status": item.status,
    }


def render_volume(snapshot: MarketSnapshot) -> None:
    item = snapshot.volume
    st.write(f"**Volume:** {item.overall_view} • Evidence quality {item.confidence:.0f}%")
    rows = []
    for row in (item.three_minute, item.fifteen_minute):
        rows.append({
            "Time": row.timeframe,
            "Relative volume": f"{row.relative_volume:.2f}×" if row.relative_volume is not None else "—",
            "Participation": row.volume_state,
            "Trend": row.volume_trend,
            "Move support": row.move_support,
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_candles(snapshot: MarketSnapshot) -> None:
    tabs = st.tabs(
        [
            "NIFTY 3m",
            "NIFTY 15m",
            "NIFTY 1m",
            "Future 3m Volume",
            "Future 15m Volume",
        ]
    )
    with tabs[0]:
        st.caption(
            "3-minute candles are aggregated from Dhan 1-minute candles at 09:15 IST."
        )
        st.dataframe(snapshot.candles_3m.tail(30), width="stretch", hide_index=True)
    with tabs[1]:
        st.dataframe(snapshot.candles_15m.tail(30), width="stretch", hide_index=True)
    with tabs[2]:
        st.dataframe(snapshot.candles_1m.tail(30), width="stretch", hide_index=True)
    with tabs[3]:
        st.dataframe(
            snapshot.future_candles_3m.tail(30),
            width="stretch",
            hide_index=True,
        )
    with tabs[4]:
        st.dataframe(
            snapshot.future_candles_15m.tail(30),
            width="stretch",
            hide_index=True,
        )


def render_option_chain(snapshot: MarketSnapshot) -> None:
    if snapshot.option_chain.empty:
        st.warning("Option chain is unavailable in this snapshot.")
        return
    columns = [
        "strike",
        "side",
        "is_atm",
        "last_price",
        "oi",
        "day_oi_change",
        "volume",
        "previous_close_price",
        "day_price_change",
        "implied_volatility",
        "top_bid_price",
        "top_ask_price",
    ]
    available = [col for col in columns if col in snapshot.option_chain.columns]
    st.caption(
        "Raw option-chain fields from the same authoritative snapshot. Derived flow is shown "
        "in the Options Intelligence section above."
    )
    st.dataframe(snapshot.option_chain[available], width="stretch", hide_index=True)


def render_heavyweights(snapshot: MarketSnapshot) -> None:
    bundle = snapshot.heavyweights
    st.info(f"Top-9 combined: {bundle.recent_state} · day basket {bundle.weighted_move_pct}%")
    st.caption(f"15m basket {bundle.recent_15m_move_pct}%; 3m early change {bundle.recent_3m_move_pct}%; estimated Nifty contribution {bundle.recent_contribution_points} points")
    st.caption(f"Recent coverage: {bundle.recent_coverage_pct:.2f}/{bundle.covered_weight_pct:.2f}% index weight. Configured weights dated {bundle.weight_date}; estimated, not predictive. Rest of index may offset this.")
    st.dataframe([{"Stock": r.symbol, "Day %": r.change_pct, "15m %": r.change_15m_pct, "3m %": r.change_3m_pct, "Nifty pts (15m)": r.contribution_15m_points, "Now": r.recent_state} for r in bundle.rows], width="stretch", hide_index=True)
    if not snapshot.heavyweight_quotes:
        st.info("Top-9 quotes are unavailable in this snapshot.")
        return
    rows: list[dict[str, Any]] = []
    for item in snapshot.heavyweight_quotes:
        ohlc = item.get("ohlc") or {}
        previous_close = ohlc.get("close")
        last = item.get("last_price")
        change_pct = None
        if previous_close not in (None, 0) and last is not None:
            change_pct = (
                (float(last) - float(previous_close)) / float(previous_close) * 100
            )
        rows.append(
            {
                "Symbol": item.get("symbol"),
                "Name": item.get("display_name"),
                "Last": last,
                "Change %": change_pct,
                "Day High": ohlc.get("high"),
                "Day Low": ohlc.get("low"),
                "Volume": item.get("volume"),
                "Security ID": item.get("security_id"),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _indicator_row(item: TimeframeIndicators) -> dict[str, Any]:
    return {
        "Timeframe": item.timeframe,
        "As Of": item.as_of.strftime("%d-%m-%Y %H:%M") if item.as_of else "—",
        "Close": item.close,
        "EMA 20": item.ema20,
        "EMA 50": item.ema50,
        "EMA State": item.ema_state,
        "MACD": item.macd,
        "Signal": item.macd_signal,
        "Histogram": item.macd_histogram,
        "MACD State": item.macd_state,
        "RSI 14": item.rsi14,
        "RSI State": item.rsi_state,
        "Status": item.status,
    }


def render_indicators(snapshot: MarketSnapshot) -> None:
    rows = []
    for item in (snapshot.indicators.three_minute, snapshot.indicators.fifteen_minute):
        rows.append({
            "Time": item.timeframe,
            "EMA trend": item.ema_state,
            "MACD": item.macd_state,
            "RSI": f"{item.rsi14:.0f} • {item.rsi_state}" if item.rsi14 is not None else item.rsi_state,
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_option_intelligence(snapshot: MarketSnapshot) -> None:
    item = snapshot.option_intelligence
    provisional = item.persistence != "CONFIRMED" or item.confidence < 75
    st.dataframe([{
        "Bias": f"{item.market_bias}{' • Provisional' if provisional else ''}",
        "Bullish": f"{item.bullish_score:.0f}%",
        "Bearish": f"{item.bearish_score:.0f}%",
        "Mixed": f"{item.range_score:.0f}%",
        "Evidence quality": f"{item.confidence:.0f}%",
    }], width="stretch", hide_index=True)
    st.info(
        f"**Reason:** {(item.reasons or ('Option flow mixed',))[0]}  •  "
        f"**Waiting:** {(item.blockers or ('Confirmation complete',))[0]}"
    )


def render_option_flow_matrix(snapshot: MarketSnapshot) -> None:
    rows = list(snapshot.option_intelligence.flow_rows)
    if not rows:
        st.info("Option flow matrix is unavailable in this snapshot.")
        return
    frame = pd.DataFrame(rows)
    keep = [column for column in ("strike", "side", "last_price", "classification", "directional_bias", "flow_strength") if column in frame.columns]
    st.dataframe(frame[keep], width="stretch", hide_index=True)


def render_option_windows(snapshot: MarketSnapshot) -> None:
    rows = []
    for item in snapshot.option_intelligence.windows:
        rows.append(
            {
                "Window": item.label,
                "Target sec": item.target_seconds,
                "Actual age sec": item.actual_age_seconds,
                "CE OI Δ": item.ce_oi_delta,
                "PE OI Δ": item.pe_oi_delta,
                "CE Premium Δ": item.ce_premium_delta,
                "PE Premium Δ": item.pe_premium_delta,
                "CE Volume Δ": item.ce_volume_delta,
                "PE Volume Δ": item.pe_volume_delta,
                "Bias": item.bias,
                "Status": item.status,
            }
        )
    ready = [row for row in rows if row["Bias"] not in {"UNAVAILABLE", None}]
    if not ready:
        st.info(f"⏳ Movement confirmation warming up • Ready 0/{len(rows)}")
        return
    st.dataframe(pd.DataFrame(ready), width="stretch", hide_index=True)


def render_walls_and_pcr(snapshot: MarketSnapshot) -> None:
    item = snapshot.option_intelligence
    walls = []
    for wall in (item.ce_wall, item.pe_wall):
        walls.append(
            {
                "Side": wall.side,
                "Main Wall Strike": wall.strike,
                "Wall OI": wall.oi,
                "Strongest 3-Strike Cluster": wall.cluster_center,
            }
        )
    st.dataframe(pd.DataFrame(walls), width="stretch", hide_index=True)
    pcr = item.pcr
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Near-ATM OI PCR",
        f"{pcr.near_atm_oi_pcr:.2f}" if pcr.near_atm_oi_pcr is not None else "—",
    )
    c2.metric(
        "Day Addition PCR",
        f"{pcr.day_addition_pcr:.2f}" if pcr.day_addition_pcr is not None else "—",
    )
    c3.metric(
        "Intraday Addition PCR",
        f"{pcr.intraday_addition_pcr:.2f}"
        if pcr.intraday_addition_pcr is not None
        else "—",
    )
    c4.metric(
        "Volume PCR", f"{pcr.volume_pcr:.2f}" if pcr.volume_pcr is not None else "—"
    )
    st.caption(f"PCR context: **{pcr.state}** | Status: {pcr.status}")


def render_heavyweight_intelligence(snapshot: MarketSnapshot) -> None:
    item = snapshot.heavyweights
    st.info(f"Recent combined: {item.recent_state} · 15m {item.recent_15m_move_pct}% · 3m {item.recent_3m_move_pct}%")
    st.caption(f"Estimated 15m Nifty contribution {item.recent_contribution_points} points; data coverage {item.recent_coverage_pct}/{item.covered_weight_pct}% index weight; weights dated {item.weight_date}.")
    remaining_move = (
        f"{item.estimated_remaining_move_pct:+.3f}%"
        if item.estimated_remaining_move_pct is not None
        else "— · Data kam"
    )
    st.info(
        f"**Top-9:** {item.state} • Move {item.weighted_move_pct or 0:+.3f}% • "
        f"{item.advancing}↑/{item.declining}↓/{item.unchanged}→ • Data coverage {item.confidence:.0f}%"
    )
    if "DISAGREEMENT" in item.market_disagreement and "NO CLEAR" not in item.market_disagreement:
        st.warning(f"⚠️ **Market disagreement:** {item.market_disagreement} — confidence kam rakho")
    st.caption(
        f"Remaining Market ({item.remaining_weight_pct:.2f}% weight) estimated move: {remaining_move} · "
        "NIFTY actual move minus Top-9 contribution se nikla; extra 41 quote calls nahi."
    )
    rows = [asdict(row) for row in item.rows]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_market_context(snapshot: MarketSnapshot) -> None:
    institutional = snapshot.institutional_context
    event = snapshot.event_risk
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Latest FII cash ₹ cr",
        f"{institutional.latest_fii_net:,.1f}"
        if institutional.latest_fii_net is not None
        else "Missing",
    )
    c2.metric(
        "Latest DII cash ₹ cr",
        f"{institutional.latest_dii_net:,.1f}"
        if institutional.latest_dii_net is not None
        else "Missing",
    )
    c3.metric(
        "FII Futures contracts",
        f"{institutional.latest_fii_index_futures_contracts:,.0f}"
        if institutional.latest_fii_index_futures_contracts is not None
        else "Missing",
    )
    futures_split = (
        f"Long {institutional.latest_fii_futures_long_pct:.2f}% | Short {institutional.latest_fii_futures_short_pct:.2f}%"
        if institutional.latest_fii_futures_long_pct is not None
        and institutional.latest_fii_futures_short_pct is not None
        else "Long/Short missing"
    )
    c4.metric("FII Futures Bias", institutional.fii_futures_bias, delta=futures_split, delta_color="off")
    event_text = event.level if event.verified else "Unverified • Weight 0%"
    st.info(f"**Institutional:** {institutional.state} • **Event:** {event_text}")
    rows = [
        {
            "Window": "5 sessions",
            "FII cash net ₹ cr": institutional.fii_5d_net,
            "DII cash net ₹ cr": institutional.dii_5d_net,
            "FII Futures Long avg %": institutional.fii_futures_5d_long_avg_pct,
            "FII Futures Short avg %": institutional.fii_futures_5d_short_avg_pct,
        },
        {
            "Window": "10 sessions",
            "FII cash net ₹ cr": institutional.fii_10d_net,
            "DII cash net ₹ cr": institutional.dii_10d_net,
            "FII Futures Long avg %": institutional.fii_futures_10d_long_avg_pct,
            "FII Futures Short avg %": institutional.fii_futures_10d_short_avg_pct,
        },
        {
            "Window": "15 sessions",
            "FII cash net ₹ cr": institutional.fii_15d_net,
            "DII cash net ₹ cr": institutional.dii_15d_net,
            "FII Futures Long avg %": institutional.fii_futures_15d_long_avg_pct,
            "FII Futures Short avg %": institutional.fii_futures_15d_short_avg_pct,
        },
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.write(
        f"**Institutional status:** {institutional.status} | "
        f"**Observations:** {institutional.observations}/15 | "
        f"**As of:** {institutional.as_of_date or '—'} | "
        f"**Confidence:** {institutional.confidence:.1f}%"
    )


def render_vix_context(snapshot: MarketSnapshot) -> None:
    item = snapshot.vix_context
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "India VIX", f"{item.last_price:.2f}" if item.last_price is not None else "—"
    )
    c2.metric(
        "VIX Change",
        f"{item.change_pct:+.2f}%" if item.change_pct is not None else "—",
    )
    c3.metric("VIX Regime", item.regime)
    c4.metric("Movement", item.movement)
    message = (
        f"**Seller environment:** {item.seller_environment} | **Status:** {item.status}"
    )
    if item.status != "READY":
        st.warning(
            f"{message} — zero or missing VIX is treated as unavailable, never as a "
            "balanced premium environment."
        )
    else:
        st.info(message)
