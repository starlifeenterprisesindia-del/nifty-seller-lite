"""Public import retained for old callers; canonical core lives in directional_core."""
from analysis.directional_core import calculate_core_market_evidence, indicator_scores

__all__ = ["calculate_core_market_evidence", "_indicator_scores"]


def _indicator_scores(indicators):
    return (*indicator_scores(indicators), ["Completed 15m EMA/MACD/RSI only"])
