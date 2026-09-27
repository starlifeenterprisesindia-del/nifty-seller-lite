"""Read-only performance diagnostics built from an existing MarketSnapshot.

This module must never fetch market data, mutate the snapshot, or feed a score back
into One Brain.  It only formats timings and feed states that were already produced
by the normal snapshot pipeline.
"""
from __future__ import annotations

from typing import Any


CRITICAL_FEEDS = ("quotes", "candles", "option_chain", "future_volume", "vix")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _age_seconds(snapshot: Any, item: Any) -> float | None:
    explicit = getattr(item, "age_seconds", None)
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            pass
    fetched = getattr(item, "fetched_at", None)
    created = getattr(snapshot, "created_at", None)
    if fetched is None or created is None:
        return None
    try:
        return max(0.0, (created - fetched).total_seconds())
    except Exception:
        return None


def build_performance_report(
    snapshot: Any,
    *,
    refresh_interval_seconds: float = 30.0,
    snapshot_built_now: bool = False,
) -> dict[str, Any]:
    """Return a small diagnostic report without recomputation or I/O."""
    metadata = getattr(snapshot, "metadata", {}) or {}
    performance = metadata.get("performance") or {}
    stages = performance.get("stages") or {}

    stage_rows = []
    for name, raw_seconds in stages.items():
        seconds = max(0.0, _number(raw_seconds))
        stage_rows.append({"Stage": str(name), "Seconds": round(seconds, 4)})
    stage_rows.sort(key=lambda row: row["Seconds"], reverse=True)

    pipeline_seconds = max(0.0, _number(performance.get("pipeline_seconds")))
    build_seconds = max(0.0, _number(performance.get("build_seconds")))
    finalize_seconds = max(0.0, _number(performance.get("finalize_seconds")))
    interval = max(1.0, _number(refresh_interval_seconds, 30.0))
    headroom = max(0.0, interval - pipeline_seconds)
    load_pct = min(999.0, 100.0 * pipeline_seconds / interval)

    statuses = getattr(snapshot, "feed_status", {}) or {}
    feed_rows = []
    critical_issue_count = 0
    for name, item in statuses.items():
        if item is None:
            continue
        state = str(getattr(item, "use_state", "UNAVAILABLE") or "UNAVAILABLE")
        ok = bool(getattr(item, "ok", False))
        age = _age_seconds(snapshot, item)
        is_critical = name in CRITICAL_FEEDS
        if is_critical and (not ok or state in {"UNAVAILABLE", "STALE", "DELAYED", "CAUTION"}):
            critical_issue_count += 1
        feed_rows.append(
            {
                "Feed": str(name),
                "Critical": "YES" if is_critical else "NO",
                "State": state,
                "Age s": round(age, 1) if age is not None else None,
                "OK": "YES" if ok else "NO",
                "Source": str(getattr(item, "source", "") or ""),
            }
        )
    feed_rows.sort(key=lambda row: (row["Critical"] != "YES", row["Feed"]))

    slowest = str(performance.get("slowest_stage") or "")
    slowest_seconds = next(
        (row["Seconds"] for row in stage_rows if row["Stage"] == slowest),
        0.0,
    )
    return {
        "pipeline_seconds": round(pipeline_seconds, 4),
        "build_seconds": round(build_seconds, 4),
        "finalize_seconds": round(finalize_seconds, 4),
        "refresh_interval_seconds": round(interval, 1),
        "refresh_headroom_seconds": round(headroom, 2),
        "refresh_load_pct": round(load_pct, 1),
        "slowest_stage": slowest,
        "slowest_stage_seconds": round(slowest_seconds, 4),
        "stage_rows": stage_rows,
        "feed_rows": feed_rows,
        "critical_issue_count": critical_issue_count,
        "snapshot_mode": (
            "FRESH BUILD THIS RERUN" if snapshot_built_now else "REUSED SNAPSHOT — NO REBUILD THIS RERUN"
        ),
        "note": "Read-only diagnostics from existing snapshot metadata; no broker/API calls and no One-Brain vote.",
    }
