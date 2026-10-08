"""Read-only snapshot freshness/coherence diagnostics.

This module never fetches data, never mutates One Brain inputs, and never changes
any trading score.  It answers one engineering question only: were the evidence
families that already reached the snapshot sufficiently fresh/coherent to be
interpreted together?
"""
from __future__ import annotations

from typing import Any
import math

CORE_DECISION_FEEDS = ("quotes", "candles", "option_chain")
CONTEXT_FEEDS = ("future_volume", "heavyweights", "vix")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def build_snapshot_integrity(snapshot: Any, *, max_age_skew_seconds: float = 20.0) -> dict[str, Any]:
    """Build a cheap, diagnostic-only consistency report from existing FeedStatus.

    Request-time feeds (for example option chain with no exchange timestamp) are
    explicitly labelled rather than assigned a fake age.  They count as available
    when LIVE, but are excluded from age-skew arithmetic.
    """
    statuses = getattr(snapshot, "feed_status", {}) or {}
    rows: list[dict[str, Any]] = []
    core_live = 0
    context_live = 0
    numeric_ages: list[float] = []
    cautions: list[str] = []

    for name in (*CORE_DECISION_FEEDS, *CONTEXT_FEEDS):
        status = statuses.get(name)
        if status is None:
            rows.append({"feed": name, "state": "MISSING", "age_seconds": None, "timestamp_mode": "MISSING"})
            cautions.append(f"{name} missing")
            continue
        state = str(getattr(status, "use_state", "UNAVAILABLE") or "UNAVAILABLE").upper()
        age = _finite(getattr(status, "age_seconds", None))
        live = state == "LIVE"
        if name in CORE_DECISION_FEEDS and live:
            core_live += 1
        if name in CONTEXT_FEEDS and live:
            context_live += 1
        if age is not None and live:
            numeric_ages.append(age)
        if state in {"UNAVAILABLE", "STALE", "DELAYED", "CAUTION"}:
            cautions.append(f"{name} {state}")
        rows.append({
            "feed": name,
            "state": state,
            "age_seconds": round(age, 1) if age is not None else None,
            "timestamp_mode": "SOURCE AGE" if age is not None else "REQUEST-TIME / NO SOURCE AGE",
        })

    skew = (max(numeric_ages) - min(numeric_ages)) if len(numeric_ages) >= 2 else 0.0
    core_coverage = 100.0 * core_live / len(CORE_DECISION_FEEDS)
    context_coverage = 100.0 * context_live / len(CONTEXT_FEEDS)

    session = getattr(snapshot, "market_session", None)
    is_live = bool(getattr(session, "is_live", False))
    if not is_live:
        state = "REFERENCE"
        reason = "Market session live nahi; report reference-only hai."
    elif core_live < len(CORE_DECISION_FEEDS):
        state = "LIMITED"
        reason = "Ek ya zyada core decision feeds LIVE nahi hain; existing NO-VOTE rules remain authoritative."
    elif skew > float(max_age_skew_seconds):
        state = "CAUTION"
        reason = f"Timestamped live feeds ka age skew {skew:.1f}s hai."
    else:
        state = "GOOD"
        reason = "Core feeds LIVE hain aur timestamped source-age skew acceptable hai."

    return {
        "state": state,
        "core_live": core_live,
        "core_total": len(CORE_DECISION_FEEDS),
        "core_coverage_pct": round(core_coverage, 1),
        "context_live": context_live,
        "context_total": len(CONTEXT_FEEDS),
        "context_coverage_pct": round(context_coverage, 1),
        "timestamped_age_skew_seconds": round(skew, 1),
        "max_age_skew_seconds": float(max_age_skew_seconds),
        "rows": rows,
        "cautions": cautions,
        "reason": reason,
        "effect_on_one_brain": "NONE — DIAGNOSTIC ONLY",
    }
