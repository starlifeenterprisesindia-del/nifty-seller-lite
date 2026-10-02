"""Read-only Phase-5 validation analytics over recorded One-Brain replay rows.

This module deliberately does NOT reconstruct option P&L or auto-tune thresholds.
It only evaluates the direction of already-recorded canonical actions against later
NIFTY spot outcomes.  All calculations are local/on-demand and make no broker/API
calls and no One-Brain writes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Iterable


BULLISH_ACTIONS = {"PE SELL", "CE BUY"}
BEARISH_ACTIONS = {"CE SELL", "PE BUY"}
RANGE_ACTIONS = {"IRON CONDOR"}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _norm_action(value: Any) -> str:
    return " ".join(str(value or "WAIT").upper().replace("_", " ").split())


def expected_direction(action: Any) -> str | None:
    action = _norm_action(action)
    if action in BULLISH_ACTIONS:
        return "UP"
    if action in BEARISH_ACTIONS:
        return "DOWN"
    if action in RANGE_ACTIONS:
        return "RANGE"
    return None


def score_directional_move(action: Any, move_points: Any, *, flat_points: float = 5.0) -> str:
    """Score only directional actions against later spot movement.

    RANGE/WAIT return UNSCORED because spot movement alone cannot establish option
    strategy P&L or whether a condor was profitable.
    """
    expected = expected_direction(action)
    move = _num(move_points)
    if expected not in {"UP", "DOWN"} or move is None:
        return "UNSCORED"
    flat = max(0.0, float(flat_points))
    if abs(move) <= flat:
        return "FLAT"
    actual = "UP" if move > 0 else "DOWN"
    return "HIT" if actual == expected else "MISS"


def _rate(hits: int, misses: int) -> float | None:
    denominator = int(hits) + int(misses)
    return round(100.0 * hits / denominator, 1) if denominator else None


def _median(values: Iterable[float]) -> float | None:
    clean = [float(v) for v in values]
    return round(float(median(clean)), 2) if clean else None


def _scored_rows(timeline: list[dict[str, Any]], horizon: int, flat_points: float) -> list[dict[str, Any]]:
    key = f"outcome_{int(horizon)}m_points"
    rows: list[dict[str, Any]] = []
    for item in timeline:
        action = _norm_action(item.get("final_action"))
        move = _num(item.get(key))
        score = score_directional_move(action, move, flat_points=flat_points)
        if score == "UNSCORED":
            continue
        rows.append({
            "at": str(item.get("at") or ""),
            "time": str(item.get("time") or ""),
            "action": action,
            "expected": expected_direction(action),
            "move_points": move,
            "score": score,
            "entry_readiness": _num(item.get("entry_readiness")),
            "regime": str(item.get("regime") or "UNKNOWN"),
            "direction": str(item.get("direction") or "UNKNOWN"),
            "big_player_direction": str(item.get("big_player_direction") or ""),
            "big_player_score": _num(item.get("big_player_score")),
            "option_bias": str(item.get("option_bias") or ""),
            "option_confidence": _num(item.get("option_confidence")),
        })
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["score"] for row in rows)
    hits = int(counts.get("HIT", 0))
    misses = int(counts.get("MISS", 0))
    flats = int(counts.get("FLAT", 0))
    moves = [abs(float(row["move_points"])) for row in rows if row.get("move_points") is not None]
    return {
        "rows": len(rows),
        "hits": hits,
        "misses": misses,
        "flat": flats,
        "directional_hit_rate_pct": _rate(hits, misses),
        "median_abs_move_points": _median(moves),
    }


def _group_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "UNKNOWN")].append(row)
    result = []
    for name, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        summary = _summary(items)
        result.append({key: name, **summary})
    return result


def _readiness_bucket(value: Any) -> str:
    score = _num(value)
    if score is None:
        return "UNKNOWN"
    if score < 50:
        return "<50"
    if score < 60:
        return "50–59"
    if score < 70:
        return "60–69"
    if score < 80:
        return "70–79"
    return "80+"


def _walk_forward(rows: list[dict[str, Any]], split_ratio: float = 0.60) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row.get("at") or "")
    if len(ordered) < 20:
        return {
            "status": "INSUFFICIENT DATA",
            "note": "At least 20 scored directional observations are required for a basic chronological split.",
        }
    split = max(10, min(len(ordered) - 10, int(round(len(ordered) * split_ratio))))
    reference = ordered[:split]
    validation = ordered[split:]
    return {
        "status": "READY",
        "reference": _summary(reference),
        "validation": _summary(validation),
        "split_index": split,
        "note": "Chronological 60/40 descriptive split only; it does not tune or approve any threshold.",
    }


def _sensitivity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for threshold in range(50, 85, 5):
        eligible = [
            row for row in rows
            if _num(row.get("entry_readiness")) is not None
            and float(row["entry_readiness"]) >= threshold
        ]
        result.append({"readiness_filter": f">={threshold}", **_summary(eligible)})
    return result


def _wait_review(timeline: list[dict[str, Any]], horizon: int, large_move_points: float) -> dict[str, Any]:
    key = f"outcome_{int(horizon)}m_points"
    rows = []
    for item in timeline:
        if _norm_action(item.get("final_action")) != "WAIT":
            continue
        move = _num(item.get(key))
        if move is None:
            continue
        rows.append({
            "time": item.get("time"),
            "at": item.get("at"),
            "move_points": round(move, 2),
            "abs_move_points": round(abs(move), 2),
            "regime": item.get("regime"),
            "direction": item.get("direction"),
            "entry_readiness": _num(item.get("entry_readiness")),
            "big_player_direction": item.get("big_player_direction"),
            "big_player_score": _num(item.get("big_player_score")),
        })
    threshold = max(0.0, float(large_move_points))
    large = [row for row in rows if row["abs_move_points"] >= threshold]
    return {
        "covered_waits": len(rows),
        "large_move_waits": len(large),
        "threshold_points": threshold,
        "median_abs_move_points": _median([row["abs_move_points"] for row in rows]),
        "largest": sorted(large, key=lambda row: row["abs_move_points"], reverse=True)[:15],
        "note": "A large move after WAIT is a review flag, not proof that WAIT was wrong or that an option trade was profitable.",
    }


def build_validation_report(
    replay_bundle: dict[str, Any] | None,
    *,
    horizon: int = 15,
    flat_points: float = 5.0,
    wait_large_move_points: float = 25.0,
) -> dict[str, Any]:
    bundle = replay_bundle if isinstance(replay_bundle, dict) else {}
    timeline = list(bundle.get("timeline") or [])
    horizon = int(horizon)
    if horizon not in {5, 15, 30}:
        horizon = 15
    rows = _scored_rows(timeline, horizon, float(flat_points))
    by_regime = _group_summary(rows, "regime")
    readiness_rows = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_readiness_bucket(row.get("entry_readiness"))].append(row)
    for name in ("<50", "50–59", "60–69", "70–79", "80+", "UNKNOWN"):
        if name in grouped:
            readiness_rows.append({"readiness_bucket": name, **_summary(grouped[name])})

    misses = sorted(
        (row for row in rows if row["score"] == "MISS"),
        key=lambda row: abs(float(row.get("move_points") or 0.0)),
        reverse=True,
    )[:20]
    return {
        "schema": 1,
        "session_date": bundle.get("session_date"),
        "horizon_minutes": horizon,
        "flat_points": float(flat_points),
        "timeline_rows": len(timeline),
        "directional": _summary(rows),
        "by_regime": by_regime,
        "by_readiness": readiness_rows,
        "walk_forward": _walk_forward(rows),
        "sensitivity": _sensitivity(rows),
        "misses": misses,
        "wait_review": _wait_review(timeline, horizon, wait_large_move_points),
        "limitations": [
            "Spot-direction validation only; it is not option P&L or fill-quality backtesting.",
            "WAIT and IRON CONDOR are not scored as directional wins/losses.",
            "Readiness sensitivity is a retrospective filter, not a counterfactual threshold backtest.",
            "No threshold, weight, One Brain state or live trading rule is changed automatically.",
        ],
    }
