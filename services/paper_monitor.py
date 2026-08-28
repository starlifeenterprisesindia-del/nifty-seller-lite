"""Server-side observation of registered paper positions. Never creates orders."""
import json
from services.shadow_journal import ShadowJournalStore, _close_open_entries


class PaperMonitor:
    def __init__(self, path):
        self.store = ShadowJournalStore(path)

    def register(self, entries):
        if not isinstance(entries, list) or len(entries) > 500:
            raise ValueError("Invalid paper records")
        if len(json.dumps(entries, allow_nan=False)) > 2_000_000:
            raise ValueError("Paper payload too large")
        with self.store._locked():
            data = self.store._read_local()
            existing = {e["trade_id"]: e for e in data["entries"] if e.get("trade_id")}
            for entry in entries:
                if not isinstance(entry, dict) or not str(entry.get("trade_id", "")).startswith("SH-"):
                    continue
                key = entry["trade_id"]
                if key not in existing:
                    existing[key] = dict(entry)
                elif existing[key].get("status") != "CLOSED" and entry.get("status") == "CLOSED":
                    existing[key] = dict(entry)
            data["entries"] = list(existing.values())[-500:]
            self.store._write_local(data)
            return data["entries"]

    def observe(self, snapshot):
        # Same lock as registration prevents an in-flight UI update losing entries.
        with self.store._locked():
            data = self.store._read_local()
            changed, _ = _close_open_entries(data["entries"], snapshot)
            if changed:
                self.store._write_local(data)
