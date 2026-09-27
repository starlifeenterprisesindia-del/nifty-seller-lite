from __future__ import annotations

from statistics import median
from typing import Any, Iterable


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def summarize_alert_history(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize already-fetched alert audit rows.

    Presentation-only helper: no network calls, no snapshot mutation and no impact on
    the One Brain decision path.
    """
    items = [dict(row) for row in rows if isinstance(row, dict)]
    sent = [row for row in items if str(row.get("status", "")).upper() == "SENT"]
    failed = [row for row in items if str(row.get("status", "")).upper() == "FAILED"]
    latencies = [
        value
        for value in (_safe_float(row.get("latency_seconds")) for row in items)
        if value is not None
    ]
    conflicts = sum(1 for row in items if bool(row.get("conflict")))
    directional = [
        row for row in items
        if str(row.get("direction", "")).upper() in {"BULLISH", "BEARISH"}
    ]
    return {
        "total": len(items),
        "sent": len(sent),
        "failed": len(failed),
        "delivery_rate_pct": round((100.0 * len(sent) / len(items)), 1) if items else None,
        "median_latency_seconds": round(median(latencies), 3) if latencies else None,
        "p95_latency_seconds": round(_percentile(latencies, 0.95), 3) if latencies else None,
        "conflict_rate_pct": round((100.0 * conflicts / len(directional)), 1) if directional else None,
        "slow_count": sum(1 for value in latencies if value > 3.0),
        "fast_count": sum(1 for value in latencies if value <= 3.0),
    }


def filter_alert_history(
    rows: Iterable[dict[str, Any]],
    *,
    status: str = "ALL",
    direction: str = "ALL",
) -> list[dict[str, Any]]:
    status_key = str(status or "ALL").upper()
    direction_key = str(direction or "ALL").upper()
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if status_key != "ALL" and str(row.get("status", "")).upper() != status_key:
            continue
        if direction_key != "ALL" and str(row.get("direction", "")).upper() != direction_key:
            continue
        result.append(dict(row))
    return result
