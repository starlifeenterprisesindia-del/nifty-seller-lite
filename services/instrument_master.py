from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
import threading

import pandas as pd
import requests

from config import CONFIG, INSTRUMENT_MASTER_URL
from services.errors import SnapshotBuildError


COLUMN_ALIASES = {
    "security_id": ["SECURITY_ID", "SEM_SMST_SECURITY_ID", "SM_SECURITY_ID"],
    "exchange_id": ["EXCH_ID", "SEM_EXM_EXCH_ID"],
    "segment": ["SEGMENT", "SEM_SEGMENT"],
    "instrument": ["INSTRUMENT", "SEM_INSTRUMENT_NAME"],
    "symbol": ["SYMBOL_NAME", "SM_SYMBOL_NAME"],
    "display_name": ["DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"],
    "expiry": ["SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"],
    "underlying_symbol": ["UNDERLYING_SYMBOL"],
}


@dataclass(frozen=True)
class ResolvedInstrument:
    symbol: str
    security_id: int
    exchange_segment: str
    instrument: str
    display_name: str
    expiry: str | None = None


class InstrumentMaster:
    """Cached Dhan instrument resolver for the dynamic future and index references.

    The CSV is large enough that reparsing and renormalising it on every Streamlit
    rerun can dominate otherwise-light UI work.  Keep a process-wide cache keyed by
    file metadata; Railway/Streamlit workers naturally invalidate it when the cache
    file is refreshed or replaced.
    """

    _memory_lock = threading.RLock()
    _raw_memory: dict[str, tuple[int, int, pd.DataFrame]] = {}
    _normalized_memory: dict[str, tuple[int, int, pd.DataFrame]] = {}
    _vix_memory: dict[tuple[str, int, int], ResolvedInstrument | None] = {}
    _future_memory: dict[tuple[str, int, int, str], ResolvedInstrument | None] = {}

    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or Path("data/instrument_master.csv")
        self._download_lock = threading.Lock()
        self._prewarm_lock = threading.Lock()
        self._prewarm_thread: threading.Thread | None = None
        self._prewarm_error = ""

    def prewarm_async(self) -> None:
        """Start one best-effort background refresh without blocking first paint."""
        with self._prewarm_lock:
            if self._prewarm_thread is not None and self._prewarm_thread.is_alive():
                return
            # A fresh local cache needs no network work.
            identity = self._cache_identity()
            if identity is not None:
                try:
                    age_seconds = max(
                        0.0, datetime.now().timestamp() - self.cache_path.stat().st_mtime
                    )
                    if age_seconds <= CONFIG.instrument_master_cache_max_age_hours * 3600:
                        return
                except OSError:
                    pass

            def _worker() -> None:
                try:
                    self.load(allow_download=True)
                    self._prewarm_error = ""
                except Exception as exc:
                    self._prewarm_error = type(exc).__name__

            self._prewarm_thread = threading.Thread(
                target=_worker, daemon=True, name="instrument-master-prewarm"
            )
            self._prewarm_thread.start()

    def prewarm_status(self) -> dict[str, object]:
        thread = self._prewarm_thread
        return {
            "running": bool(thread is not None and thread.is_alive()),
            "cache_ready": self._cache_identity() is not None,
            "error": self._prewarm_error,
        }

    def _cache_identity(self) -> tuple[str, int, int] | None:
        try:
            stat = self.cache_path.stat()
        except OSError:
            return None
        return (str(self.cache_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))

    @classmethod
    def _remember_raw(cls, identity: tuple[str, int, int], frame: pd.DataFrame) -> None:
        path, mtime_ns, size = identity
        with cls._memory_lock:
            cls._raw_memory[path] = (mtime_ns, size, frame)
            # New raw bytes invalidate the normalized view for this path.
            normalized = cls._normalized_memory.get(path)
            if normalized and normalized[:2] != (mtime_ns, size):
                cls._normalized_memory.pop(path, None)

    @classmethod
    def clear_memory_cache(cls) -> None:
        with cls._memory_lock:
            cls._raw_memory.clear()
            cls._normalized_memory.clear()
            cls._vix_memory.clear()
            cls._future_memory.clear()

    @staticmethod
    def _first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
        normalized = {str(col).strip().upper(): col for col in df.columns}
        for candidate in candidates:
            if candidate in normalized:
                return normalized[candidate]
        return None

    def download(self) -> pd.DataFrame:
        """Refresh the large Dhan master atomically and at most once per instance.

        Streamlit can rerun while a cold-start prewarm is still downloading.  A
        partial CSV used to be visible to a second reader.  The lock + atomic replace
        keeps the old cache usable until the new file is complete.
        """
        with self._download_lock:
            # Another prewarm may have finished while this caller waited.
            identity = self._cache_identity()
            if identity is not None:
                try:
                    age_seconds = max(
                        0.0, datetime.now().timestamp() - self.cache_path.stat().st_mtime
                    )
                    if age_seconds <= CONFIG.instrument_master_cache_max_age_hours * 3600:
                        path, mtime_ns, size = identity
                        with self._memory_lock:
                            memory = self._raw_memory.get(path)
                            if memory and memory[:2] == (mtime_ns, size):
                                return memory[2]
                        cached = pd.read_csv(self.cache_path, low_memory=False)
                        if not cached.empty:
                            self._remember_raw(identity, cached)
                            return cached
                except Exception:
                    pass

            response = requests.get(
                INSTRUMENT_MASTER_URL, timeout=CONFIG.request_timeout_seconds
            )
            response.raise_for_status()
            df = pd.read_csv(StringIO(response.text), low_memory=False)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            try:
                df.to_csv(temporary, index=False)
                temporary.replace(self.cache_path)
            finally:
                try:
                    if temporary.exists():
                        temporary.unlink()
                except OSError:
                    pass
            identity = self._cache_identity()
            if identity is not None:
                self._remember_raw(identity, df)
            return df

    def load(self, *, allow_download: bool = True) -> pd.DataFrame:
        cached: pd.DataFrame | None = None
        cache_is_fresh = False
        identity = self._cache_identity()
        if identity is not None:
            path, mtime_ns, size = identity
            with self._memory_lock:
                memory = self._raw_memory.get(path)
                if memory and memory[:2] == (mtime_ns, size):
                    cached = memory[2]
            if cached is None:
                try:
                    cached = pd.read_csv(self.cache_path, low_memory=False)
                    if cached.empty:
                        cached = None
                    else:
                        self._remember_raw(identity, cached)
                except Exception:
                    cached = None
            try:
                age_seconds = max(
                    0.0, datetime.now().timestamp() - self.cache_path.stat().st_mtime
                )
                cache_is_fresh = (
                    age_seconds <= CONFIG.instrument_master_cache_max_age_hours * 3600
                )
            except OSError:
                cache_is_fresh = False

        if cached is not None and cache_is_fresh:
            return cached
        if allow_download:
            try:
                return self.download()
            except Exception:
                if cached is not None:
                    return cached
                raise
        if cached is not None:
            return cached
        raise SnapshotBuildError("Dhan instrument master is unavailable")

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        identity = self._cache_identity()
        if identity is not None:
            path, mtime_ns, size = identity
            with self._memory_lock:
                raw = self._raw_memory.get(path)
                normalized = self._normalized_memory.get(path)
                if (
                    raw
                    and raw[:2] == (mtime_ns, size)
                    and raw[2] is df
                    and normalized
                    and normalized[:2] == (mtime_ns, size)
                ):
                    return normalized[2]

        result = pd.DataFrame(index=df.index)
        for target, aliases in COLUMN_ALIASES.items():
            source = self._first_existing(df, aliases)
            result[target] = df[source] if source is not None else ""
        result["security_id"] = pd.to_numeric(result["security_id"], errors="coerce")
        text_columns = (
            "symbol",
            "display_name",
            "underlying_symbol",
            "instrument",
            "exchange_id",
            "segment",
        )
        for col in text_columns:
            result[col] = result[col].fillna("").astype(str).str.upper().str.strip()
        result["expiry"] = pd.to_datetime(result["expiry"], errors="coerce")
        result = result.dropna(subset=["security_id"]).copy()

        if identity is not None:
            path, mtime_ns, size = identity
            with self._memory_lock:
                raw = self._raw_memory.get(path)
                if raw and raw[:2] == (mtime_ns, size) and raw[2] is df:
                    self._normalized_memory[path] = (mtime_ns, size, result)
        return result

    def resolve_india_vix(
        self,
        df: pd.DataFrame | None = None,
    ) -> ResolvedInstrument | None:
        identity = self._cache_identity()
        if identity is not None:
            with self._memory_lock:
                if identity in self._vix_memory:
                    return self._vix_memory[identity]
        frame = self.normalize(df if df is not None else self.load())
        symbol_match = frame["symbol"].str.replace(" ", "", regex=False).eq("INDIAVIX")
        display_match = (
            frame["display_name"]
            .str.replace(" ", "", regex=False)
            .str.contains("INDIAVIX", regex=False, na=False)
        )
        candidates = frame[symbol_match | display_match].copy()
        if candidates.empty:
            return None
        index_like = candidates[
            candidates["instrument"].isin(["INDEX", "INDEXVALUE", "IDX"])
        ]
        row = (index_like if not index_like.empty else candidates).iloc[0]
        result = ResolvedInstrument(
            symbol="INDIA VIX",
            security_id=int(row["security_id"]),
            exchange_segment="IDX_I",
            instrument="INDEX",
            display_name=str(row["display_name"] or "INDIA VIX"),
        )
        if identity is not None:
            with self._memory_lock:
                self._vix_memory[identity] = result
        return result

    def resolve_nearest_nifty_future(
        self,
        df: pd.DataFrame | None = None,
        now: datetime | None = None,
    ) -> ResolvedInstrument | None:
        current = pd.Timestamp(now or datetime.now())
        identity = self._cache_identity()
        future_key = (*identity, current.date().isoformat()) if identity is not None else None
        if future_key is not None:
            with self._memory_lock:
                if future_key in self._future_memory:
                    return self._future_memory[future_key]
        frame = self.normalize(df if df is not None else self.load())
        candidates = frame[
            frame["instrument"].isin(["FUTIDX", "FUTURE", "FUTURES"])
            & (
                frame["underlying_symbol"].str.fullmatch("NIFTY", na=False)
                | frame["symbol"].str.fullmatch("NIFTY", na=False)
                | (
                    frame["display_name"].str.contains(
                        r"(?:^|\s)NIFTY(?:\s|$)",
                        regex=True,
                        na=False,
                    )
                    & ~frame["display_name"].str.contains(
                        "BANKNIFTY|FINNIFTY|MIDCPNIFTY",
                        regex=True,
                        na=False,
                    )
                )
            )
            & frame["expiry"].notna()
            & (frame["expiry"] >= current.normalize())
        ].sort_values("expiry")
        if candidates.empty:
            return None
        row = candidates.iloc[0]
        result = ResolvedInstrument(
            symbol="NIFTY_FUT",
            security_id=int(row["security_id"]),
            exchange_segment="NSE_FNO",
            instrument="FUTIDX",
            display_name=str(row["display_name"] or "NIFTY FUTURE"),
            expiry=row["expiry"].date().isoformat(),
        )
        if future_key is not None:
            with self._memory_lock:
                self._future_memory[future_key] = result
        return result
