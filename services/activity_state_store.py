from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from config import CONFIG

try:  # Linux/Railway runtime.
    import fcntl
except ImportError:  # pragma: no cover - Windows local fallback.
    fcntl = None


class ActivityStateStore:
    """Small same-day journal with one row per distinct market observation.

    Writes are atomic and guarded by a process-safe file lock so Streamlit reruns,
    background helpers or a transient restart cannot interleave read/modify/write
    cycles.  This remains a tiny local state file; no market calculation depends on
    the storage implementation itself.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or CONFIG.big_player_state_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self, captured_at: datetime) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict) or payload.get("date") != captured_at.date().isoformat():
            return []
        rows = payload.get("rows")
        return list(rows) if isinstance(rows, list) else []

    def load(self, captured_at: datetime) -> list[dict[str, Any]]:
        with self._locked():
            return self._load_unlocked(captured_at)

    def append(
        self,
        captured_at: datetime,
        *,
        direction: str,
        score: float,
        state: str,
        observation_key: str = "",
        spot: float | None = None,
        activity_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        current = {
            "captured_at": captured_at.isoformat(),
            "direction": str(direction),
            "score": float(score),
            "state": str(state),
            "observation_key": str(observation_key),
            "spot": float(spot) if spot is not None else None,
            "activity_payload": activity_payload,
        }
        with self._locked():
            rows = self._load_unlocked(captured_at)
            if rows:
                try:
                    latest = datetime.fromisoformat(str(rows[-1]["captured_at"]))
                    same_observation = bool(observation_key) and str(
                        rows[-1].get("observation_key", "")
                    ) == str(observation_key)
                    if same_observation or (
                        not observation_key
                        and (captured_at - latest).total_seconds()
                        < CONFIG.big_player_dedupe_seconds
                    ):
                        rows[-1] = current
                    else:
                        rows.append(current)
                except (KeyError, TypeError, ValueError):
                    rows.append(current)
            else:
                rows.append(current)
            rows = rows[-CONFIG.big_player_state_max_snapshots :]
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"date": captured_at.date().isoformat(), "rows": rows}, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
            return rows
