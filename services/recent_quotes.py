"""Bounded, same-session observations. No credentials or broker mutations."""
import json
import os
from datetime import datetime
from pathlib import Path


def record_quotes(quotes, nifty_quote, captured_at, path="data/recent_top9.json"):
    target = Path(path)
    try:
        rows = json.loads(target.read_text())
    except (OSError, ValueError):
        rows = []
    valid = []
    for row in rows if isinstance(rows, list) else []:
        try:
            age = (captured_at - datetime.fromisoformat(row["at"])).total_seconds()
            if 0 <= age <= 1800:
                valid.append(row)
        except (ValueError, KeyError, TypeError):
            continue
    history = list(valid)
    if not valid or (captured_at - datetime.fromisoformat(valid[-1]["at"])).total_seconds() >= 5:
        valid.append({"at": captured_at.isoformat(), "nifty": nifty_quote.get("last_price"),
                      "prices": {q["symbol"]: q.get("last_price") for q in quotes if q.get("symbol")}})
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(valid[-400:]))
        os.replace(temp, target)
    return history
