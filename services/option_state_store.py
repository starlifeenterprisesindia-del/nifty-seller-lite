from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from config import CONFIG

try:  # Linux/Streamlit runtime.
    import fcntl
except ImportError:  # pragma: no cover - Windows local fallback.
    fcntl = None


class OptionStateStore:
    """Bounded same-day persistence for option-chain comparison snapshots.

    It stores only market fields needed for flow comparison. Credentials, headers,
    order data and arbitrary session data are never written.
    """

    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or CONFIG.option_state_path)
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

    @staticmethod
    def _clean_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if pd.isna(number):
            return None
        return number

    @classmethod
    def frame_rows(cls, frame: pd.DataFrame) -> list[dict[str, Any]]:
        keep = (
            "strike",
            "side",
            "security_id",
            "last_price",
            "top_bid_price",
            "top_ask_price",
            "oi",
            "volume",
            "previous_oi",
            "previous_volume",
            "previous_close_price",
            "day_oi_change",
            "day_price_change",
            "implied_volatility",
            "is_atm",
        )
        rows: list[dict[str, Any]] = []
        for raw in frame.to_dict(orient="records"):
            row: dict[str, Any] = {}
            for key in keep:
                value = raw.get(key)
                if key == "side":
                    row[key] = str(value or "").upper()
                elif key == "is_atm":
                    row[key] = bool(value)
                else:
                    row[key] = cls._clean_number(value)
            if row.get("strike") is not None and row.get("side") in {"CE", "PE"}:
                rows.append(row)
        rows.sort(key=lambda item: (item["strike"], item["side"]))
        return rows

    @staticmethod
    def _fingerprint(
        expiry: str, spot: float | None, rows: list[dict[str, Any]], vix: float | None = None
    ) -> str:
        payload = {"expiry": expiry, "spot": spot, "vix": vix, "rows": rows}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]

    def make_snapshot(
        self,
        *,
        captured_at: datetime,
        expiry: str,
        spot: float | None,
        frame: pd.DataFrame,
        vix: float | None = None,
    ) -> dict[str, Any]:
        rows = self.frame_rows(frame)
        clean_spot = self._clean_number(spot)
        clean_vix = self._clean_number(vix)
        return {
            "captured_at": captured_at.isoformat(),
            "expiry": str(expiry),
            "spot": clean_spot,
            "vix": clean_vix,
            "fingerprint": self._fingerprint(str(expiry), clean_spot, rows, clean_vix),
            "rows": rows,
        }

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "sessions": {}, "iv_sessions": {}}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        # Backward-compatible migration from the Phase-6/7 schema.  Raw same-day
        # snapshots are preserved; Phase-8 simply starts accumulating compact
        # daily ATM-IV summaries from this point forward.
        schema = data.get("schema_version")
        if schema not in {1, self.SCHEMA_VERSION}:
            return self._empty()
        if not isinstance(data.get("sessions"), dict):
            return self._empty()
        if not isinstance(data.get("iv_sessions"), dict):
            data["iv_sessions"] = {}
        data["schema_version"] = self.SCHEMA_VERSION
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _session_key(captured_at: datetime, expiry: str) -> str:
        return f"{captured_at.date().isoformat()}|{expiry}"

    @classmethod
    def _snapshot_atm_iv(cls, snapshot: dict[str, Any]) -> float | None:
        spot = cls._clean_number(snapshot.get("spot"))
        rows = snapshot.get("rows") or []
        if spot is None or not isinstance(rows, list):
            return None
        strikes = sorted({
            cls._clean_number(row.get("strike"))
            for row in rows if isinstance(row, dict) and cls._clean_number(row.get("strike")) is not None
        })
        if not strikes:
            return None
        atm = min(strikes, key=lambda value: abs(float(value) - spot))
        ivs = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            strike = cls._clean_number(row.get("strike"))
            iv = cls._clean_number(row.get("implied_volatility"))
            side = str(row.get("side") or "").upper()
            if strike == atm and side in {"CE", "PE"} and iv is not None and iv > 0:
                ivs.append(iv)
        if not ivs:
            return None
        return float(sum(ivs) / len(ivs))

    def load_iv_history(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return compact daily ATM-IV summaries, oldest -> newest.

        The history is produced by the same option-state append that already runs
        for option-flow continuity; this method never talks to Dhan.
        """
        cap = max(1, int(limit or getattr(CONFIG, "iv_history_max_sessions", 60)))
        with self._locked():
            data = self._read_unlocked()
            rows = [row for row in data.get("iv_sessions", {}).values() if isinstance(row, dict)]
        rows.sort(key=lambda row: str(row.get("date") or row.get("updated_at") or ""))
        return rows[-cap:]

    def load_session(
        self, *, captured_at: datetime, expiry: str
    ) -> list[dict[str, Any]]:
        key = self._session_key(captured_at, expiry)
        with self._locked():
            data = self._read_unlocked()
            raw = data["sessions"].get(key, [])
            return list(raw) if isinstance(raw, list) else []

    def append(self, snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        captured_at = datetime.fromisoformat(str(snapshot["captured_at"]))
        expiry = str(snapshot["expiry"])
        key = self._session_key(captured_at, expiry)
        with self._locked():
            data = self._read_unlocked()
            # Active file stays small: keep only the current trading date.
            date_prefix = f"{captured_at.date().isoformat()}|"
            data["sessions"] = {
                name: value
                for name, value in data["sessions"].items()
                if name.startswith(date_prefix)
            }
            history = data["sessions"].setdefault(key, [])
            appended = True
            if history:
                latest = history[-1]
                latest_at = datetime.fromisoformat(str(latest["captured_at"]))
                age = max(0.0, (captured_at - latest_at).total_seconds())
                if (
                    latest.get("fingerprint") == snapshot.get("fingerprint")
                    and age < CONFIG.option_state_dedupe_seconds
                ):
                    appended = False
            if appended:
                history.append(snapshot)
                del history[: -CONFIG.option_state_max_snapshots]

                # Phase-8 historical IV context. Keep one tiny summary per trading
                # date (latest selected expiry for that day) in the SAME atomic
                # write. No extra API request and no second persistence path.
                atm_iv = self._snapshot_atm_iv(snapshot)
                if atm_iv is not None:
                    day = captured_at.date().isoformat()
                    iv_sessions = data.setdefault("iv_sessions", {})
                    previous = iv_sessions.get(day) if isinstance(iv_sessions.get(day), dict) else {}
                    prev_low = self._clean_number(previous.get("low"))
                    prev_high = self._clean_number(previous.get("high"))
                    observations = int(previous.get("observations") or 0) + 1
                    iv_sessions[day] = {
                        "date": day,
                        "expiry": expiry,
                        "open": self._clean_number(previous.get("open")) or atm_iv,
                        "low": min(prev_low, atm_iv) if prev_low is not None else atm_iv,
                        "high": max(prev_high, atm_iv) if prev_high is not None else atm_iv,
                        "last": atm_iv,
                        "vix": self._clean_number(snapshot.get("vix")),
                        "observations": observations,
                        "updated_at": snapshot.get("captured_at"),
                    }
                    max_sessions = max(20, int(getattr(CONFIG, "iv_history_max_sessions", 60)))
                    ordered_days = sorted(iv_sessions)
                    for old_day in ordered_days[:-max_sessions]:
                        iv_sessions.pop(old_day, None)

                self._write_unlocked(data)
            return list(history), appended

    def clear(self) -> None:
        with self._locked():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
