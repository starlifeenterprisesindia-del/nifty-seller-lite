"""Post-market calibration summaries from already-recorded Market Intelligence rows.

No broker/API calls.  This module never auto-tunes thresholds and never calls the
result an accuracy or win probability.  It produces descriptive diagnostics only.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any
import math


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _med(values: list[float]) -> float | None:
    clean = [float(v) for v in values if _num(v) is not None]
    return round(median(clean), 2) if clean else None


def _direction_sign(direction: Any) -> int:
    text = str(direction or "").upper()
    if "BULL" in text or text in {"UP", "BUYING"}:
        return 1
    if "BEAR" in text or text in {"DOWN", "SELLING"}:
        return -1
    return 0


def _future_abs_excursion(row: dict[str, Any], prefix: str) -> float | None:
    up = _num(row.get(f"actual_{prefix}_max_up"))
    down = _num(row.get(f"actual_{prefix}_max_down"))
    values = [v for v in (up, down) if v is not None]
    return max(values) if values else None


def build_calibration_summary(rows: list[dict[str, Any]], alerts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    snapshots = [dict(row) for row in rows if isinstance(row, dict)]
    by_quality: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_attack: dict[str, list[dict[str, Any]]] = defaultdict(list)
    warning_rows: list[dict[str, Any]] = []

    for row in snapshots:
        by_quality[str(row.get("pressure_quality_state") or "UNKNOWN").upper()].append(row)
        by_attack[str(row.get("move_attack_state") or "UNKNOWN").upper()].append(row)
        radar = str(row.get("move_radar_state") or "NORMAL").upper()
        risk = str(row.get("move_risk_state") or "NORMAL").upper()
        if radar != "NORMAL" or risk != "NORMAL":
            warning_rows.append(row)

    horizon_summary: dict[str, Any] = {}
    for prefix in ("5m", "15m", "30m"):
        close_key = f"actual_{prefix}_close_change"
        covered = [row for row in snapshots if _num(row.get(close_key)) is not None]
        directional = [row for row in covered if _direction_sign(row.get("direction"))]
        agreement = []
        signed_moves = []
        for row in directional:
            change = _num(row.get(close_key))
            sign = _direction_sign(row.get("direction"))
            if change is None or not sign:
                continue
            agreement.append(1 if change * sign > 0 else 0 if change * sign < 0 else None)
            signed_moves.append(change * sign)
        agreement = [x for x in agreement if x is not None]
        warning_excursions = [
            _future_abs_excursion(row, prefix) for row in warning_rows
            if _future_abs_excursion(row, prefix) is not None
        ]
        horizon_summary[prefix] = {
            "covered_snapshots": len(covered),
            "directional_snapshots": len(directional),
            "directional_agreement_pct": round(100.0 * sum(agreement) / len(agreement), 1) if agreement else None,
            "median_directional_signed_move_points": _med(signed_moves),
            "warning_rows_with_outcome": len(warning_excursions),
            "median_abs_excursion_after_warning_points": _med(warning_excursions),
            "note": "Descriptive post-hoc agreement/excursion only; not accuracy, win-rate or probability.",
        }

    quality_rows: list[dict[str, Any]] = []
    for state, items in sorted(by_quality.items()):
        quality_rows.append({
            "Pressure Quality": state,
            "Samples": len(items),
            "Median 5m abs excursion": _med([_future_abs_excursion(x, "5m") for x in items]),
            "Median 15m abs excursion": _med([_future_abs_excursion(x, "15m") for x in items]),
            "Median 30m abs excursion": _med([_future_abs_excursion(x, "30m") for x in items]),
            "Median fake score": _med([_num(x.get("fake_pressure_score")) for x in items]),
            "Median real score": _med([_num(x.get("real_pressure_score")) for x in items]),
        })

    attack_rows: list[dict[str, Any]] = []
    for state, items in sorted(by_attack.items()):
        attack_rows.append({
            "Move Attack": state,
            "Samples": len(items),
            "Median 5m abs excursion": _med([_future_abs_excursion(x, "5m") for x in items]),
            "Median 15m abs excursion": _med([_future_abs_excursion(x, "15m") for x in items]),
        })

    return {
        "snapshot_rows": len(snapshots),
        "warning_rows": len(warning_rows),
        "alert_rows": len(alerts or []),
        "horizons": horizon_summary,
        "pressure_quality_summary": quality_rows,
        "move_attack_summary": attack_rows,
        "rules": [
            "Future columns are retrospective labels only.",
            "No threshold is auto-tuned from this report.",
            "Missing outcomes are excluded, never converted to neutral.",
            "Directional agreement is diagnostic only and must not be advertised as accuracy.",
        ],
    }


def build_latency_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def percentile(values: list[float], pct: float) -> float | None:
        clean = sorted(v for v in (_num(x) for x in values) if v is not None and v >= 0)
        if not clean:
            return None
        if len(clean) == 1:
            return round(clean[0], 4)
        pos = (len(clean) - 1) * pct
        lo = int(pos)
        hi = min(len(clean) - 1, lo + 1)
        frac = pos - lo
        return round(clean[lo] * (1 - frac) + clean[hi] * frac, 4)

    pipeline = [_num(row.get("pipeline_seconds")) for row in rows]
    build = [_num(row.get("build_seconds")) for row in rows]
    intelligence = [_num(row.get("market_intelligence_seconds")) for row in rows]
    slowest: dict[str, int] = defaultdict(int)
    for row in rows:
        name = str(row.get("slowest_stage") or "").strip()
        if name:
            slowest[name] += 1
    return {
        "samples": len(rows),
        "pipeline_p50_seconds": percentile(pipeline, 0.50),
        "pipeline_p95_seconds": percentile(pipeline, 0.95),
        "build_p50_seconds": percentile(build, 0.50),
        "build_p95_seconds": percentile(build, 0.95),
        "market_intelligence_p50_seconds": percentile(intelligence, 0.50),
        "market_intelligence_p95_seconds": percentile(intelligence, 0.95),
        "slowest_stage_counts": dict(sorted(slowest.items(), key=lambda kv: kv[1], reverse=True)),
        "note": "Post-market recorded timing summary only; no live computation added.",
    }
