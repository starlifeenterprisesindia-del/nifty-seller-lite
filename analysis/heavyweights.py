from __future__ import annotations

from datetime import datetime
from dataclasses import replace
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
    nifty_quote: dict[str, Any] | None = None,
    *,
    reference_only: bool = False,
    history: list[dict[str, Any]] | None = None,
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
    elif reference_only:
        state = "REFERENCE ONLY"
        confidence = 0.0
        status = "REFERENCE ONLY"
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

    remaining_weight = max(0.0, 100.0 - covered_weight)
    nifty_change = _change_pct(nifty_quote or {})
    remaining_move = None
    disagreement = "UNAVAILABLE"
    if nifty_change is not None and remaining_weight > 0 and usable_weight >= covered_weight * 0.99:
        remaining_contribution = nifty_change - contribution_sum
        remaining_move = remaining_contribution / remaining_weight * 100.0
        if weighted_move is not None and weighted_move >= 0.05 and remaining_move <= -0.05:
            disagreement = "TOP-9 UP / REMAINING MARKET DOWN"
        elif weighted_move is not None and weighted_move <= -0.05 and remaining_move >= 0.05:
            disagreement = "TOP-9 DOWN / REMAINING MARKET UP"
        else:
            disagreement = "ALIGNED / NO CLEAR DISAGREEMENT"

    bundle = HeavyweightBundle(
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
        remaining_weight_pct=round(remaining_weight, 2),
        estimated_remaining_move_pct=round(remaining_move, 4) if remaining_move is not None else None,
        market_disagreement=disagreement,
    )
    if reference_only:
        return bundle
    anchors = {}
    for minutes in (3, 15):
        candidates = []
        for observation in history or []:
            try:
                age = (captured_at - datetime.fromisoformat(observation["at"])).total_seconds()
                stamp = datetime.fromisoformat(observation["at"])
                if stamp.date() == captured_at.date() and minutes * 60 <= age <= minutes * 60 + 90:
                    candidates.append((age, observation))
            except (ValueError, KeyError, TypeError):
                continue
        anchors[minutes] = min(candidates, key=lambda x: x[0])[1] if candidates else None
    enriched = []
    sums = {3: 0.0, 15: 0.0}
    covered = {3: 0.0, 15: 0.0}
    points = 0.0
    for row in rows:
        changes = {}
        for minutes in (3, 15):
            anchor = anchors[minutes]
            start = (anchor or {}).get("prices", {}).get(row.symbol)
            change = (row.last_price / float(start) - 1) * 100 if start and row.last_price and row.change_pct is not None else None
            changes[minutes] = change
            if change is not None:
                sums[minutes] += change * row.official_weight_pct
                covered[minutes] += row.official_weight_pct
        change = changes[15]
        contribution = None
        recent = "WARMING UP"
        if change is not None:
            recent = "RECOVERY" if change > 0.03 and (row.change_pct or 0) < 0 else "PULLBACK" if change < -0.03 and (row.change_pct or 0) > 0 else "BUYING SUPPORT" if change > 0.03 else "SELLING PRESSURE" if change < -0.03 else "FLAT"
            start_nifty = (anchors[15] or {}).get("nifty")
            if start_nifty:
                contribution = float(start_nifty) * row.official_weight_pct * change / 10000
                points += contribution
        enriched.append(replace(row, change_3m_pct=changes[3], change_15m_pct=change,
                                contribution_15m_points=contribution, recent_state=recent))
    move15 = sums[15] / covered[15] if covered[15] else None
    move3 = sums[3] / covered[3] if covered[3] else None
    recent_state = "WARMING UP" if move15 is None else "RECOVERY" if move15 > 0.03 and (weighted_move or 0) < 0 else "PULLBACK" if move15 < -0.03 and (weighted_move or 0) > 0 else "BUYING SUPPORT" if move15 > 0.03 else "SELLING PRESSURE" if move15 < -0.03 else "MIXED / FLAT"
    return replace(bundle, rows=tuple(enriched), recent_15m_move_pct=move15, recent_3m_move_pct=move3,
                   recent_contribution_points=points if covered[15] else None,
                   recent_coverage_pct=round(covered[15], 2), recent_state=recent_state)
