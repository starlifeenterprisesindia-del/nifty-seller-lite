from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CONFIG


class ActivityStateStore:
    """Small same-day journal with one row per distinct market observation."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or CONFIG.big_player_state_path)

    def load(self, captured_at: datetime) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []
        if payload.get("date") != captured_at.date().isoformat():
            return []
        rows = payload.get("rows")
        return list(rows) if isinstance(rows, list) else []

    def append(
        self,
        captured_at: datetime,
        *,
        direction: str,
        score: float,
        state: str,
        observation_key: str = "",
        spot: float | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.load(captured_at)
        current = {
            "captured_at": captured_at.isoformat(),
            "direction": str(direction),
            "score": float(score),
            "state": str(state),
            "observation_key": str(observation_key),
            "spot": float(spot) if spot is not None else None,
        }
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"date": captured_at.date().isoformat(), "rows": rows}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        return rows
