from __future__ import annotations

from dataclasses import dataclass

from models import MarketSnapshot


@dataclass(frozen=True)
class CanonicalForecast:
    up: float
    down: float
    range: float
    direction: str
    state: str
    confirmation: str
    invalidation: str


def _normalize(up: float, down: float, range_score: float) -> tuple[float, float, float]:
    values = [max(0.0, float(up)), max(0.0, float(down)), max(0.0, float(range_score))]
    total = sum(values) or 1.0
    rounded = [round(value * 100.0 / total, 1) for value in values]
    rounded[2] = round(100.0 - rounded[0] - rounded[1], 1)
    return rounded[0], rounded[1], rounded[2]


def build_canonical_forecast(snapshot: MarketSnapshot) -> CanonicalForecast:
    """One presentation contract sourced only from FinalDecision.outlook.

    It deliberately does not re-vote indicators. Every screen uses these same
    path weights, direction and invalidation fields.
    """
    outlook = snapshot.decision.outlook
    up, down, range_score = _normalize(
        outlook.bullish_path_pct, outlook.bearish_path_pct, outlook.range_path_pct
    )
    direction = max({"UP": up, "DOWN": down, "RANGE": range_score}, key={"UP": up, "DOWN": down, "RANGE": range_score}.get)
    gap = sorted((up, down, range_score), reverse=True)
    state = "CONTINUATION" if direction == snapshot.decision.market_direction.replace("BULLISH", "UP").replace("BEARISH", "DOWN") else "REVERSAL WATCH"
    if gap[0] - gap[1] < 8:
        state = "MIXED / TRANSITION"
    if direction == "RANGE":
        state = "RANGE / COMPRESSION"
    confirmation = "3m completed close + OI/volume follow-through"
    return CanonicalForecast(
        up=up,
        down=down,
        range=range_score,
        direction=direction,
        state=state,
        confirmation=confirmation,
        invalidation=str(outlook.invalidation_text or "Level unavailable"),
    )


def compatible_strategies(direction: str) -> tuple[str, ...]:
    return {
        "UP": ("CE BUY", "PE SELL"),
        "BULLISH": ("CE BUY", "PE SELL"),
        "DOWN": ("PE BUY", "CE SELL"),
        "BEARISH": ("PE BUY", "CE SELL"),
        "RANGE": ("IRON CONDOR",),
    }.get(str(direction).upper(), ())
