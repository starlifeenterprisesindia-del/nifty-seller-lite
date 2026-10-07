"""One-click Market Intelligence validation export.

This module is deliberately post-hoc/read-only. It consumes the existing Day Memory
export after the user clicks a download control. It performs no broker/API calculation,
never changes One Brain decisions, and is not imported by the live market engine.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import datetime, timedelta
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile


def _json_body(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(str(key))
                seen.add(str(key))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in fields})
    return output.getvalue().encode("utf-8")


def _parse_evidence(raw_gzip: bytes) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    with gzip.GzipFile(fileobj=io.BytesIO(raw_gzip), mode="rb") as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8")
        for line in text:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            table = item.get("table")
            row = item.get("row")
            if isinstance(table, str) and isinstance(row, dict):
                tables.setdefault(table, []).append(row)
    return tables


def _path_fields(prefix: str, path: Any) -> dict[str, Any]:
    if not isinstance(path, dict):
        return {f"{prefix}_up": None, f"{prefix}_down": None, f"{prefix}_range": None}
    return {
        f"{prefix}_up": path.get("up"),
        f"{prefix}_down": path.get("down"),
        f"{prefix}_range": path.get("range"),
    }


def _future_metrics(samples: list[dict[str, Any]], index: int, horizon_minutes: int) -> dict[str, Any]:
    current = samples[index]
    start_time = current.get("_dt")
    start_spot = current.get("_spot")
    prefix = f"actual_{horizon_minutes}m"
    empty = {
        f"{prefix}_close_change": None,
        f"{prefix}_max_up": None,
        f"{prefix}_max_down": None,
        f"{prefix}_observations": 0,
    }
    if start_time is None or start_spot is None:
        return empty
    target = start_time + timedelta(minutes=horizon_minutes)
    end_limit = target + timedelta(seconds=90)
    future_window: list[dict[str, Any]] = []
    target_row: dict[str, Any] | None = None
    for row in samples[index + 1 :]:
        dt = row.get("_dt")
        spot = row.get("_spot")
        if dt is None:
            continue
        if dt > end_limit:
            break
        if dt <= target + timedelta(seconds=60) and spot is not None:
            future_window.append(row)
        if target_row is None and dt >= target and dt <= end_limit and spot is not None:
            target_row = row
    if not future_window:
        return empty
    spots = [row["_spot"] for row in future_window if row.get("_spot") is not None]
    return {
        f"{prefix}_close_change": round(float(target_row["_spot"]) - float(start_spot), 2) if target_row else None,
        f"{prefix}_max_up": round(max(spots) - float(start_spot), 2) if spots else None,
        f"{prefix}_max_down": round(float(start_spot) - min(spots), 2) if spots else None,
        f"{prefix}_observations": len(spots),
    }


def build_market_intelligence_test_pack(
    evidence_gzip: bytes,
    current_snapshot_summary: dict[str, Any] | None = None,
) -> bytes:
    """Build a compact validation ZIP from the existing recorded evidence."""
    tables = _parse_evidence(evidence_gzip)
    parsed_samples: list[dict[str, Any]] = []
    for row in tables.get("samples", []):
        body = _json_body(row.get("body"))
        at = body.get("at") or row.get("at")
        spot = _float(body.get("spot"))
        parsed_samples.append({"row": row, "body": body, "_dt": _iso(at), "_spot": spot})
    parsed_samples.sort(key=lambda item: item.get("_dt") or datetime.min)

    snapshots: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    experts: list[dict[str, Any]] = []
    alignments: list[dict[str, Any]] = []
    impulse_rows: list[dict[str, Any]] = []
    liquidity_rows: list[dict[str, Any]] = []
    performance_rows: list[dict[str, Any]] = []

    for idx, wrapped in enumerate(parsed_samples):
        body = wrapped["body"]
        mie = body.get("market_intelligence") if isinstance(body.get("market_intelligence"), dict) else {}
        if not mie:
            continue
        at = body.get("at") or wrapped["row"].get("at")
        liquidity = mie.get("liquidity") if isinstance(mie.get("liquidity"), dict) else {}
        primary = liquidity.get("primary_zone") if isinstance(liquidity.get("primary_zone"), dict) else {}
        extension = liquidity.get("extension_zone") if isinstance(liquidity.get("extension_zone"), dict) else {}
        radar = mie.get("move_radar") if isinstance(mie.get("move_radar"), dict) else {}
        base = {
            "timestamp": at,
            "app_version": body.get("version"),
            "spot": body.get("spot"),
            "market_state": mie.get("market_state"),
            "direction": mie.get("direction"),
            "bull_pressure": mie.get("bull_pressure"),
            "bear_pressure": mie.get("bear_pressure"),
            "range_pressure": mie.get("range_pressure"),
            "expansion_pressure": mie.get("expansion_pressure"),
            "pressure_velocity": mie.get("pressure_velocity"),
            "pressure_persistence": mie.get("pressure_persistence"),
            "impulse_state": mie.get("impulse_state"),
            "move_potential": mie.get("move_potential"),
            "volatility_state": mie.get("volatility_state"),
            "structure_event": mie.get("structure_event"),
            "breakout_direction": mie.get("breakout_direction"),
            "breakout_quality": mie.get("breakout_quality"),
            "reversal_direction": mie.get("reversal_direction"),
            "reversal_quality": mie.get("reversal_quality"),
            "institutional_pressure": mie.get("institutional_pressure"),
            "evidence_coverage": mie.get("evidence_coverage"),
            "evidence_conflict": mie.get("evidence_conflict"),
            "conflict_score": mie.get("conflict_score"),
            "fake_move_risk": mie.get("fake_move_risk"),
            "system_status": mie.get("system_status"),
            "one_brain_direction": mie.get("one_brain_direction"),
            "one_brain_alignment": mie.get("one_brain_alignment"),
            "one_brain_action": body.get("background_action"),
            "move_radar_state": radar.get("state"),
            "move_radar_level": radar.get("level"),
            "liquidity_state": liquidity.get("state"),
            "hunt_bias": liquidity.get("hunt_bias"),
            "hunt_strength": liquidity.get("hunt_strength"),
            "upside_hunt_pressure": liquidity.get("upside_hunt_pressure"),
            "downside_hunt_pressure": liquidity.get("downside_hunt_pressure"),
            "liquidity_target_lower": primary.get("lower"),
            "liquidity_target_upper": primary.get("upper"),
            "liquidity_target_attraction": primary.get("attraction_score"),
            "liquidity_target_distance": primary.get("distance_points"),
            "liquidity_target_sources": " | ".join(str(x) for x in (primary.get("sources") or [])),
            "extension_lower": extension.get("lower"),
            "extension_upper": extension.get("upper"),
            "reach_score": liquidity.get("reach_score"),
            "reach_state": liquidity.get("reach_state"),
            "path_clearance": liquidity.get("path_clearance"),
            "sweep_state": liquidity.get("sweep_state"),
            "sweep_outcome": liquidity.get("sweep_outcome"),
            "sweep_quality": liquidity.get("sweep_quality"),
            "invalidation": mie.get("invalidation"),
            **_path_fields("path_5m", mie.get("path_5m")),
            **_path_fields("path_15m", mie.get("path_15m")),
            **_path_fields("path_30m", mie.get("path_30m")),
            **_future_metrics(parsed_samples, idx, 5),
            **_future_metrics(parsed_samples, idx, 15),
            **_future_metrics(parsed_samples, idx, 30),
        }
        snapshots.append(base)

        alignment = str(mie.get("one_brain_alignment") or "")
        if alignment and alignment != "NO CLEAR ALIGNMENT":
            alignments.append(dict(base))

        expansion = _float(mie.get("expansion_pressure")) or 0.0
        velocity = _float(mie.get("pressure_velocity"))
        if expansion >= 50.0 or velocity is not None or str(mie.get("impulse_state") or "").upper() not in {"", "NORMAL"}:
            impulse_rows.append(dict(base))

        if primary or str(liquidity.get("hunt_bias") or "") not in {"", "UNCLEAR", "BALANCED"} or str(liquidity.get("sweep_state") or "NONE") != "NONE":
            liquidity_rows.append(dict(base))

        for alert in mie.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            alerts.append({
                "timestamp": at,
                "type": alert.get("kind") or alert.get("type"),
                "severity": alert.get("priority") or alert.get("severity"),
                "title": alert.get("title"),
                "message": alert.get("message"),
                "direction": mie.get("direction"),
                "expansion_pressure": mie.get("expansion_pressure"),
                "pressure_velocity": mie.get("pressure_velocity"),
                "coverage": mie.get("evidence_coverage"),
                "conflict": mie.get("evidence_conflict"),
                "one_brain_alignment": mie.get("one_brain_alignment"),
                "hunt_bias": liquidity.get("hunt_bias"),
                "liquidity_target_lower": primary.get("lower"),
                "liquidity_target_upper": primary.get("upper"),
                "sweep_state": liquidity.get("sweep_state"),
                "sweep_outcome": liquidity.get("sweep_outcome"),
            })

        for expert in mie.get("experts") or []:
            if not isinstance(expert, dict):
                continue
            experts.append({
                "timestamp": at,
                "expert": expert.get("name"),
                "available": expert.get("available"),
                "bullish": expert.get("bullish"),
                "bearish": expert.get("bearish"),
                "range_score": expert.get("range_score"),
                "reliability": expert.get("reliability"),
                "freshness": expert.get("freshness"),
                "reasons": " | ".join(str(item) for item in (expert.get("reasons") or [])),
            })

        perf = body.get("performance") if isinstance(body.get("performance"), dict) else {}
        if perf:
            performance_rows.append({
                "timestamp": at,
                "pipeline_seconds": perf.get("pipeline_seconds"),
                "build_seconds": perf.get("build_seconds"),
                "market_intelligence_seconds": perf.get("market_intelligence_seconds"),
                "slowest_stage": perf.get("slowest_stage"),
            })

    decision_rows: list[dict[str, Any]] = []
    for row in tables.get("app_decisions", []):
        body = _json_body(row.get("body"))
        if body:
            decision_rows.append(body)

    readme = """One Brain Market Intelligence — Validation Test Pack

