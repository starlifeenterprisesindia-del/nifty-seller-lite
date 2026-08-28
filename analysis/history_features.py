"""Observed comparisons, never trader counts or calibrated win probabilities.

No extra directional vote: the canonical OI engine already consumes this history.
Keep source time, contract identity and coverage beside every comparison.
"""
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    try:
        value = datetime.fromisoformat(str(value))
        return value.astimezone(IST) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def identity(row):
    security = row.get("security_id")
    strike = number(row.get("strike"))
    side = str(row.get("side", "")).upper()
    if security is None or strike is None or side not in {"CE", "PE"}:
        return None
    return (str(security), strike, side)


def unique_rows(rows):
    result, duplicates = {}, set()
    for row in rows:
        key = identity(row)
        if key is None:
            continue
        if key in result:
            duplicates.add(key)
        result[key] = row
    return {key: row for key, row in result.items() if key not in duplicates}


def oi_history(history, current, *, live=True):
    now = stamp(current.get("captured_at"))
    result = {"status": "REFERENCE" if not live else "WARMING", "windows": [],
              "extra_vote": 0, "note": "OI contracts hain, buyers/sellers ki ginti nahi. Premium + OI sirf build-up inference hai."}
    if not live or now is None:
        return result
    points = {}
    for item in [*history, current]:
        at = stamp(item.get("captured_at"))
        if (at and at.date() == now.date() and item.get("expiry") == current.get("expiry")
                and 0 <= (now-at).total_seconds() <= 1800):
            points[at] = item
    present = unique_rows(current.get("rows", []))
    for minutes in (3, 5, 15):
        target = now-timedelta(minutes=minutes)
        candidates = [t for t in points if abs((t-target).total_seconds()) <= 60 and t < now]
        window = {"minutes": minutes, "status": "WARMING", "rows": []}
        result["windows"].append(window)
        if not candidates:
            continue
        start = min(candidates, key=lambda t: abs((t-target).total_seconds()))
        timeline = sorted(t for t in points if start <= t <= now)
        if any((b-a).total_seconds() > 120 for a,b in zip(timeline,timeline[1:])):
            window["status"] = "DATA GAP"
            continue
        old = unique_rows(points[start].get("rows", []))
        spot_now, spot_before = number(current.get("spot")), number(points[start].get("spot"))
        for key, row in present.items():
            before = old.get(key)
            if not before:
                continue
            values = [number(x.get(k)) for x in (before,row) for k in ("oi", "volume", "last_price")]
            if any(v is None or v < 0 for v in values):
                continue
            old_oi, old_vol, old_price, oi, vol, price = values
            if vol < old_vol or old_price <= 0 or price <= 0:
                continue  # counter reset / invalid quote, not negative participation
            doi, dp = oi-old_oi, price-old_price
            label = ("LONG BUILD-UP" if dp > 0 else "SHORT BUILD-UP") if doi > 0 and dp != 0 else (
                ("SHORT COVERING" if dp > 0 else "LONG UNWINDING") if doi < 0 and dp != 0 else "MIXED / FLAT")
            window["rows"].append({"strike": key[1], "side": key[2], "security_id": key[0],
                "oi_before": old_oi, "oi_now": oi, "oi_change": doi, "volume_added": vol-old_vol,
                "premium_change": round(dp,2), "inference": label})
        window.update(status="READY" if window["rows"] else "NO MATCHED CONTRACTS",
                      start=start.isoformat(), end=now.isoformat(),
                      spot_change=round(spot_now-spot_before,2) if spot_now is not None and spot_before is not None else None)
        bull = bear = 0.0
        for row in window["rows"]:
            # Use the same current ATM neighbourhood for both endpoints, not the
            # whole chain's distant strikes; no volume = no inferred fresh flow.
            if spot_now is None or abs(row["strike"]-spot_now)>250 or row["volume_added"] <= 0:
                continue
            bullish = ((row["side"]=="CE" and row["inference"] in {"LONG BUILD-UP","SHORT COVERING"}) or
                       (row["side"]=="PE" and row["inference"] in {"SHORT BUILD-UP","LONG UNWINDING"}))
            bearish = ((row["side"]=="CE" and row["inference"] in {"SHORT BUILD-UP","LONG UNWINDING"}) or
                       (row["side"]=="PE" and row["inference"] in {"LONG BUILD-UP","SHORT COVERING"}))
            bull += abs(row["oi_change"]) if bullish else 0
            bear += abs(row["oi_change"]) if bearish else 0
        balance = (bull-bear)/(bull+bear) if bull+bear else 0
        pressure = "BULLISH" if balance > .2 else "BEARISH" if balance < -.2 else "MIXED"
        move = window.get("spot_change")
        supported = move is not None and ((pressure=="BULLISH" and move>0) or (pressure=="BEARISH" and move<0))
        window.update(inferred_pressure=pressure, price_supports_pressure=supported,
                      pressure_note="OI-change weighted inference, not buyer majority; no independent AI vote")
    result["status"] = "READY" if any(w["status"] == "READY" for w in result["windows"]) else "WARMING"
    return result


