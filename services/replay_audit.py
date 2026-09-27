"""Offline observed-move audit, not a strategy backtest or win-rate estimate.

Usage: python -m services.replay_audit nifty-evidence.jsonl.gz
No network, no writes to the input; emits JSON to stdout. Future prices are used
ONLY as outcome labels, never to reconstruct earlier signals.
"""
import gzip
import json
import sys
from datetime import datetime, timedelta
from statistics import median


def _activity(row):
    raw = row.get("activity") or {}
    if not isinstance(raw, dict):
        return {}
    try:
        score = round(float(raw.get("score") or 0.0), 1)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "direction": str(raw.get("direction") or "MIXED"),
        "score": score,
        "state": str(raw.get("state") or ""),
        "persistence": str(raw.get("persistence") or ""),
        "activity_type": str(raw.get("activity_type") or ""),
        "confirmation_count": int(raw.get("confirmation_count") or 0),
        "confirmation_total": int(raw.get("confirmation_total") or 0),
    }


def audit_samples(samples, horizon_minutes=15, move_points=30):
    """Review observed moves from recorded samples only.

    This is deliberately post-market/offline diagnostics. It never feeds a score,
    changes a threshold or calls a broker. "MOVE WHILE WAIT" means only that the
    saved app/background action at the episode start was WAIT; it is not a claim
    that a profitable trade was missed.
    """
    if horizon_minutes <= 0 or move_points <= 0:
        raise ValueError("Positive horizon and move threshold required")
    rows=sorted(samples,key=lambda r:r["at"])
    episodes=[]
    next_start=None
    for index,row in enumerate(rows):
        at=datetime.fromisoformat(row["at"])
        if next_start and at < next_start:
            continue
        target=at+timedelta(minutes=horizon_minutes)
        future=[r for r in rows[index+1:] if r.get("expiry")==row.get("expiry") and r.get("version")==row.get("version")
                and datetime.fromisoformat(r["at"]).date()==at.date()
                and abs((datetime.fromisoformat(r["at"])-target).total_seconds())<=60]
        if not future:
            continue
        end=min(future,key=lambda r:abs((datetime.fromisoformat(r["at"])-target).total_seconds()))
        segment=[r for r in rows[index:] if at<=datetime.fromisoformat(r["at"])<=datetime.fromisoformat(end["at"])]
        if any((datetime.fromisoformat(b["at"])-datetime.fromisoformat(a["at"])).total_seconds()>120 for a,b in zip(segment,segment[1:])):
            continue
        if any(r.get("version")!=row.get("version") or r.get("expiry")!=row.get("expiry") or r.get("spot") is None
               or any((r.get("feeds",{}).get(k) or {}).get("use_state")!="LIVE" for k in ("quotes","candles")) for r in segment):
            continue
        change=float(end["spot"])-float(row["spot"])
        if abs(change)<move_points:
            continue
        action=row.get("background_action","UNKNOWN")
        observed_direction = "BUYING" if change > 0 else "SELLING"
        start_activity = _activity(row)
        matching = []
        for sample in segment:
            activity = _activity(sample)
            if activity.get("direction") == observed_direction and float(activity.get("score") or 0) >= 60:
                matching.append((sample, activity))
        first_match = matching[0] if matching else None
        end_at = datetime.fromisoformat(end["at"])
        episodes.append({
            "start":row["at"],"end":end["at"],"observed_move":round(change,2),
            "absolute_move":round(abs(change),2),
            "duration_minutes":round((end_at-at).total_seconds()/60.0,1),
            "start_spot":round(float(row["spot"]),2),
            "end_spot":round(float(end["spot"]),2),
            "observed_direction": observed_direction,
            "background_action_at_start":action,"direction_at_start":row.get("direction"),
            "label":"MOVE WHILE WAIT" if action=="WAIT" else "MOVE AFTER SIGNAL",
            "big_player_at_start": start_activity,
            "first_same_direction_big_player_at": first_match[0].get("at") if first_match else None,
            "first_same_direction_big_player_score": first_match[1].get("score") if first_match else None,
            "big_player_observation_lag_minutes": (
                round((datetime.fromisoformat(first_match[0]["at"])-at).total_seconds()/60.0, 1)
                if first_match else None
            ),
            "note":"Observed endpoints only; not a missed profitable trade or actual app decision"
        })
        next_start=datetime.fromisoformat(end["at"])

    directional_at_start = [e for e in episodes if (e.get("big_player_at_start") or {}).get("direction") in {"BUYING","SELLING"}]
    aligned_at_start = [e for e in directional_at_start if e["big_player_at_start"]["direction"] == e["observed_direction"]]
    opposite_at_start = [e for e in directional_at_start if e["big_player_at_start"]["direction"] != e["observed_direction"]]
    same_direction_observed = [e for e in episodes if e.get("first_same_direction_big_player_at")]
    lags = [float(e["big_player_observation_lag_minutes"]) for e in same_direction_observed
            if e.get("big_player_observation_lag_minutes") is not None]
    wait_episodes = [e for e in episodes if e.get("background_action_at_start") == "WAIT"]
    signal_episodes = [e for e in episodes if e.get("background_action_at_start") != "WAIT"]
    upward = [e for e in episodes if e.get("observed_direction") == "BUYING"]
    downward = [e for e in episodes if e.get("observed_direction") == "SELLING"]
    magnitudes = [float(e.get("absolute_move") or 0.0) for e in episodes]
    return {
        "samples":len(rows),"horizon_minutes":horizon_minutes,"move_threshold_points":move_points,
        "non_overlapping_episodes":episodes,
        "episode_summary": {
            "move_episodes": len(episodes),
            "wait_at_start": len(wait_episodes),
            "signal_present_at_start": len(signal_episodes),
            "upward_moves": len(upward),
            "downward_moves": len(downward),
            "median_move_points": round(median(magnitudes), 2) if magnitudes else None,
            "largest_move_points": round(max(magnitudes), 2) if magnitudes else None,
            "median_same_direction_bp_lag_minutes": round(median(lags), 1) if lags else None,
            "same_direction_bp_within_3m": sum(lag <= 3 for lag in lags),
            "same_direction_bp_within_5m": sum(lag <= 5 for lag in lags),
            "note": "Observed-session replay summary only; it is not a backtest, P&L estimate, or win-rate.",
        },
        "big_player_validation": {
            "move_episodes": len(episodes),
            "directional_at_episode_start": len(directional_at_start),
            "aligned_at_episode_start": len(aligned_at_start),
            "opposite_at_episode_start": len(opposite_at_start),
            "same_direction_60plus_observed_within_window": len(same_direction_observed),
            "median_same_direction_lag_minutes": round(median(lags), 1) if lags else None,
            "same_direction_within_3m": sum(lag <= 3 for lag in lags),
            "same_direction_within_5m": sum(lag <= 5 for lag in lags),
            "note": "Diagnostic counts only; thresholds are not auto-tuned and no accuracy/win-rate claim is made.",
        },
        "warning":"Observed endpoints only. Fees, fills, stops and intraminute path not simulated; no accuracy claim."
    }


def read_export(path):
    samples=[]
    with gzip.open(path,"rt",encoding="utf-8") as source:
        header=json.loads(next(source))
        if header.get("format")!="nifty-evidence-jsonl":
            raise ValueError("Unsupported evidence export")
        for line in source:
            item=json.loads(line)
            if item.get("table")=="samples":
                samples.append(json.loads(item["row"]["body"]))
    return samples


if __name__=="__main__":
    print(json.dumps(audit_samples(read_export(sys.argv[1])),indent=2))
