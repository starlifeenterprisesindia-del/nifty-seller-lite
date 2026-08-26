from __future__ import annotations

from typing import Any

import streamlit as st

from models import MarketSnapshot


def _pick(bull: float, bear: float, neutral: float) -> tuple[str, float]:
    values = {
        "BULLISH": max(0.0, float(bull)),
        "BEARISH": max(0.0, float(bear)),
        "NEUTRAL": max(0.0, float(neutral)),
    }
    total = sum(values.values()) or 1.0
    direction, raw = max(values.items(), key=lambda item: item[1])
    return direction, round(raw / total * 100.0, 1)


def _short_reason(*parts: Any) -> str:
    return " · ".join(str(part) for part in parts if part not in (None, ""))[:150]


def build_timeframe_rows(snapshot: MarketSnapshot, live_impulse: Any | None = None) -> list[dict[str, Any]]:
    """Display-only horizon projection from existing One-Brain evidence.

    It never feeds scores back into FinalDecision, so there is no second brain and
    no module receives weight twice.
    """
    pa3 = snapshot.price_action.three_minute
    pa15 = snapshot.price_action.fifteen_minute
    core = snapshot.core_evidence
    outlook = snapshot.decision.outlook

    five = _pick(pa3.bullish_score, pa3.bearish_score, pa3.range_score)
    five_reason = _short_reason("3m price action", pa3.structure, pa3.current_move)
    if live_impulse is not None and getattr(live_impulse, "direction", "RANGE") in {"BULLISH", "BEARISH"}:
        impulse_score = float(getattr(live_impulse, "score", 0.0) or 0.0)
        if impulse_score >= 55:
            five = (str(live_impulse.direction), round(impulse_score, 1))
            five_reason = _short_reason("Live impulse", live_impulse.state, *(live_impulse.reasons[:2]))

    fifteen = _pick(pa15.bullish_score, pa15.bearish_score, pa15.range_score)
    fifteen_reason = _short_reason("15m structure", pa15.structure, pa15.current_move)

    thirty = _pick(outlook.bullish_path_pct, outlook.bearish_path_pct, outlook.range_path_pct)
    thirty_reason = _short_reason("One-Brain path", snapshot.decision.market_direction, *(outlook.reasons[:1]))

    hourly = _pick(core.bullish_score, core.bearish_score, core.range_score)
    hourly_reason = _short_reason("Core structure", core.market_state, core.move_stage, *(core.reasons[:1]))

    return [
        {"Time": "5 min", "Direction": f"{five[0]} {five[1]:.1f}%", "Kyun": five_reason},
        {"Time": "15 min", "Direction": f"{fifteen[0]} {fifteen[1]:.1f}%", "Kyun": fifteen_reason},
        {"Time": "30 min", "Direction": f"{thirty[0]} {thirty[1]:.1f}%", "Kyun": thirty_reason},
        {"Time": "1 hour", "Direction": f"{hourly[0]} {hourly[1]:.1f}%", "Kyun": hourly_reason},
    ]


def render_timeframe_outlook(snapshot: MarketSnapshot, live_impulse: Any | None = None) -> None:
    st.subheader("🧭 One-Brain Timeframe Outlook")
    st.caption(
        "Yeh same One-Brain evidence ka horizon view hai—alag signal engine nahi. "
        "Percent conditional hai, guarantee nahi; final entry status wahi ek One-Brain deta hai."
    )
    st.dataframe(
        build_timeframe_rows(snapshot, live_impulse),
        width="stretch",
        hide_index=True,
        column_config={
            "Time": st.column_config.TextColumn(width="small"),
            "Direction": st.column_config.TextColumn(width="medium"),
            "Kyun": st.column_config.TextColumn(width="large"),
        },
    )
