"""Read-only replay/review helpers built only from persisted observations.

The module never calls a broker, never changes One Brain weights/thresholds and never
writes market state.  It converts already-recorded Railway SQLite evidence into a
small UI payload for post-market or explicit on-demand review.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any, Iterable

from analysis.institutional_window import calculate_institutional_window_from_record
from analysis.liquidity_intelligence import calculate_money_concentration_from_option_chain


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _minute_key(value: Any) -> str:
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value or "")[:16]
    return stamp.replace(second=0, microsecond=0).isoformat()


def _barrier(level: Any) -> dict[str, Any]:
    if not isinstance(level, dict):
        return {}
    return {
        "lower": _num(level.get("lower")),
        "upper": _num(level.get("upper")),
        "strength": _num(level.get("strength")),
        "pressure": _num(level.get("break_pressure")),
        "state": str(level.get("state") or ""),
        "label": str(level.get("label") or ""),
    }


def _wall(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        "strike": _num(raw.get("strike")),
        "oi": _num(raw.get("oi")),
        "previous_strike": _num(raw.get("previous_strike")),
        "migration_points": _num(raw.get("migration_points")),
        "cluster_center": _num(raw.get("cluster_center")),
        "cluster_oi": _num(raw.get("cluster_oi")),
        "status": str(raw.get("status") or ""),
    }


def _option_context(sample: dict[str, Any]) -> dict[str, Any]:
    evidence = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else {}
    option = evidence.get("option_intelligence") if isinstance(evidence.get("option_intelligence"), dict) else {}
    pcr = option.get("pcr") if isinstance(option.get("pcr"), dict) else {}
    return {
        "bias": str(option.get("market_bias") or ""),
        "confidence": _num(option.get("confidence")),
        "persistence": str(option.get("persistence") or ""),
        "ce": _wall(option.get("ce_wall")),
        "pe": _wall(option.get("pe_wall")),
        "pcr": {
            "near_atm": _num(pcr.get("near_atm_oi_pcr")),
            "day_addition": _num(pcr.get("day_addition_pcr")),
            "intraday_addition": _num(pcr.get("intraday_addition_pcr")),
            "volume": _num(pcr.get("volume_pcr")),
            "state": str(pcr.get("state") or ""),
        },
    }



def _market_intelligence_context(sample: dict[str, Any]) -> dict[str, Any]:
    mie = sample.get("market_intelligence") if isinstance(sample.get("market_intelligence"), dict) else {}
    integrity = mie.get("pressure_integrity") if isinstance(mie.get("pressure_integrity"), dict) else {}
    radar = mie.get("move_radar") if isinstance(mie.get("move_radar"), dict) else {}
    liquidity = mie.get("liquidity") if isinstance(mie.get("liquidity"), dict) else {}
    money = liquidity.get("money_concentration") if isinstance(liquidity.get("money_concentration"), dict) else {}
    if not money and sample.get("options") and _num(sample.get("spot")) is not None:
        try:
            money = calculate_money_concentration_from_option_chain(
                sample.get("options") or [], spot=float(sample.get("spot")), atr=12.0
            ).to_dict()
            liquidity = dict(liquidity)
            liquidity["money_concentration"] = money
        except Exception:
            money = {}
    next_hunt = liquidity.get("next_hunt_zone") if isinstance(liquidity.get("next_hunt_zone"), dict) else {}
    sync = sample.get("snapshot_integrity") if isinstance(sample.get("snapshot_integrity"), dict) else {}
    institutional = mie.get("institutional_window") if isinstance(mie.get("institutional_window"), dict) else {}
    if not institutional and mie:
        try:
            mie_for_backfill = dict(mie)
            mie_for_backfill["liquidity"] = liquidity
            historical = calculate_institutional_window_from_record(
                mie_for_backfill,
                activity=sample.get("activity") if isinstance(sample.get("activity"), dict) else {},
                recorded_sync=sync,
                recorded_feeds=sample.get("feeds") if isinstance(sample.get("feeds"), dict) else {},
                live_override=((sample.get("session") or {}).get("is_live") if isinstance(sample.get("session"), dict) else None),
            )
            institutional = historical.to_dict()
        except Exception:
            institutional = {}
    return {
        "market_state": str(mie.get("market_state") or ""),
        "direction": str(mie.get("direction") or ""),
        "expansion_pressure": _num(mie.get("expansion_pressure")),
        "pressure_velocity": _num(mie.get("pressure_velocity")),
        "quality_state": str(integrity.get("quality_state") or ""),
        "quality_score": _num(integrity.get("quality_score")),
        "move_attack_state": str(integrity.get("move_attack_state") or ""),
        "move_risk_state": str(integrity.get("move_risk_state") or ""),
        "radar_state": str(radar.get("state") or "NORMAL"),
        "alignment": str(mie.get("one_brain_alignment") or ""),
        "system_status": str(mie.get("system_status") or ""),
        "hunt_bias": str(liquidity.get("hunt_bias") or ""),
        "money_concentration_bias": str(money.get("bias") or ""),
        "upside_money_score": _num(money.get("upside_score")),
        "downside_money_score": _num(money.get("downside_score")),
        "money_concentration_confidence": _num(money.get("confidence")),
        "next_hunt_lower": _num(next_hunt.get("lower")),
        "next_hunt_upper": _num(next_hunt.get("upper")),
        "institutional_window_state": str(institutional.get("state") or ""),
        "institutional_window_direction": str(institutional.get("direction") or ""),
        "institutional_window_score": _num(institutional.get("opportunity_score")),
        "institutional_window_gates": _num(institutional.get("gates_ready")),
        "institutional_window_data_safety": str(institutional.get("data_safety_state") or ""),
        "sync_state": str(sync.get("state") or ""),
    }

def _decision_statistics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(str(item.get("final_action") or "UNKNOWN") for item in decisions)
    regimes = Counter(str(item.get("regime") or "UNKNOWN") for item in decisions)
    directions = Counter(str(item.get("direction") or "UNKNOWN") for item in decisions)
    readiness = [
        value for value in (_num(item.get("entry_readiness")) for item in decisions) if value is not None
    ]
    horizons: dict[str, Any] = {}
    for horizon in (5, 15, 30):
        point_key = f"outcome_{horizon}m_points"
        label_key = f"outcome_{horizon}m_label"
        covered = [item for item in decisions if _num(item.get(point_key)) is not None]
        points = [_num(item.get(point_key)) for item in covered]
        valid_points = [float(value) for value in points if value is not None]
        labels = Counter(str(item.get(label_key) or "UNLABELLED") for item in covered)
        horizons[f"{horizon}m"] = {
            "covered": len(covered),
            "pending": max(0, len(decisions) - len(covered)),
            "labels": dict(labels),
            "median_move_points": round(median(valid_points), 2) if valid_points else None,
            "median_abs_move_points": (
                round(median(abs(value) for value in valid_points), 2) if valid_points else None
            ),
        }
    return {
        "decision_rows": len(decisions),
        "wait_rows": int(actions.get("WAIT", 0)),
        "signal_rows": int(sum(count for action, count in actions.items() if action != "WAIT")),
        "action_counts": dict(actions),
        "regime_counts": dict(regimes),
        "direction_counts": dict(directions),
        "median_entry_readiness": round(median(readiness), 1) if readiness else None,
        "outcomes": horizons,
        "note": "Descriptive recorded outcomes only; no P&L, fill, win-rate or automatic threshold tuning.",
    }


def _change_count(values: Iterable[Any]) -> int:
    marker = object()
    previous: Any = marker
    changes = 0
    for value in values:
        if value in (None, "", {}):
            continue
        if previous is not marker and value != previous:
            changes += 1
        previous = value
    return changes


def build_replay_bundle(
    samples: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    candles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a bounded, JSON-friendly historical review payload."""

    ordered_samples = sorted(samples, key=lambda item: str(item.get("at") or ""))
    ordered_decisions = sorted(decisions, key=lambda item: str(item.get("at") or ""))
    decision_by_minute = {_minute_key(item.get("at")): item for item in ordered_decisions}

    timeline: list[dict[str, Any]] = []
    barrier_keys: list[tuple[Any, ...]] = []
    ce_strikes: list[float | None] = []
    pe_strikes: list[float | None] = []
    high_bp = 0
    radar_non_normal = 0
    quality_verified = 0
    pressure_flip = 0
    absorption_risk = 0
    institutional_forming = 0
    institutional_open = 0

    for sample in ordered_samples:
        at = str(sample.get("at") or "")
        decision = decision_by_minute.get(_minute_key(at), {})
        barriers = sample.get("barriers") if isinstance(sample.get("barriers"), dict) else {}
        r1 = _barrier(barriers.get("nearest_resistance"))
        r2 = _barrier(barriers.get("next_resistance"))
        s1 = _barrier(barriers.get("nearest_support"))
        s2 = _barrier(barriers.get("next_support"))
        options = _option_context(sample)
        intelligence = _market_intelligence_context(sample)
        activity = sample.get("activity") if isinstance(sample.get("activity"), dict) else {}
        bp_score = _num(activity.get("score"))
        if bp_score is not None and bp_score >= 60:
            high_bp += 1
        if intelligence.get("radar_state") and intelligence.get("radar_state") != "NORMAL":
            radar_non_normal += 1
        qstate = str(intelligence.get("quality_state") or "").upper()
        if qstate in {"VERIFIED", "REALIZED"}:
            quality_verified += 1
        if qstate in {"FLIP WATCH", "FLIP CONFIRMED"}:
            pressure_flip += 1
        if qstate == "ABSORPTION RISK":
            absorption_risk += 1
        iw_state = str(intelligence.get("institutional_window_state") or "").upper()
        if iw_state == "FORMING":
            institutional_forming += 1
        if iw_state in {"OPEN", "STRONG"}:
            institutional_open += 1

        row = {
            "at": at,
            "time": at[11:16] if len(at) >= 16 else at,
            "spot": _num(sample.get("spot")),
            "version": str(sample.get("version") or ""),
            "direction": str(decision.get("direction") or sample.get("direction") or ""),
            "regime": str(decision.get("regime") or ""),
            "entry_readiness": _num(decision.get("entry_readiness")),
            "entry_state": str(decision.get("entry_state") or ""),
            "final_action": str(decision.get("final_action") or sample.get("background_action") or "WAIT"),
            "trigger": str(decision.get("trigger") or ""),
            "reason": str(decision.get("reason") or ""),
            "outcome_5m_points": _num(decision.get("outcome_5m_points")),
            "outcome_15m_points": _num(decision.get("outcome_15m_points")),
            "outcome_30m_points": _num(decision.get("outcome_30m_points")),
            "big_player_direction": str(activity.get("direction") or ""),
            "big_player_score": bp_score,
            "big_player_state": str(activity.get("state") or ""),
            "big_player_type": str(activity.get("activity_type") or ""),
            "mi_market_state": intelligence.get("market_state"),
            "mi_direction": intelligence.get("direction"),
            "mi_pressure": intelligence.get("expansion_pressure"),
            "mi_velocity": intelligence.get("pressure_velocity"),
            "mi_quality_state": intelligence.get("quality_state"),
            "mi_quality_score": intelligence.get("quality_score"),
            "mi_attack_state": intelligence.get("move_attack_state"),
            "mi_risk_state": intelligence.get("move_risk_state"),
            "mi_radar_state": intelligence.get("radar_state"),
            "mi_alignment": intelligence.get("alignment"),
            "mi_system_status": intelligence.get("system_status"),
            "mi_hunt_bias": intelligence.get("hunt_bias"),
            "mi_money_concentration_bias": intelligence.get("money_concentration_bias"),
            "mi_upside_money_score": intelligence.get("upside_money_score"),
            "mi_downside_money_score": intelligence.get("downside_money_score"),
            "mi_money_concentration_confidence": intelligence.get("money_concentration_confidence"),
            "mi_next_hunt_lower": intelligence.get("next_hunt_lower"),
            "mi_next_hunt_upper": intelligence.get("next_hunt_upper"),
            "mi_institutional_window_state": intelligence.get("institutional_window_state"),
            "mi_institutional_window_direction": intelligence.get("institutional_window_direction"),
            "mi_institutional_window_score": intelligence.get("institutional_window_score"),
            "mi_institutional_window_gates": intelligence.get("institutional_window_gates"),
            "mi_institutional_window_data_safety": intelligence.get("institutional_window_data_safety"),
            "snapshot_sync_state": intelligence.get("sync_state"),
            "r1": r1,
            "r2": r2,
            "s1": s1,
            "s2": s2,
            "option_bias": options["bias"],
            "option_confidence": options["confidence"],
            "option_persistence": options["persistence"],
            "ce_wall": options["ce"],
            "pe_wall": options["pe"],
            "pcr": options["pcr"],
        }
        timeline.append(row)
        barrier_keys.append(
            tuple(
                None if not level else (level.get("lower"), level.get("upper"), level.get("state"))
                for level in (r1, r2, s1, s2)
            )
        )
        ce_strikes.append(options["ce"].get("strike"))
        pe_strikes.append(options["pe"].get("strike"))

    clean_candles: list[dict[str, Any]] = []
    for item in sorted(candles, key=lambda row: str(row.get("at") or "")):
        open_ = _num(item.get("open"))
        high = _num(item.get("high"))
        low = _num(item.get("low"))
        close = _num(item.get("close"))
        if None in {open_, high, low, close}:
            continue
        clean_candles.append(
            {
                "at": str(item.get("at") or ""),
                "open": round(float(open_), 2),
                "high": round(float(high), 2),
                "low": round(float(low), 2),
                "close": round(float(close), 2),
                "volume": _num(item.get("volume")),
            }
        )

    event_rows = []
    for item in sorted(events, key=lambda row: str(row.get("at") or "")):
        event_rows.append(
            {
                "at": str(item.get("at") or ""),
                "kind": str(item.get("kind") or ""),
                "identity": str(item.get("identity") or ""),
                "status": str(item.get("status") or ""),
                "side": str(item.get("side") or ""),
                "zone": str(item.get("zone") or ""),
                "action": str(item.get("action") or ""),
                "reason": str(item.get("reason") or ""),
            }
        )

    session_day = timeline[0]["at"][:10] if timeline else (
        ordered_decisions[0].get("session_date") if ordered_decisions else None
    )
    return {
        "schema": 1,
        "session_date": session_day,
        "timeline": timeline,
        "candles_1m": clean_candles,
        "events": event_rows,
        "statistics": {
            **_decision_statistics(ordered_decisions),
            "sample_rows": len(timeline),
            "candle_rows": len(clean_candles),
            "barrier_state_changes": _change_count(barrier_keys),
            "ce_wall_changes": _change_count(ce_strikes),
            "pe_wall_changes": _change_count(pe_strikes),
            "big_player_60plus_samples": high_bp,
            "move_radar_non_normal_samples": radar_non_normal,
            "pressure_verified_or_realized_samples": quality_verified,
            "pressure_flip_samples": pressure_flip,
            "absorption_risk_samples": absorption_risk,
            "institutional_window_forming_samples": institutional_forming,
            "institutional_window_open_or_strong_samples": institutional_open,
        },
        "safety": {
            "broker_calls": 0,
            "brain_writes": 0,
            "threshold_tuning": False,
            "mode": "READ ONLY / ON DEMAND",
        },
        "note": (
            "Historical observations are shown as recorded. Missing minutes remain missing; "
            "future outcomes are labels only and never reconstruct earlier signals."
        ),
    }
