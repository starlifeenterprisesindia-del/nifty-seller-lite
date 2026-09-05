"""Frozen candidate observations, not fills or backtested trading profits."""
import json
import math
from datetime import datetime


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def quote(row):
    bid, ask = number(row.get("top_bid_price")), number(row.get("top_ask_price"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    if (ask-bid) / ((ask+bid)/2) > .15:
        return None
    return bid, ask


def contract_id(value):
    if value is None or str(value).strip() == "":
        return None
    numeric = number(value)
    return str(int(numeric)) if numeric is not None and numeric.is_integer() else str(value)[:80]


def frozen_basket(body):
    legs = body.get("legs")
    if not isinstance(legs, list) or len(legs) not in (2, 4):
        return [], None
    saved, credit = [], 0.0
    for row in legs:
        if not isinstance(row, dict):
            return [], None
        prices = quote(row)
        strike = number(row.get("strike"))
        if not prices or strike is None or strike <= 0 or contract_id(row.get("security_id")) is None or row.get("side") not in ("CE", "PE") or row.get("role") not in ("BUY", "SELL", "HEDGE"):
            return [], None
        item = {k: row.get(k) for k in ("strike", "side", "role", "security_id", "top_bid_price", "top_ask_price")}
        item["strike"] = strike
        item["top_bid_price"], item["top_ask_price"] = prices
        item["security_id"] = contract_id(item["security_id"])
        saved.append(item)
    # Only protected equal-quantity spreads; never a naked or duplicated leg.
    keys = {(x["strike"], x["side"]) for x in saved}
    if len(keys) != len(saved):
        return [], None
    buys = [x for x in saved if x["role"] == "BUY"]
    if buys:
        sells = [x for x in saved if x["role"] == "SELL"]
        if len(saved) != 2 or len(buys) != 1 or len(sells) != 1 or buys[0]["side"] != sells[0]["side"]:
            return [], None
        side = buys[0]["side"]
        if (side == "CE" and sells[0]["strike"] <= buys[0]["strike"]) or (side == "PE" and sells[0]["strike"] >= buys[0]["strike"]):
            return [], None
        debit = buys[0]["top_ask_price"] - sells[0]["top_bid_price"]
        width = abs(sells[0]["strike"] - buys[0]["strike"])
        return (saved, debit) if 0 < debit < width else ([], None)
    if sum(x["role"] == "SELL" for x in saved) != len(saved)//2:
        return [], None
    credit = sum(
        x["top_bid_price"] if x["role"] == "SELL" else -x["top_ask_price"]
        for x in saved
    )
    widths = []
    for short in [x for x in saved if x["role"] == "SELL"]:
        hedges = [x for x in saved if x["role"] == "HEDGE" and x["side"] == short["side"]]
        if len(hedges) != 1:
            return [], None
        if (short["side"] == "CE" and hedges[0]["strike"] <= short["strike"]) or (short["side"] == "PE" and hedges[0]["strike"] >= short["strike"]):
            return [], None
        widths.append(abs(hedges[0]["strike"]-short["strike"]))
    return (saved, credit) if 0 < credit < max(widths) else ([], None)


def record_signal(db, body):
    from services.day_memory import encode
    spot = number(body.get("spot"))
    if spot is None or spot <= 0 or body.get("fresh") is not True:
        return
    expiry = str(body.get("expiry", ""))
    try:
        datetime.fromisoformat(expiry)
    except ValueError:
        return
    legs, credit = frozen_basket(body)
    is_debit = any(row.get("role") == "BUY" for row in legs)
    item = {"at": body["at"], "expiry": expiry, "spot": spot,
            "action": str(body.get("action", ""))[:80], "candidate": str(body.get("candidate", ""))[:80],
            "version": str(body.get("version", ""))[:80], "legs": legs,
            "structure_type": "DEBIT" if is_debit else "CREDIT",
            "entry_credit": None if is_debit else credit,
            "entry_debit": credit if is_debit else None,
            "score_band": int((number(body.get("score")) or 0)//5)*5,
            "label": "Candidate observation; no order/fill, no fees included"}
    future = body.get("future_brain") or {}
    if not isinstance(future, dict):
        future = {}
    item["future_brain"] = {key: future.get(key) for key in (
        "feature_key", "current_direction", "next_direction", "transition",
        "up_5m", "down_5m", "range_5m", "up_15m", "down_15m", "range_15m",
        "final_gate", "model_label")}
    institutional = body.get("institutional") or {}
    if not isinstance(institutional, dict):
        institutional = {}
    item["institutional"] = {"as_of_date": str(institutional.get("as_of_date", ""))[:10],
                             "source": "App institutional context at capture; not inferred from price/OI"}
    for key in ("latest_fii_net", "latest_dii_net", "latest_fii_index_futures_contracts", "latest_fii_futures_long_pct", "latest_fii_futures_short_pct"):
        item["institutional"][key] = number(institutional.get(key))
    signature = encode({k: item[k] for k in ("expiry", "action", "candidate", "version", "score_band")} | {
        "future_feature": item["future_brain"].get("feature_key"),
        "future_direction": item["future_brain"].get("next_direction"),
        "legs": [{k:x[k] for k in ("strike", "side", "role", "security_id")} for x in legs]})
    old = db.execute("SELECT body FROM state WHERE identity='signal_signature'").fetchone()
    if old and old[0] == signature:
        return
    db.execute("INSERT INTO signals(at,body) VALUES (?,?)", (body["at"], encode(item)))
    db.execute("INSERT OR REPLACE INTO state VALUES ('signal_signature',?)", (signature,))


def mark_spread(signal, sample):
    if sample.get("expiry") != signal["expiry"] or sample.get("version") != signal["version"]:
        return None
    if not signal.get("legs"):
        return None
    is_debit = signal.get("structure_type") == "DEBIT" or any(
        leg.get("role") == "BUY" for leg in signal["legs"]
    )
    if is_debit and signal.get("entry_debit") is None:
        return None
    if not is_debit and signal.get("entry_credit") is None:
        return None
    close_value = 0.0
    for leg in signal["legs"]:
        matches = [r for r in sample.get("options", []) if r.get("strike") == leg["strike"] and r.get("side") == leg["side"]
                   and contract_id(r.get("security_id")) == contract_id(leg.get("security_id"))]
        if len(matches) != 1 or not quote(matches[0]):
            return None
        bid, ask = quote(matches[0])
        if is_debit:
            close_value += bid if leg["role"] == "BUY" else -ask
        else:
            close_value += ask if leg["role"] == "SELL" else -bid
    return (
        close_value - signal["entry_debit"]
        if is_debit
        else signal["entry_credit"] - close_value
    )


def update_outcomes(db, sample):
    from services.day_memory import encode
    now = datetime.fromisoformat(sample["at"])
    pending = db.execute("SELECT id,at,body FROM signals WHERE (SELECT COUNT(*) FROM outcomes WHERE signal_id=signals.id)<3").fetchall()
    for identity, stamp, raw in pending:
        signal = json.loads(raw)
        start = datetime.fromisoformat(stamp)
        elapsed = (now-start).total_seconds()/60
        for horizon in (5,15,30):
            if elapsed < horizon or db.execute("SELECT 1 FROM outcomes WHERE signal_id=? AND horizon=?", (identity,horizon)).fetchone():
                continue
            result = {"candidate": signal["candidate"], "action": signal["action"], "status": "UNAVAILABLE",
                      "label": "Observed simulation, points per equal-quantity spread; not realised P&L"}
            result["feature_key"] = (signal.get("future_brain") or {}).get("feature_key")
            if now.date() == start.date() and horizon <= elapsed <= horizon+2:
                end_spot = number(sample.get("spot"))
                result.update(status="OBSERVED", observed_minutes=round(elapsed,2),
                              spot_change=None if end_spot is None else round(end_spot-signal["spot"],2),
                              move_threshold_points=8.0)
                history = [json.loads(r[0]) for r in db.execute("SELECT body FROM samples WHERE at>=? AND at<=? ORDER BY at", (start.replace(second=0,microsecond=0).isoformat(),now.isoformat()))]
                history = [r for r in history if start <= datetime.fromisoformat(r["at"]) <= now]
                times = [start]+[datetime.fromisoformat(r["at"]) for r in history]+[now]
                covered = all((b-a).total_seconds() <= 120 for a,b in zip(times,times[1:]))
                marks = [mark_spread(signal, s) for s in history]
                pnl = mark_spread(signal, sample)
                spots = [number(s.get("spot")) for s in history]
                spots = [x for x in spots if x is not None]
                result.update(spread_points=None if pnl is None else round(pnl,2),
                              coverage="SAMPLED" if covered else "GAPS",
                              observed_up_points=round(max(spots+[signal["spot"]])-signal["spot"],2),
                              observed_down_points=round(signal["spot"]-min(spots+[signal["spot"]]),2),
                              spread_path_complete=covered and bool(marks) and all(x is not None for x in marks))
                valid = [x for x in marks if x is not None]
                result["observed_max_loss_points"] = round(max(0,-min(valid)),2) if valid else None
                result["observed_max_gain_points"] = round(max(0,max(valid)),2) if valid else None
            db.execute("INSERT INTO outcomes VALUES (?,?,?)", (identity,horizon,encode(result)))


def cycle_summary(db, expiry):
    outcomes = [json.loads(r[0]) for r in db.execute("SELECT body FROM outcomes")]
    observed = [x for x in outcomes if x.get("status") == "OBSERVED"]
    groups = {}
    for horizon, raw in db.execute("SELECT horizon,body FROM outcomes"):
        item = json.loads(raw)
        key = f"{item.get('action')} / {item.get('candidate')} / {horizon}m"
        bucket = groups.setdefault(key, {"observations": 0, "priced": 0, "points_sum": 0.0, "gaps": 0,
                                         "negative_observations": 0, "worst_observed_loss_points": None})
        bucket["observations"] += 1
        bucket["gaps"] += item.get("coverage") == "GAPS" or item.get("status") == "UNAVAILABLE"
        value = item.get("spread_points")
        if value is not None:
            bucket["priced"] += 1
            bucket["points_sum"] += value
            bucket["negative_observations"] += value < 0
        adverse = item.get("observed_max_loss_points")
        if adverse is not None:
            bucket["worst_observed_loss_points"] = max(bucket["worst_observed_loss_points"] or 0,adverse)
    for bucket in groups.values():
        total = bucket.pop("points_sum")
        bucket["mean_observed_spread_points"] = round(total/bucket["priced"],2) if bucket["priced"] else None
    return {"expiry": expiry, "groups": groups, "sessions": db.execute("SELECT COUNT(DISTINCT substr(at,1,10)) FROM samples").fetchone()[0],
            "samples": db.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
            "signals": db.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
            "observed_horizons": len(observed), "unavailable_horizons": sum(x.get("status") != "OBSERVED" for x in outcomes),
            "pending_horizons": 3*db.execute("SELECT COUNT(*) FROM signals").fetchone()[0]-len(outcomes),
            "spread_horizons_with_quotes": sum(x.get("spread_points") is not None for x in observed),
            "note": "Overlapping candidate observations, not independent trades or accuracy estimate"}
