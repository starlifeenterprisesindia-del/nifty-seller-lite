from __future__ import annotations

import base64
import json

from services.github_journal import GitHubJsonJournal


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.get_response = FakeResponse(404, {"message": "Not Found"})
        self.put_response = FakeResponse(
            201, {"content": {"sha": "new-sha"}, "commit": {"sha": "commit"}}
        )
        self.last_get = None
        self.last_put = None

    def get(self, url, **kwargs):
        self.last_get = (url, kwargs)
        return self.get_response

    def put(self, url, **kwargs):
        self.last_put = (url, kwargs)
        return self.put_response


def test_github_journal_reads_missing_file_as_empty():
    session = FakeSession()
    journal = GitHubJsonJournal(
        owner="owner", repo="repo", token="secret", session=session
    )
    result = journal.read()
    assert result.exists is False
    assert result.sha is None
    assert result.data["entries"] == []


def test_github_journal_decodes_and_updates_json():
    session = FakeSession()
    data = {"schema_version": 1, "entries": [{"date": "2026-08-01"}]}
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    session.get_response = FakeResponse(
        200, {"content": encoded, "encoding": "base64", "sha": "old-sha"}
    )
    journal = GitHubJsonJournal(
        owner="owner", repo="repo", token="secret", path="data/journal.json", session=session
    )
    result = journal.read()
    assert result.exists is True
    assert result.sha == "old-sha"
    assert result.data == data

    new_sha = journal.write(data, sha=result.sha)
    assert new_sha == "new-sha"
    payload = session.last_put[1]["json"]
    assert payload["sha"] == "old-sha"
    assert json.loads(base64.b64decode(payload["content"])) == data
