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

from analysis.session_calibration import build_calibration_summary, build_latency_summary
from analysis.institutional_window import calculate_institutional_window_from_record
from analysis.liquidity_intelligence import calculate_money_concentration_from_option_chain


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
    pressure_rows: list[dict[str, Any]] = []
    institutional_rows: list[dict[str, Any]] = []
    performance_rows: list[dict[str, Any]] = []
    previous_institutional: dict[str, Any] = {}

    for idx, wrapped in enumerate(parsed_samples):
        body = wrapped["body"]
        mie = body.get("market_intelligence") if isinstance(body.get("market_intelligence"), dict) else {}
        if not mie:
            continue
        at = body.get("at") or wrapped["row"].get("at")
        liquidity = mie.get("liquidity") if isinstance(mie.get("liquidity"), dict) else {}
        primary = liquidity.get("primary_zone") if isinstance(liquidity.get("primary_zone"), dict) else {}
        extension = liquidity.get("extension_zone") if isinstance(liquidity.get("extension_zone"), dict) else {}
        next_hunt = liquidity.get("next_hunt_zone") if isinstance(liquidity.get("next_hunt_zone"), dict) else {}
        money = liquidity.get("money_concentration") if isinstance(liquidity.get("money_concentration"), dict) else {}
        if not money and body.get("options") and _float(body.get("spot")) is not None:
            # Honest historical backfill from the option rows recorded at this exact
            # timestamp.  Future 5/15/30m movement is never an input.
            try:
                money = calculate_money_concentration_from_option_chain(
                    body.get("options") or [], spot=float(body.get("spot")), atr=12.0
                ).to_dict()
                liquidity = dict(liquidity)
                liquidity["money_concentration"] = money
            except Exception:
                money = {}
        money_primary = money.get("primary_zone") if isinstance(money.get("primary_zone"), dict) else {}
        radar = mie.get("move_radar") if isinstance(mie.get("move_radar"), dict) else {}
        integrity = mie.get("pressure_integrity") if isinstance(mie.get("pressure_integrity"), dict) else {}
        sync = body.get("snapshot_integrity") if isinstance(body.get("snapshot_integrity"), dict) else {}
        institutional = mie.get("institutional_window") if isinstance(mie.get("institutional_window"), dict) else {}
        if not institutional:
            session = body.get("session") if isinstance(body.get("session"), dict) else {}
            mie_for_backfill = dict(mie)
            mie_for_backfill["liquidity"] = liquidity
            historical = calculate_institutional_window_from_record(
                mie_for_backfill, activity=body.get("activity") if isinstance(body.get("activity"), dict) else {},
                recorded_sync=sync,
                recorded_feeds=body.get("feeds") if isinstance(body.get("feeds"), dict) else {},
                previous_window=previous_institutional,
                live_override=bool(session.get("is_live")) if "is_live" in session else None,
            )
            institutional = historical.to_dict()
        previous_institutional = institutional
        iw_edge = institutional.get("directional_edge") if isinstance(institutional.get("directional_edge"), dict) else {}
        iw_opp = institutional.get("opposition_weakness") if isinstance(institutional.get("opposition_weakness"), dict) else {}
        iw_path = institutional.get("path_clearance") if isinstance(institutional.get("path_clearance"), dict) else {}
        iw_capacity = institutional.get("participation_capacity") if isinstance(institutional.get("participation_capacity"), dict) else {}
        iw_trigger = institutional.get("trigger_readiness") if isinstance(institutional.get("trigger_readiness"), dict) else {}
        iw_pressure = institutional.get("pressure_effectiveness") if isinstance(institutional.get("pressure_effectiveness"), dict) else {}
        base = {
            "timestamp": at,
            "app_version": body.get("version"),
            "spot": body.get("spot"),
            "snapshot_sync_state": sync.get("state"),
            "snapshot_core_live": sync.get("core_live"),
            "snapshot_core_total": sync.get("core_total"),
            "snapshot_age_skew_seconds": sync.get("timestamped_age_skew_seconds"),
            "market_state": mie.get("market_state"),
            "direction": mie.get("direction"),
            "early_direction": mie.get("early_direction"),
            "dominant_context": mie.get("dominant_context"),
            "direction_context": mie.get("direction_context"),
            "fast_confirmation_count": mie.get("fast_confirmation_count"),
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
            "institutional_window_state": institutional.get("state"),
            "institutional_window_direction": institutional.get("direction"),
            "institutional_window_score": institutional.get("opportunity_score"),
            "institutional_window_gates_ready": institutional.get("gates_ready"),
            "institutional_window_data_safety": institutional.get("data_safety_state"),
            "institutional_window_alert_eligible": institutional.get("alert_eligible"),
            "institutional_window_transition": institutional.get("transition"),
            "iw_directional_edge": iw_edge.get("score"),
            "iw_directional_edge_state": iw_edge.get("state"),
            "iw_opposition_weakness": iw_opp.get("score"),
            "iw_opposition_weakness_state": iw_opp.get("state"),
            "iw_path_clearance": iw_path.get("score"),
            "iw_path_clearance_state": iw_path.get("state"),
            "iw_participation_capacity": iw_capacity.get("score"),
            "iw_participation_capacity_state": iw_capacity.get("state"),
            "iw_trigger_readiness": iw_trigger.get("score"),
            "iw_trigger_readiness_state": iw_trigger.get("state"),
            "iw_pressure_effectiveness": iw_pressure.get("score"),
            "iw_pressure_effectiveness_state": iw_pressure.get("state"),
            "iw_missing_gates": " | ".join(str(x) for x in (institutional.get("missing_gates") or [])),
            "iw_supportive_signals": " | ".join(str(x) for x in (institutional.get("supportive_signals") or [])),
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
            "pressure_quality_state": integrity.get("quality_state"),
            "pressure_quality_score": integrity.get("quality_score"),
            "real_pressure_score": integrity.get("real_pressure_score"),
            "fake_pressure_score": integrity.get("fake_pressure_score"),
            "move_risk_state": integrity.get("move_risk_state"),
            "move_attack_state": integrity.get("move_attack_state"),
            "price_response_state": integrity.get("price_response_state"),
            "price_response_score": integrity.get("price_response_score"),
            "pressure_efficiency": integrity.get("pressure_efficiency"),
            "pressure_barrier_state": integrity.get("barrier_state"),
            "pressure_barrier_attack": integrity.get("barrier_attack_score"),
            "pressure_barrier_distance": integrity.get("barrier_distance_points"),
            "pressure_barrier_break": integrity.get("barrier_break_pressure"),
            "live_candle_force_state": integrity.get("live_candle_force_state"),
            "live_candle_force_score": integrity.get("live_candle_force_score"),
            "pressure_family_confirmations": integrity.get("family_confirmations"),
            "pressure_family_oppositions": integrity.get("family_oppositions"),
            "pressure_supportive_pattern_score": integrity.get("supportive_pattern_score"),
            "pressure_supportive_signals": " | ".join(str(x) for x in (integrity.get("supportive_signals") or [])),
            "pressure_flip_state": integrity.get("flip_state"),
            "pressure_realized_move": integrity.get("realized_move"),
            "pressure_progress_points": integrity.get("progress_points"),
            "pressure_best_progress_points": integrity.get("best_progress_points"),
            "pressure_realized_threshold_points": integrity.get("realized_threshold_points"),
            "liquidity_state": liquidity.get("state"),
            "liquidity_zone_role": liquidity.get("zone_role"),
            "next_hunt_lower": next_hunt.get("lower"),
            "next_hunt_upper": next_hunt.get("upper"),
            "next_hunt_attraction": next_hunt.get("attraction_score"),
            "next_hunt_distance": next_hunt.get("distance_points"),
            "hunt_bias": liquidity.get("hunt_bias"),
            "hunt_strength": liquidity.get("hunt_strength"),
            "upside_hunt_pressure": liquidity.get("upside_hunt_pressure"),
            "downside_hunt_pressure": liquidity.get("downside_hunt_pressure"),
            "money_concentration_state": money.get("state"),
            "money_concentration_bias": money.get("bias"),
            "money_concentration_confidence": money.get("confidence"),
            "upside_money_score": money.get("upside_score"),
            "downside_money_score": money.get("downside_score"),
            "money_primary_side": money_primary.get("side"),
            "money_primary_strike": money_primary.get("strike"),
            "money_primary_score": money_primary.get("concentration_score"),
            "money_primary_oi": money_primary.get("oi"),
            "money_primary_oi_change": money_primary.get("oi_change"),
            "money_primary_volume": money_primary.get("volume"),
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
            "acceptance_state": liquidity.get("acceptance_state"),
            "sweep_age_seconds": liquidity.get("sweep_age_seconds"),
            "sweep_anchor_lower": ((liquidity.get("sweep_anchor_zone") or {}).get("lower") if isinstance(liquidity.get("sweep_anchor_zone"), dict) else None),
            "sweep_anchor_upper": ((liquidity.get("sweep_anchor_zone") or {}).get("upper") if isinstance(liquidity.get("sweep_anchor_zone"), dict) else None),
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

        quality_state = str(integrity.get("quality_state") or "UNVERIFIED").upper()
        move_risk_state = str(integrity.get("move_risk_state") or "NORMAL").upper()
        if integrity and (move_risk_state != "NORMAL" or quality_state != "UNVERIFIED"):
            pressure_rows.append(dict(base))

        if institutional and (str(institutional.get("state") or "CLOSED").upper() != "CLOSED" or int(_float(institutional.get("gates_ready")) or 0) >= 3):
            institutional_rows.append(dict(base))

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
                "pressure_quality_state": integrity.get("quality_state"),
                "pressure_quality_score": integrity.get("quality_score"),
                "move_attack_state": integrity.get("move_attack_state"),
                "fake_pressure_score": integrity.get("fake_pressure_score"),
                "coverage": mie.get("evidence_coverage"),
                "conflict": mie.get("evidence_conflict"),
                "one_brain_alignment": mie.get("one_brain_alignment"),
                "hunt_bias": liquidity.get("hunt_bias"),
                "liquidity_target_lower": primary.get("lower"),
                "liquidity_target_upper": primary.get("upper"),
                "sweep_state": liquidity.get("sweep_state"),
                "sweep_outcome": liquidity.get("sweep_outcome"),
                "institutional_window_state": institutional.get("state"),
                "institutional_window_score": institutional.get("opportunity_score"),
                "institutional_window_gates": institutional.get("gates_ready"),
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
                "finalize_seconds": perf.get("finalize_seconds"),
                "market_intelligence_seconds": perf.get("market_intelligence_seconds"),
                "slowest_stage": perf.get("slowest_stage"),
                "stages": json.dumps(perf.get("stages") or {}, separators=(",", ":"), sort_keys=True),
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
- pressure_integrity_review.csv: fast move-risk versus pressure quality, price response, barrier attack, supportive candle/W-M evidence, fake/real scores, realization and flip states.
- institutional_window_review.csv: 6-gate Institutional Opportunity Window states, historical backfill where possible, plus 5/15/30m retrospective outcomes.
- liquidity_hunt_review.csv: current battle/next hunt zones, visible Money Concentration proxy, hunt pressure, reach score, sweep/breach outcome, acceptance/reclaim state and observed 5/15/30m movement.
- one_brain_alignment.csv: One Brain + Market Intelligence alignment/conflict observations.
- market_intelligence_alerts.csv: generated precaution/alignment/liquidity alerts.
- expert_evidence.csv: per-family evidence, freshness and reliability.
- one_brain_decisions.csv: recorded One Brain decision journal.
- performance.csv: processing timings, including Market Intelligence runtime.
- calibration_summary.json: post-market descriptive agreement/excursion summaries; never auto-tunes thresholds.
- latency_summary.json: p50/p95 recorded pipeline/build/MI timings and slow-stage counts.
- current_snapshot.json: current screen snapshot summary when supplied.

Important:
- Path, pressure, hunt and reach scores are evidence scores, NOT calibrated probabilities.
- Liquidity zones infer probable clustered interest; they do not reveal exact retail stop money or participant intent.
- Future outcome columns are retrospective labels only and are never available to the live predictor at prediction time.
- Missing evidence remains NO VOTE.
- Move-risk warning is intentionally faster than direction verification. W/M and strong candles are supportive only and are never mandatory gates.
- Pressure that already produced a meaningful move is tagged REALIZED/EXHAUSTING rather than falsely labelled fake only because it later reverses.
- Early direction is a precaution/fast-family consensus; dominant 15m context remains separately recorded.
- First liquidity breach is PENDING ACCEPTANCE; a reclaim starts as REVERSAL WATCH and needs follow-through before REVERSAL FAVORED.
"""

    calibration_summary = build_calibration_summary(snapshots, alerts)
    latency_summary = build_latency_summary(performance_rows)

    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("market_intelligence_snapshots.csv", _csv_bytes(snapshots))
        archive.writestr("impulse_move_review.csv", _csv_bytes(impulse_rows))
        archive.writestr("pressure_integrity_review.csv", _csv_bytes(pressure_rows))
        archive.writestr("institutional_window_review.csv", _csv_bytes(institutional_rows))
        archive.writestr("liquidity_hunt_review.csv", _csv_bytes(liquidity_rows))
        archive.writestr("one_brain_alignment.csv", _csv_bytes(alignments))
        archive.writestr("market_intelligence_alerts.csv", _csv_bytes(alerts))
        archive.writestr("expert_evidence.csv", _csv_bytes(experts))
        archive.writestr("one_brain_decisions.csv", _csv_bytes(decision_rows))
        archive.writestr("performance.csv", _csv_bytes(performance_rows))
        archive.writestr(
            "calibration_summary.json",
            json.dumps(calibration_summary, indent=2, ensure_ascii=True, default=str),
        )
        archive.writestr(
            "latency_summary.json",
            json.dumps(latency_summary, indent=2, ensure_ascii=True, default=str),
        )
        if current_snapshot_summary is not None:
            archive.writestr(
                "current_snapshot.json",
                json.dumps(current_snapshot_summary, indent=2, ensure_ascii=True, default=str),
            )
    return output.getvalue()
