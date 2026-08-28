"""Offline observed-move audit, not a strategy backtest or win-rate estimate.

Usage: python -m services.replay_audit nifty-evidence.jsonl.gz
No network, no writes to the input; emits JSON to stdout. Future prices are used
ONLY as outcome labels, never to reconstruct earlier signals.
"""
import gzip
import json
import sys
from datetime import datetime, timedelta


def audit_samples(samples, horizon_minutes=15, move_points=30):
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
        episodes.append({"start":row["at"],"end":end["at"],"observed_move":round(change,2),
                         "background_action_at_start":action,"direction_at_start":row.get("direction"),
                         "label":"MOVE WHILE WAIT" if action=="WAIT" else "MOVE AFTER SIGNAL",
                         "note":"Background context only; not a missed profitable trade or actual app decision"})
        next_start=datetime.fromisoformat(end["at"])
    return {"samples":len(rows),"horizon_minutes":horizon_minutes,"move_threshold_points":move_points,
            "non_overlapping_episodes":episodes,"warning":"Observed endpoints only. Fees, fills, stops and intraminute path not simulated; no accuracy claim."}


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
