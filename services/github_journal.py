from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import requests


class GitHubJournalError(RuntimeError):
    """Safe cloud-journal failure without exposing credentials."""


@dataclass(frozen=True)
class GitHubJournalSnapshot:
    data: dict[str, Any]
    sha: str | None
    exists: bool


class GitHubJsonJournal:
    """Tiny JSON document store backed by GitHub's repository-contents API.

    This service is persistence only. It never calculates market direction, strategy
    scores or trade actions. A fine-grained token can be limited to one private data
    repository with Contents read/write permission.
    """

    API_ROOT = "https://api.github.com"
    API_VERSION = "2026-03-10"

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        token: str,
        path: str = "fii_dii_15_sessions.json",
        branch: str = "main",
        timeout_seconds: int = 8,
        session: requests.Session | None = None,
    ) -> None:
        self.owner = str(owner or "").strip()
        self.repo = str(repo or "").strip()
        self.token = str(token or "").strip()
        self.path = str(path or "fii_dii_15_sessions.json").strip().lstrip("/")
        self.branch = str(branch or "main").strip()
        self.timeout_seconds = max(2, int(timeout_seconds))
        self.session = session or requests.Session()

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Any] | None, *, timeout_seconds: int = 8
    ) -> GitHubJsonJournal | None:
        if not values:
            return None
        owner = str(values.get("owner") or "").strip()
        repo = str(values.get("repo") or "").strip()
        token = str(values.get("token") or "").strip()
        if not (owner and repo and token):
            return None
        return cls(
            owner=owner,
            repo=repo,
            token=token,
            path=str(values.get("path") or "fii_dii_15_sessions.json"),
            branch=str(values.get("branch") or "main"),
            timeout_seconds=timeout_seconds,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.owner and self.repo and self.token and self.path)

    @property
    def location(self) -> str:
        return f"{self.owner}/{self.repo}:{self.path}"

    def _url(self) -> str:
        owner = quote(self.owner, safe="")
        repo = quote(self.repo, safe="")
        path = quote(self.path, safe="/")
        return f"{self.API_ROOT}/repos/{owner}/{repo}/contents/{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": "nifty-seller-lite-fii-dii-journal",
        }

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": 1, "entries": []}

    @staticmethod
    def _safe_error(response: requests.Response) -> str:
        message = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                message = str(payload.get("message") or "")
        except ValueError:
            message = ""
        message = message.replace("\n", " ").strip()[:180]
        return f"GitHub returned HTTP {response.status_code}" + (
            f" ({message})" if message else ""
        )

    def read(self) -> GitHubJournalSnapshot:
        if not self.enabled:
            raise GitHubJournalError("Cloud journal is not configured")
        try:
            response = self.session.get(
                self._url(),
                headers=self._headers(),
                params={"ref": self.branch},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GitHubJournalError(f"GitHub read failed: {exc.__class__.__name__}") from None

        if response.status_code == 404:
            return GitHubJournalSnapshot(data=self._empty(), sha=None, exists=False)
        if response.status_code != 200:
            raise GitHubJournalError(self._safe_error(response))

        try:
            envelope = response.json()
            encoded = str(envelope.get("content") or "").replace("\n", "")
            raw = base64.b64decode(encoded, validate=True)
            data = json.loads(raw.decode("utf-8"))
            sha = str(envelope.get("sha") or "").strip() or None
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubJournalError(
                f"Cloud journal content is invalid: {exc.__class__.__name__}"
            ) from None
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise GitHubJournalError("Cloud journal JSON structure is invalid")
        return GitHubJournalSnapshot(data=data, sha=sha, exists=True)

    def write(self, data: dict[str, Any], *, sha: str | None) -> str:
        if not self.enabled:
            raise GitHubJournalError("Cloud journal is not configured")
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload: dict[str, Any] = {
            "message": "Update Nifty Seller Lite FII/DII journal",
            "content": base64.b64encode(raw).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            response = self.session.put(
                self._url(),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GitHubJournalError(f"GitHub write failed: {exc.__class__.__name__}") from None
        if response.status_code not in {200, 201}:
            raise GitHubJournalError(self._safe_error(response))
        try:
            result = response.json()
            content = result.get("content") or {}
            return str(content.get("sha") or "")
        except ValueError:
            return ""
