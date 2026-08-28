"""Read-only market observations; never shares trade/confirmation state."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def bounded(rows, now, key, expiry=None):
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            stamp = datetime.fromisoformat(row[key])
            if stamp.tzinfo is None or now.tzinfo is None:
                continue
            age = (now - stamp).total_seconds()
            if not 0 < age <= 1800 or stamp.astimezone(IST).date() != now.astimezone(IST).date():
                continue
            if expiry is not None and row.get("expiry") != expiry:
                continue
            result[stamp.isoformat()] = row
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(result.values(), key=lambda row: datetime.fromisoformat(row[key]))[-400:]


def read_history(root, now, expiry):
    def read(name, fallback):
        try:
            return json.loads((root / name).read_text())
        except (OSError, ValueError):
            return fallback
    options = read("options.json", {})
    sessions = options.get("sessions", {}) if isinstance(options, dict) else {}
    if not isinstance(sessions, dict):
        sessions = {}
    rows = sessions.get(f"{now.astimezone(IST).date().isoformat()}|{expiry}", [])
    return {"options": bounded(rows, now, "captured_at", expiry),
            "top9": bounded(read("top9.json", []), now, "at")}
