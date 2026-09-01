"""Canonical timeframe core. Missing evidence never becomes a range vote."""
from models import CoreMarketEvidence


def indicator_scores(indicators):
    item = indicators.fifteen_minute
    if item.status != "READY":
        return 0.0, 0.0, 0.0
    bull = bear = neutral = 0.0
    for state, points in ((item.ema_state, 15.0), (item.macd_state, 40.0)):
        factor = 0.6 if "MIXED" in state or "WEAKENING" in state else 1.0
        if "BULLISH" in state:
            bull += points * factor
        elif "BEARISH" in state:
            bear += points * factor
        else:
            neutral += points
    rsi = getattr(item, "rsi14", None)
    if rsi is not None:
        if rsi >= 55:
            bull += 45.0
        elif rsi <= 45:
            bear += 45.0
        else:
            neutral += 45.0
    return bull, bear, neutral


def calculate_core_market_evidence(price_action, indicators, levels, volume, market_session, *, future_volume_live=True):
    # Permission/timing core: completed 15m PA 15 + completed 15m
    # EMA/MACD/RSI 10 + completed 3m trigger 15 = 40 points.  The returned
    # values remain normalized to 0–100 so the One-Brain can assign its 40%
    # canonical weight without hidden/double weighting.
    pa3, pa15 = price_action.three_minute, price_action.fifteen_minute
    scores = [0.0, 0.0, 0.0]
    coverage = 0.0
    for pa, points in ((pa15, 15.0), (pa3, 15.0)):
        if pa.status == "READY":
            for idx, value in enumerate((pa.bullish_score, pa.bearish_score, pa.range_score)):
                scores[idx] += value * points / 40.0
            coverage += points / 40.0 * 100
    scores = [a + b * 10 / 40 for a, b in zip(scores, indicator_scores(indicators))]
    if indicators.fifteen_minute.status == "READY":
        coverage += 10 / 40 * 100
    ordered = sorted(zip(("BULLISH", "BEARISH", "RANGE / MIXED"), scores), key=lambda x: x[1], reverse=True)
    state = ordered[0][0] if ordered[0][1] - ordered[1][1] >= 8 else "MIXED / NO CLEAR CORE EDGE"
    blockers = []
    if coverage < 99.9:
        blockers.append(f"Core data coverage {coverage:.0f}%; missing evidence has zero vote")
    if not market_session.is_live:
        blockers.append("Reference-only market session")
    ind = indicators.fifteen_minute
    reasons = (f"15m structure: {pa15.structure}; 3m: {pa3.event}",
               f"15m: {ind.ema_state}; {ind.macd_state}; {ind.rsi_state}", price_action.relationship)
    return CoreMarketEvidence(
        bullish_score=round(scores[0], 1), bearish_score=round(scores[1], 1), range_score=round(scores[2], 1),
        confidence=round(coverage, 1), market_state=state, move_stage=pa15.move_stage,
        status="REFERENCE ONLY" if not market_session.is_live else "READY" if coverage >= 99.9 else "PARTIAL",
        reasons=reasons, blockers=tuple(blockers))
