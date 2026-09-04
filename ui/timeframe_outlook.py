from __future__ import annotations

from typing import Any
from math import isfinite

import streamlit as st

from models import MarketSnapshot
from analysis.canonical_forecast import build_canonical_forecast


def _pick(bull: float, bear: float, neutral: float) -> tuple[str, float]:
    def safe(value):
        try:
            value = float(value)
            return max(0.0, value) if isfinite(value) else 0.0
        except (ValueError, TypeError):
            return 0.0
    values = {
        "BULLISH": safe(bull),
        "BEARISH": safe(bear),
        "NEUTRAL": safe(neutral),
    }
    if sum(values.values()) == 0:
        return "INSUFFICIENT DATA", 0.0
    if list(values.values()).count(max(values.values())) > 1:
        return "MIXED", round(max(values.values()) / sum(values.values()) * 100, 1)
    total = sum(values.values()) or 1.0
    direction, raw = max(values.items(), key=lambda item: item[1])
    return direction, round(raw / total * 100.0, 1)


def _short_reason(*parts: Any) -> str:
    return " · ".join(str(part) for part in parts if part not in (None, ""))[:150]


def _price_action_move(item: Any) -> str:
    """Return a move label across old and new snapshot model versions."""
    return str(
        getattr(item, "current_move", None)
        or getattr(item, "event", None)
        or getattr(item, "move_stage", None)
        or ""
    )


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
    five_reason = _short_reason("3m price action", pa3.structure, _price_action_move(pa3))
    # Fast impulse remains a separate early alert. A cached impulse must never
    # overwrite this snapshot's canonical completed-candle horizon view.

    fifteen = _pick(pa15.bullish_score, pa15.bearish_score, pa15.range_score)
    fifteen_reason = _short_reason("15m structure", pa15.structure, _price_action_move(pa15))

    forecast = build_canonical_forecast(snapshot)
    thirty = (forecast.direction.replace("UP", "BULLISH").replace("DOWN", "BEARISH"), max(forecast.up, forecast.down, forecast.range))
    thirty_reason = _short_reason("One-Brain path", snapshot.decision.market_direction, *(outlook.reasons[:1]))

    hourly = _pick(core.bullish_score, core.bearish_score, core.range_score)
    hourly_reason = _short_reason("Core structure", core.market_state, core.move_stage, *(core.reasons[:1]))

    rows = [{"Time": label, "Direction": pick[0], "Evidence /100": pick[1], "Kyun": reason}
            for label, pick, reason in (("5 min", five, five_reason), ("15 min", fifteen, fifteen_reason),
                                        ("30 min", thirty, thirty_reason), ("1 hour", hourly, hourly_reason))]
    # Explicit next-session context, not a new weighted model or calibrated forecast.
    days = snapshot.metadata.get("cycle_recorded_days", 0)
    direction = str(snapshot.decision.market_direction)
    agrees = direction in {"BULLISH", "BEARISH"} and hourly[0] == direction
    rows.append({"Time": "1 day", "Direction": direction if days >= 2 and agrees else "PENDING / MIXED",
                 "Evidence /100": None,
                 "Kyun": f"Agle trading session ka provisional context · {days} recorded days · "
                         + ("Current One-Brain + core agree; gap/news se badal sakta hai" if days >= 2 and agrees
                            else "History kam ya core/direction conflict; daily prediction verified nahi")})
    return rows


def render_timeframe_outlook(snapshot: MarketSnapshot, live_impulse: Any | None = None) -> None:
    st.subheader("🔮 Future Brain — Timeframe Detail")
    st.caption(
        "5m/15m Future Brain ke actionable horizons hain. 30m/1h/1day context-only detail hai; "
        "Common Final Gate ke bina koi strategy entry nahi banti."
    )
    if not snapshot.market_session.is_live:
        st.info("Last-session outlook — market closed; live prediction nahi.")
    future = snapshot.metadata.get("future_brain") or {}
    def future_pick(horizon: str) -> tuple[str, float]:
        values = {
            "UP": float(future.get(f"up_{horizon}") or 0),
            "DOWN": float(future.get(f"down_{horizon}") or 0),
            "RANGE": float(future.get(f"range_{horizon}") or 0),
        }
        direction, score = max(values.items(), key=lambda item: item[1])
        return (direction if score else "MIXED", score)
    pick5, pick15 = future_pick("5m"), future_pick("15m")
    future_rows = [
        {"Time":"5 min", "Direction":pick5[0], "Evidence /100":pick5[1], "Kyun":future.get("confirmation", "Leading evidence warming up")},
        {"Time":"15 min", "Direction":pick15[0], "Evidence /100":pick15[1], "Kyun":future.get("transition", "MIXED / TRANSITION")},
    ]
    st.dataframe(
        future_rows,
        width="stretch",
        hide_index=True,
        column_config={
            "Time": st.column_config.TextColumn(width="small"),
            "Direction": st.column_config.TextColumn(width="medium"),
            "Kyun": st.column_config.TextColumn(width="large"),
        },
    )
    with st.expander("30m / 1h / next-session context", expanded=False):
        st.dataframe(build_timeframe_rows(snapshot, live_impulse)[2:], width="stretch", hide_index=True)
