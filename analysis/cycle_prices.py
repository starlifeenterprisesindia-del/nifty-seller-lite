"""Read-only cycle price views; no trading votes or network access."""
from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def contract_key(row):
    strike = number(row.get("strike"))
    side = row.get("side")
    identity = row.get("security_id")
    if strike is None or side not in ("CE", "PE") or identity in (None, ""):
        return None
    numeric_id = number(identity)
    identity = str(int(numeric_id)) if numeric_id is not None and numeric_id.is_integer() else str(identity)
    return f"{strike:g}|{side}|{identity}"


def cycle_prices(samples, expiry):
    days, contracts = {}, {}
    for sample in samples:
        if str(sample.get("expiry")) != str(expiry):
            continue
        try:
            stamp = datetime.fromisoformat(sample["at"])
            if stamp.tzinfo is None:
                continue
            stamp = stamp.astimezone(IST)
        except (ValueError, TypeError, KeyError):
            continue
        day = stamp.date().isoformat()
        state = days.setdefault(day, {"slots": {}, "spot": [], "contracts": {}, "samples": 0})
        state["samples"] += 1
        feeds = sample.get("feeds") or {}
        spot = number(sample.get("spot")) if feeds.get("quotes", {}).get("use_state") == "LIVE" else None
        if spot is not None and spot > 0:
            state["spot"].append(spot)
        else:
            spot = None
        options, duplicates = {}, set()
        if feeds.get("option_chain", {}).get("use_state") == "LIVE":
            for row in sample.get("options") or []:
                key = contract_key(row)
                if key is None:
                    continue
                if key in options:
                    duplicates.add(key)
                options[key] = row
        options = {k: v for k, v in options.items() if k not in duplicates}
        valid = {}
        for key, row in options.items():
            ltp = number(row.get("last_price"))
            if ltp is None or ltp <= 0:
                continue
            contracts[key] = {"key": key, "strike": number(row["strike"]), "side": row["side"], "security_id": key.split("|")[-1]}
            state["contracts"].setdefault(key, []).append(ltp)
            valid[key] = {k: number(row.get(k)) for k in ("last_price", "oi", "implied_volatility", "top_bid_price", "top_ask_price")}
        slot = stamp.strftime("%H:%M")
        if slot in ("09:30", "15:30") and slot not in state["slots"]:
            state["slots"][slot] = {"observed_at": stamp.isoformat(), "spot": spot, "options": valid}
    rows, summaries = [], []
    for day, state in sorted(days.items()):
        for slot in ("09:30", "15:30"):
            rows.append({"day": day, "time": slot, **state["slots"].get(slot, {"observed_at": None, "spot": None, "options": {}})})
        prices = state["spot"]
        summaries.append({"day": day, "samples": state["samples"], "observed_high": max(prices) if prices else None,
                          "observed_low": min(prices) if prices else None,
                          "contracts": {k: {"observed_high": max(v), "observed_low": min(v)} for k, v in state["contracts"].items()}})
    return {"expiry": expiry, "rows": rows, "days": summaries,
            "contracts": sorted(contracts.values(), key=lambda r: (r["side"], r["strike"], r["security_id"]))}


def selected_rows(view, ce, pe):
    result = []
    for row in view.get("rows", []):
        options = row["options"]
        result.append({"Din": row["day"], "Time IST": row["time"], "Nifty LTP": row["spot"],
                       "CE LTP": options.get(ce, {}).get("last_price"), "PE LTP": options.get(pe, {}).get("last_price"),
                       "Observed at": row["observed_at"] or "Data missing"})
    return result