This ZIP is post-hoc validation only. It does not call Dhan, does not change One Brain, and does not feed any intelligence score back into live decisions.

Files:
- market_intelligence_snapshots.csv: all recorded OB-MIE states/scores plus observed future spot movement at 5/15/30m.
- impulse_move_review.csv: Move Radar / pressure-building observations for large-candle lead-time testing.
- liquidity_hunt_review.csv: probable liquidity target, hunt pressure, reach score, sweep/breach outcome and observed 5/15/30m movement.
- one_brain_alignment.csv: One Brain + Market Intelligence alignment/conflict observations.
- market_intelligence_alerts.csv: generated precaution/alignment/liquidity alerts.
- expert_evidence.csv: per-family evidence, freshness and reliability.
- one_brain_decisions.csv: recorded One Brain decision journal.
- performance.csv: processing timings, including Market Intelligence runtime.
- current_snapshot.json: current screen snapshot summary when supplied.

Important:
- Path, pressure, hunt and reach scores are evidence scores, NOT calibrated probabilities.
- Liquidity zones infer probable clustered interest; they do not reveal exact retail stop money or participant intent.
- Future outcome columns are retrospective labels only and are never available to the live predictor at prediction time.
- Missing evidence remains NO VOTE.
"""

    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("market_intelligence_snapshots.csv", _csv_bytes(snapshots))
        archive.writestr("impulse_move_review.csv", _csv_bytes(impulse_rows))
        archive.writestr("liquidity_hunt_review.csv", _csv_bytes(liquidity_rows))
        archive.writestr("one_brain_alignment.csv", _csv_bytes(alignments))
        archive.writestr("market_intelligence_alerts.csv", _csv_bytes(alerts))
        archive.writestr("expert_evidence.csv", _csv_bytes(experts))
        archive.writestr("one_brain_decisions.csv", _csv_bytes(decision_rows))
        archive.writestr("performance.csv", _csv_bytes(performance_rows))
        if current_snapshot_summary is not None:
            archive.writestr(
                "current_snapshot.json",
                json.dumps(current_snapshot_summary, indent=2, ensure_ascii=True, default=str),
            )
    return output.getvalue()
