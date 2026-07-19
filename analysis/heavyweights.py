from __future__ import annotations

from datetime import datetime
from typing import Any

from config import CONFIG
from models import HeavyweightBundle, HeavyweightContribution


def _change_pct(quote: dict[str, Any]) -> float | None:
    last = quote.get("last_price")
    previous_close = (quote.get("ohlc") or {}).get("close")
    if last is None or previous_close in (None, 0):
        return None
    return (float(last) - float(previous_close)) / float(previous_close) * 100.0


def calculate_heavyweight_bundle(
    quotes: list[dict[str, Any]],
    captured_at: datetime,
) -> HeavyweightBundle:
    by_symbol = {str(item.get("symbol")): item for item in quotes}
    rows: list[HeavyweightContribution] = []
    advancing = declining = unchanged = 0
    weighted_sum = 0.0
    contribution_sum = 0.0
    covered_weight = 0.0
    usable_weight = 0.0

    for configured in CONFIG.top7:
        quote = by_symbol.get(configured.symbol, {})
        change = _change_pct(quote) if quote else None
        last = quote.get("last_price") if quote else None
        contribution = None
        direction = "UNAVAILABLE"
        if change is not None:
            contribution = configured.weight_pct * change / 100.0
            weighted_sum += configured.weight_pct * change
            contribution_sum += contribution
            usable_weight += configured.weight_pct
            if change > 0.03:
                direction = "UP"
                advancing += 1
            elif change < -0.03:
                direction = "DOWN"
                declining += 1
            else:
                direction = "FLAT"
                unchanged += 1
        covered_weight += configured.weight_pct
        rows.append(
            HeavyweightContribution(
                symbol=configured.symbol,
                name=configured.name,
                official_weight_pct=configured.weight_pct,
                last_price=float(last) if last is not None else None,
                change_pct=round(change, 4) if change is not None else None,
                index_contribution_pct=round(contribution, 5)
                if contribution is not None
                else None,
                direction=direction,
            )
        )

    weighted_move = weighted_sum / usable_weight if usable_weight > 0 else None
    if usable_weight <= 0:
        state = "UNAVAILABLE"
        confidence = 0.0
        status = "UNAVAILABLE"
    else:
        breadth = advancing - declining
        if weighted_move > 0.20 and breadth >= 3:
            state = "BROAD BULLISH"
        elif weighted_move < -0.20 and breadth <= -3:
            state = "BROAD BEARISH"
        elif weighted_move > 0.08:
            state = "NARROW BULLISH"
        elif weighted_move < -0.08:
            state = "NARROW BEARISH"
        else:
            state = "MIXED / FLAT"
        completeness = usable_weight / max(covered_weight, 0.01)
        confidence = min(95.0, 55.0 + completeness * 40.0)
        status = "READY" if completeness >= 0.99 else "CAUTION"

    return HeavyweightBundle(
        as_of=captured_at,
        rows=tuple(rows),
        covered_weight_pct=round(covered_weight, 2),
        weighted_move_pct=round(weighted_move, 4)
        if weighted_move is not None
        else None,
        estimated_index_contribution_pct=round(contribution_sum, 5)
        if usable_weight > 0
        else None,
        advancing=advancing,
        declining=declining,
        unchanged=unchanged,
        state=state,
        confidence=round(confidence, 1),
        status=status,
    )