def futures_vwap(frame, now):
    """Completed current-session futures bars only; no spot-volume substitution."""
    result = {"status": "UNAVAILABLE", "extra_vote": 0, "source": "NIFTY FUTURES", "note": "Futures price ko futures VWAP se compare kiya; spot se nahi."}
    if frame.empty or not {"timestamp", "high", "low", "close", "volume", "is_complete"} <= set(frame):
        return result
    now = now.astimezone(IST)
    bars = frame.copy()
    bars["at"] = [stamp(t) for t in bars.timestamp]
    bars = bars[bars["at"].map(lambda t: t is not None and t.date() == now.date() and
                          (t.hour,t.minute) >= (9,15) and t+timedelta(minutes=1) <= now)]
    bars = bars[bars.is_complete.eq(True)].sort_values("at").drop_duplicates("at", keep="last")
    if bars.empty:
        return result
    if (now-(bars.iloc[-1]["at"]+timedelta(minutes=1))).total_seconds() > 180:
        return {**result, "status": "STALE"}
    for name in ("high", "low", "close", "volume"):
        bars[name] = pd.to_numeric(bars[name], errors="coerce")
    if any(number(v) is None for v in bars[["high","low","close","volume"]].to_numpy().flat) or (bars.volume < 0).any():
        return result
    volume = bars.volume.sum()
    if volume <= 0:
        return result
    typical = (bars.high+bars.low+bars.close)/3
    vwap = float((typical*bars.volume).sum()/volume)
    latest = float(bars.iloc[-1].close)
    first = bars.iloc[0]["at"]
    expected = int((bars.iloc[-1]["at"]-first).total_seconds()//60)+1
    complete = (first.hour,first.minute)==(9,15) and expected == len(bars)
    return {**result, "status": "READY" if complete else "PARTIAL SESSION",
            "value": round(vwap,2), "future_close": latest, "distance": round(latest-vwap,2),
            "position": "ABOVE" if latest>vwap else "BELOW" if latest<vwap else "AT",
            "bars": len(bars), "last_candle": bars.iloc[-1]["at"].isoformat()}


def institutional_trends(entries, today):
    # Prefer latest supplied row per date. Missing values are not zero flows.
    dated = {str(e.get("date")):e for e in entries if str(e.get("date", "")) < today.isoformat() and e.get("date")}
    dates = sorted(dated)[-15:]
    rows = []
    for field in ("fii_cash_net", "dii_cash_net", "fii_index_futures_contracts"):
        item = {"field": field, "unit": "contracts" if field.endswith("contracts") else "crore"}
        for size in (3,5,15):
            values = [number(dated[d].get(field)) for d in dates[-size:]]
            item[f"{size}_sum"] = round(sum(values),2) if len(values)==size and all(v is not None for v in values) else None
            item[f"{size}_available"] = sum(v is not None for v in values)
        item["latest"] = number(dated[dates[-1]].get(field)) if dates else None
        previous = number(dated[dates[-2]].get(field)) if len(dates)>1 else None
        item["change_vs_previous"] = round(item["latest"]-previous,2) if item["latest"] is not None and previous is not None else None
        rows.append(item)
    return {"as_of": dates[-1] if dates else None, "rows": rows, "extra_vote": 0,
            "note": "Prior reported sessions only; cash crore aur futures contracts alag. Missing sessions ko zero nahi mana."}
