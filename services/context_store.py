from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from config import CONFIG
from services.github_journal import GitHubJsonJournal, GitHubJournalError

try:  # Linux/Streamlit runtime.
    import fcntl
except ImportError:  # pragma: no cover - Windows local fallback.
    fcntl = None


@dataclass(frozen=True)
class ContextSyncStatus:
    label: str
    message: str
    enabled: bool
    last_sync_at: str | None = None


class MarketContextStore:
    """Bounded FII/DII journal with local redundancy and optional cloud sync.

    One row is kept per trading date. Local primary + mirror files are merged on every
    read. When a GitHubJsonJournal is configured, the same bounded JSON is also pulled
    and pushed to a private repository. Cloud failure never destroys local data.
    """

    SCHEMA_VERSION = 1
    MAX_ABS_CRORE = 100_000.0
    MAX_FUTURES_CONTRACTS = 10_000_000.0
    ALLOWED_EVENT_RISK = {"NONE", "LOW", "MEDIUM", "HIGH"}
    PRESERVE_IF_BLANK = {
        "fii_cash_net",
        "dii_cash_net",
        "fii_index_futures_net",
        "fii_index_futures_contracts",
        "fii_futures_long_pct",
        "fii_futures_short_pct",
    }

    def __init__(
        self,
        path: str | Path | None = None,
        mirror_path: str | Path | None = None,
        *,
        cloud_backend: GitHubJsonJournal | None = None,
        cloud_pull_ttl_seconds: int | None = None,
    ):
        if path is None:
            self.path = Path(CONFIG.market_context_path)
            self.mirror_path = Path(mirror_path or CONFIG.market_context_mirror_path)
        else:
            self.path = Path(path)
            self.mirror_path = Path(
                mirror_path
                or self.path.with_name(f"{self.path.stem}.mirror{self.path.suffix}")
            )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.cloud_meta_path = self.path.with_name(
            f"{self.path.stem}.cloud_sync{self.path.suffix}"
        )
        self.cloud_backend = cloud_backend
        self.cloud_pull_ttl_seconds = int(
            cloud_pull_ttl_seconds
            if cloud_pull_ttl_seconds is not None
            else CONFIG.market_context_cloud_pull_ttl_seconds
        )
        self._status = ContextSyncStatus(
            label="LOCAL BACKUP",
            message="Cloud journal secrets configured nahi hain.",
            enabled=bool(cloud_backend and cloud_backend.enabled),
        )

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

    @classmethod
    def _number(cls, value: Any, field_name: str = "value") -> float | None:
        if value in (None, ""):
            return None
        try:
            number = round(float(value), 4)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {field_name}: {value!r}") from None
        if abs(number) > cls.MAX_ABS_CRORE:
            raise ValueError(
                f"{field_name} looks too large ({number:,.2f} crore). "
                "Enter the daily net amount in crore, not contracts or cumulative quantity."
            )
        return number

    @classmethod
    def _contracts(cls, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = round(float(value), 2)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid FII index futures contracts: {value!r}") from None
        if number < 0 or number > cls.MAX_FUTURES_CONTRACTS:
            raise ValueError("FII index futures contracts must be a positive quantity")
        return number

    @staticmethod
    def _percent(value: Any, field_name: str) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = round(float(value), 4)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {field_name}: {value!r}") from None
        if not 0.0 <= number <= 100.0:
            raise ValueError(f"{field_name} must be between 0 and 100")
        return number

    @classmethod
    def _futures_percent_pair(
        cls, long_pct: Any, short_pct: Any
    ) -> tuple[float | None, float | None]:
        long_value = cls._percent(long_pct, "FII futures long %")
        short_value = cls._percent(short_pct, "FII futures short %")
        if long_value is None and short_value is not None:
            long_value = round(100.0 - short_value, 4)
        elif short_value is None and long_value is not None:
            short_value = round(100.0 - long_value, 4)
        if long_value is not None and short_value is not None:
            if not 99.0 <= long_value + short_value <= 101.0:
                raise ValueError("FII futures Long % + Short % should be about 100")
        return long_value, short_value

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "entries": []}

    def _read_path(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return self._empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != self.SCHEMA_VERSION
            or not isinstance(data.get("entries"), list)
        ):
            return self._empty()
        return data

    @staticmethod
    def _updated_key(item: dict[str, Any]) -> str:
        return str(item.get("updated_at") or "")

    @staticmethod
    def _sorted_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = [item for item in entries if isinstance(item, dict) and item.get("date")]
        clean.sort(key=lambda item: str(item.get("date", "")))
        return clean

    @classmethod
    def _merge_same_date(
        cls, current: dict[str, Any], incoming: dict[str, Any]
    ) -> dict[str, Any]:
        if cls._updated_key(incoming) >= cls._updated_key(current):
            newer, older = dict(incoming), current
        else:
            newer, older = dict(current), incoming
        for key in cls.PRESERVE_IF_BLANK:
            if newer.get(key) in (None, "") and older.get(key) not in (None, ""):
                newer[key] = older.get(key)
        return newer

    def _merge_entries(self, *entry_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_date: dict[str, dict[str, Any]] = {}
        for group in entry_groups:
            for item in self._sorted_entries(group):
                key = str(item.get("date"))
                current = by_date.get(key)
                by_date[key] = (
                    dict(item)
                    if current is None
                    else self._merge_same_date(current, item)
                )
        return self._sorted_entries(list(by_date.values()))[
            -CONFIG.market_context_max_entries :
        ]

    def _read_unlocked(self) -> dict[str, Any]:
        primary = self._read_path(self.path)
        mirror = self._read_path(self.mirror_path)
        merged = self._merge_entries(primary["entries"], mirror["entries"])
        return {"schema_version": self.SCHEMA_VERSION, "entries": merged}

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        clean = {
            "schema_version": self.SCHEMA_VERSION,
            "entries": self._sorted_entries(data.get("entries", []))[
                -CONFIG.market_context_max_entries :
            ],
        }
        self._atomic_write(self.path, clean)
        self._atomic_write(self.mirror_path, clean)

    def _read_cloud_meta(self) -> dict[str, Any]:
        try:
            value = json.loads(self.cloud_meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_cloud_meta(
        self, *, label: str, message: str, sha: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "label": label,
            "message": message,
            "sha": sha,
            "last_sync_at": now,
        }
        self._atomic_write(self.cloud_meta_path, data)
        self._status = ContextSyncStatus(
            label=label,
            message=message,
            enabled=bool(self.cloud_backend and self.cloud_backend.enabled),
            last_sync_at=now,
        )

    def _cloud_pull_due(self) -> bool:
        meta = self._read_cloud_meta()
        raw = str(meta.get("last_sync_at") or "")
        if not raw:
            return True
        try:
            then = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - then >= timedelta(
            seconds=max(0, self.cloud_pull_ttl_seconds)
        )

    def _set_status_from_meta(self) -> None:
        meta = self._read_cloud_meta()
        if meta:
            self._status = ContextSyncStatus(
                label=str(meta.get("label") or "LOCAL BACKUP"),
                message=str(meta.get("message") or ""),
                enabled=bool(self.cloud_backend and self.cloud_backend.enabled),
                last_sync_at=str(meta.get("last_sync_at") or "") or None,
            )

    def _pull_cloud_unlocked(
        self, *, force: bool
    ) -> tuple[list[dict[str, Any]], str | None]:
        backend = self.cloud_backend
        if backend is None or not backend.enabled:
            self._status = ContextSyncStatus(
                label="LOCAL BACKUP",
                message="Cloud journal secrets configured nahi hain.",
                enabled=False,
            )
            return [], None
        if not force and not self._cloud_pull_due():
            self._set_status_from_meta()
            return [], str(self._read_cloud_meta().get("sha") or "") or None
        try:
            remote = backend.read()
            entries = remote.data.get("entries", [])
            if not isinstance(entries, list):
                entries = []
            message = (
                f"Cloud sync OK · {backend.location}"
                if remote.exists
                else f"Cloud ready · nayi journal file save par banegi · {backend.location}"
            )
            self._write_cloud_meta(
                label="CLOUD SYNC OK", message=message, sha=remote.sha
            )
            return entries, remote.sha
        except GitHubJournalError as exc:
            self._write_cloud_meta(
                label="CLOUD FAILED · LOCAL SAFE",
                message=str(exc),
                sha=str(self._read_cloud_meta().get("sha") or "") or None,
            )
            return [], None

    def _push_cloud_unlocked(
        self, data: dict[str, Any], *, sha: str | None
    ) -> None:
        backend = self.cloud_backend
        if backend is None or not backend.enabled:
            return
        try:
            new_sha = backend.write(data, sha=sha)
            self._write_cloud_meta(
                label="CLOUD SYNC OK",
                message=f"Cloud + local dono safe · {backend.location}",
                sha=new_sha or sha,
            )
            return
        except GitHubJournalError:
            # One serialized retry handles a remote edit or stale SHA without allowing
            # either side to erase newer same-date values.
            try:
                remote = backend.read()
                remote_entries = remote.data.get("entries", [])
                merged = self._merge_entries(data.get("entries", []), remote_entries)
                merged_data = {
                    "schema_version": self.SCHEMA_VERSION,
                    "entries": merged,
                }
                self._write_unlocked(merged_data)
                new_sha = backend.write(merged_data, sha=remote.sha)
                self._write_cloud_meta(
                    label="CLOUD SYNC OK",
                    message=f"Cloud conflict merge karke save hua · {backend.location}",
                    sha=new_sha or remote.sha,
                )
                return
            except GitHubJournalError as exc:
                self._write_cloud_meta(
                    label="CLOUD FAILED · LOCAL SAFE",
                    message=str(exc),
                    sha=sha,
                )

    def sync_now(self) -> list[dict[str, Any]]:
        with self._locked():
            local = self._read_unlocked()
            remote_entries, sha = self._pull_cloud_unlocked(force=True)
            merged = self._merge_entries(local["entries"], remote_entries)
            data = {"schema_version": self.SCHEMA_VERSION, "entries": merged}
            self._write_unlocked(data)
            if self.cloud_backend and self.cloud_backend.enabled:
                self._push_cloud_unlocked(data, sha=sha)
            return list(merged)

    def sync_status(self) -> ContextSyncStatus:
        if not self.cloud_enabled:
            self._status = ContextSyncStatus(
                label="LOCAL BACKUP",
                message="Cloud journal secrets configured nahi hain.",
                enabled=False,
            )
            return self._status
        self._set_status_from_meta()
        return self._status

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.cloud_backend and self.cloud_backend.enabled)

    def load(self) -> list[dict[str, Any]]:
        with self._locked():
            data = self._read_unlocked()
            remote_entries, _ = self._pull_cloud_unlocked(force=False)
            if remote_entries:
                data["entries"] = self._merge_entries(data["entries"], remote_entries)
            self._write_unlocked(data)
            return list(data["entries"])

    def get(self, session_date: date) -> dict[str, Any] | None:
        target = session_date.isoformat()
        for item in self.load():
            if item.get("date") == target:
                return dict(item)
        return None

    def upsert(
        self,
        *,
        session_date: date,
        fii_cash_net: float | None,
        dii_cash_net: float | None,
        fii_index_futures_net: float | None = None,
        fii_index_futures_contracts: float | None = None,
        fii_futures_long_pct: float | None = None,
        fii_futures_short_pct: float | None = None,
        event_risk: str,
        event_note: str = "",
        verified: bool = False,
    ) -> list[dict[str, Any]]:
        level = str(event_risk or "NONE").strip().upper()
        if level not in self.ALLOWED_EVENT_RISK:
            raise ValueError(f"Unsupported event risk: {level}")
        if level in {"MEDIUM", "HIGH"} and not verified:
            raise ValueError("Medium/high event risk must be marked verified")
        futures_long, futures_short = self._futures_percent_pair(
            fii_futures_long_pct, fii_futures_short_pct
        )
        entry = {
            "date": session_date.isoformat(),
            "fii_cash_net": self._number(fii_cash_net, "FII cash net"),
            "dii_cash_net": self._number(dii_cash_net, "DII cash net"),
            "fii_index_futures_net": self._number(
                fii_index_futures_net, "FII index futures net"
            ),
            "fii_index_futures_contracts": self._contracts(
                fii_index_futures_contracts
            ),
            "fii_futures_long_pct": futures_long,
            "fii_futures_short_pct": futures_short,
            "event_risk": level,
            "event_note": str(event_note or "").strip()[:280],
            "verified": bool(verified),
            "source": "MANUAL",
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        with self._locked():
            local = self._read_unlocked()
            remote_entries, sha = self._pull_cloud_unlocked(force=True)
            entries = self._merge_entries(local["entries"], remote_entries)
            existing = next(
                (item for item in entries if item.get("date") == entry["date"]), None
            )
            if existing is not None:
                entry = self._merge_same_date(existing, entry)
            entries = [item for item in entries if item.get("date") != entry["date"]]
            entries.append(entry)
            data = {
                "schema_version": self.SCHEMA_VERSION,
                "entries": self._sorted_entries(entries)[
                    -CONFIG.market_context_max_entries :
                ],
            }
            self._write_unlocked(data)
            self._push_cloud_unlocked(data, sha=sha)
            return list(data["entries"])

    def delete_date(self, session_date: date) -> list[dict[str, Any]]:
        target = session_date.isoformat()
        with self._locked():
            local = self._read_unlocked()
            remote_entries, sha = self._pull_cloud_unlocked(force=True)
            merged = self._merge_entries(local["entries"], remote_entries)
            data = {
                "schema_version": self.SCHEMA_VERSION,
                "entries": self._sorted_entries(
                    [item for item in merged if item.get("date") != target]
                ),
            }
            self._write_unlocked(data)
            self._push_cloud_unlocked(data, sha=sha)
            return list(data["entries"])

    def export_bytes(self) -> bytes:
        data = {
            "schema_version": self.SCHEMA_VERSION,
            "entries": self.load(),
        }
        return json.dumps(data, indent=2, sort_keys=True).encode("utf-8")

    def import_bytes(self, payload: bytes) -> list[dict[str, Any]]:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid context backup: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError("Invalid context backup structure")

        validated: list[dict[str, Any]] = []
        for raw in data["entries"]:
            if not isinstance(raw, dict):
                continue
            try:
                session_date = date.fromisoformat(str(raw.get("date")))
            except ValueError:
                continue
            level = str(raw.get("event_risk") or "NONE").upper()
            verified = bool(raw.get("verified"))
            if level not in self.ALLOWED_EVENT_RISK:
                level = "NONE"
            if level in {"MEDIUM", "HIGH"} and not verified:
                level = "NONE"
            long_pct, short_pct = self._futures_percent_pair(
                raw.get("fii_futures_long_pct"), raw.get("fii_futures_short_pct")
            )
            validated.append(
                {
                    "date": session_date.isoformat(),
                    "fii_cash_net": self._number(
                        raw.get("fii_cash_net"), "FII cash net"
                    ),
                    "dii_cash_net": self._number(
                        raw.get("dii_cash_net"), "DII cash net"
                    ),
                    "fii_index_futures_net": self._number(
                        raw.get("fii_index_futures_net"), "FII index futures net"
                    ),
                    "fii_index_futures_contracts": self._contracts(
                        raw.get("fii_index_futures_contracts")
                    ),
                    "fii_futures_long_pct": long_pct,
                    "fii_futures_short_pct": short_pct,
                    "event_risk": level,
                    "event_note": str(raw.get("event_note") or "").strip()[:280],
                    "verified": verified,
                    "source": str(raw.get("source") or "BACKUP")[:40],
                    "updated_at": str(
                        raw.get("updated_at")
                        or datetime.now().astimezone().isoformat()
                    ),
                }
            )

        with self._locked():
            local = self._read_unlocked()["entries"]
            remote_entries, sha = self._pull_cloud_unlocked(force=True)
            entries = self._merge_entries(local, remote_entries, validated)
            merged = {"schema_version": self.SCHEMA_VERSION, "entries": entries}
            self._write_unlocked(merged)
            self._push_cloud_unlocked(merged, sha=sha)
        return entries

    def clear(self) -> None:
        with self._locked():
            for path in (self.path, self.mirror_path, self.cloud_meta_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
