"""On-demand IV-change analytics for the presentation layer.

This module is intentionally outside the One-Brain critical path. It reads the
already-persisted option-state snapshots only when the UI explicitly asks for
IV delta. It never calls Dhan, never changes a MarketSnapshot, and never feeds a
score/decision.
"""
from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Any

from config import CONFIG

WINDOWS: tuple[tuple[str, int], ...] = (("1m", 60), ("3m", 180), ("5m", 300))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot_time(item: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(item.get("captured_at") or ""))
    except (TypeError, ValueError):
        return None


def _choose_history_sample(
    history: list[dict[str, Any]], current_time: datetime, target_seconds: int
) -> tuple[dict[str, Any] | None, float | None]:
    """Mirror the established option-window timing without importing core logic."""

    tolerance = min(
        target_seconds * CONFIG.option_window_tolerance_ratio,
        target_seconds * 0.20,
    )
    minimum_age = max(10.0, target_seconds - tolerance)
    maximum_age = target_seconds + max(
        tolerance, min(90.0, target_seconds * 0.5)
    )
    candidates: list[tuple[float, dict[str, Any], float]] = []
    for item in history:
        captured_at = _snapshot_time(item)
        if captured_at is None:
            continue
        try:
            age = (current_time - captured_at).total_seconds()
        except TypeError:
            # Never mix naive/aware observations. The persisted store is expected
            # to be timezone-aware in production; invalid rows are ignored.
            continue
        if minimum_age <= age <= maximum_age:
            candidates.append((abs(age - target_seconds), item, age))
    if not candidates:
        return None, None
    _, chosen, age = min(candidates, key=lambda pair: pair[0])
    return chosen, round(age, 1)


def _row_key(row: dict[str, Any]) -> tuple[float, str, int | None] | None:
    strike = _finite(row.get("strike"))
    side = str(row.get("side") or "").upper()
    if strike is None or side not in {"CE", "PE"}:
        return None
    security = _finite(row.get("security_id"))
    security_id = int(security) if security is not None else None
    return float(strike), side, security_id


def _fallback_key(row: dict[str, Any]) -> tuple[float, str] | None:
    key = _row_key(row)
    if key is None:
        return None
    return key[0], key[1]


def _matched_deltas(
    current_rows: list[dict[str, Any]], previous_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return per-strike IV deltas in volatility points for valid matched rows."""

    previous_exact: dict[tuple[float, str, int | None], dict[str, Any]] = {}
    previous_fallback: dict[tuple[float, str], dict[str, Any]] = {}
    for row in previous_rows:
        exact = _row_key(row)
        fallback = _fallback_key(row)
        if exact is not None:
            previous_exact[exact] = row
        if fallback is not None:
            previous_fallback[fallback] = row

    result: list[dict[str, Any]] = []
    for row in current_rows:
        exact = _row_key(row)
        fallback = _fallback_key(row)
        if exact is None or fallback is None:
            continue
        prior = previous_exact.get(exact)
        if prior is None:
            prior = previous_fallback.get(fallback)
        if prior is None:
            continue
        current_iv = _finite(row.get("implied_volatility"))
        previous_iv = _finite(prior.get("implied_volatility"))
        # Zero/negative broker IV values are not valid analytical observations.
        if current_iv is None or previous_iv is None or current_iv <= 0 or previous_iv <= 0:
            continue
        result.append(
            {
                "strike": exact[0],
                "side": exact[1],
                "current_iv": round(current_iv, 4),
                "prior_iv": round(previous_iv, 4),
                "iv_delta": round(current_iv - previous_iv, 4),
            }
        )
    return result


def _side_median(rows: list[dict[str, Any]], side: str) -> float | None:
    values = [float(row["iv_delta"]) for row in rows if row.get("side") == side]
    return round(float(median(values)), 4) if values else None


def compute_iv_delta_payload(
    *,
    current_snapshot: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute 1m/3m/5m IV change using only already-persisted snapshots."""

    current_time = _snapshot_time(current_snapshot)
    current_rows = current_snapshot.get("rows") or []
    if current_time is None or not isinstance(current_rows, list) or not current_rows:
        return {
            "status": "UNAVAILABLE",
            "message": "Current compact option snapshot unavailable.",
            "windows": [],
            "preferred_window": None,
            "preferred_strikes": {},
        }

    windows: list[dict[str, Any]] = []
    deltas_by_label: dict[str, list[dict[str, Any]]] = {}
    for label, target_seconds in WINDOWS:
        sample, age = _choose_history_sample(history, current_time, target_seconds)
        if sample is None:
            windows.append(
                {
                    "label": label,
                    "actual_age_seconds": None,
                    "ce_iv_delta": None,
                    "pe_iv_delta": None,
                    "matched_rows": 0,
                    "status": "WARMING UP",
                }
            )
            continue
        previous_rows = sample.get("rows") or []
        if not isinstance(previous_rows, list):
            previous_rows = []
        matched = _matched_deltas(current_rows, previous_rows)
        deltas_by_label[label] = matched
        ce_count = sum(1 for row in matched if row.get("side") == "CE")
        pe_count = sum(1 for row in matched if row.get("side") == "PE")
        ready = ce_count >= 2 and pe_count >= 2
        windows.append(
            {
                "label": label,
                "actual_age_seconds": age,
                "ce_iv_delta": _side_median(matched, "CE") if ready else None,
                "pe_iv_delta": _side_median(matched, "PE") if ready else None,
                "matched_rows": len(matched),
                "status": "READY" if ready else "INSUFFICIENT MATCHES",
            }
        )

    # 3m is the default strike-board context: less noisy than 1m, earlier than 5m.
    # Fall back to another ready window only when 3m continuity is unavailable.
    preferred_label = None
    for candidate in ("3m", "1m", "5m"):
        row = next((item for item in windows if item["label"] == candidate), None)
        if row and row["status"] == "READY":
            preferred_label = candidate
            break

    preferred_strikes: dict[str, dict[float, float]] = {"CE": {}, "PE": {}}
    if preferred_label:
        for row in deltas_by_label.get(preferred_label, []):
            side = str(row.get("side") or "")
            strike = _finite(row.get("strike"))
            delta = _finite(row.get("iv_delta"))
            if side in preferred_strikes and strike is not None and delta is not None:
                preferred_strikes[side][float(strike)] = round(delta, 4)

    ready_count = sum(1 for row in windows if row["status"] == "READY")
    return {
        "status": "READY" if ready_count else "WARMING UP",
        "message": (
            f"{ready_count}/3 IV windows ready; persisted history only, no broker call."
            if ready_count
            else "IV history warming up; no broker call was made."
        ),
        "windows": windows,
        "preferred_window": preferred_label,
        "preferred_strikes": preferred_strikes,
    }
