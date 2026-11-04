"""Synthetic GitHub responses only: no credentials, account or network required."""

import base64
import hashlib
import json

import pytest

from brain_settings.errors import AppError
from brain_settings.github_client import GitHubClient
from brain_settings.github_models import GitHubRepositoriesRequest, normalize_repository


def repository(name="owner/project"):
    return {"id": 123, "full_name": name, "description": "Synthetic project", "private": True,
            "default_branch": "main", "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-11-01T00:00:00Z", "temp_clone_token": "must-never-return"}


def issue(number=1, pull=False):
    return {"number": number, "title": "Synthetic decision", "body": "Use SQLite",
            "state": "open", "labels": [{"name": "memory"}],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z",
            **({"pull_request": {}} if pull else {})}


class SyntheticClient(GitHubClient):
    def __init__(self, replies):
        super().__init__(sleep=lambda _: None)
        self.replies = list(replies)
        self.calls = []

    def _send(self, token, path):
        self.calls.append((token, path))
        reply = self.replies.pop(0)
        if len(reply) == 2:
            return reply[0], {}, json.dumps(reply[1]).encode()
        return reply


def test_repository_selection_is_paginated_and_allowlists_metadata():
    client = SyntheticClient([(200, {"Link": '<https://evil.invalid/?page=9>; rel="next"'},
                               json.dumps([repository()]).encode())])
    result = client.list_repositories("synthetic", page=2)
    assert result["next_page"] == 3
    assert result["repositories"][0]["full_name"] == "owner/project"
    assert "must-never-return" not in json.dumps(result)
    assert client.calls[0][1].startswith("/user/repos?")
    assert "page=2" in client.calls[0][1]


@pytest.mark.parametrize("status,code", [(401, "github_auth"), (403, "github_access"),
                                       (404, "github_not_found"), (302, "github_redirect"),
                                       (500, "github_upstream")])
def test_errors_never_return_remote_bodies(status, code):
    client = SyntheticClient([(status, {}, b"synthetic-secret-private-response")])
    with pytest.raises(AppError) as caught:
        client.list_repositories("synthetic")
    assert caught.value.code == code
    assert "synthetic-secret" not in caught.value.message


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limits_fail_fast_and_block_subsequent_requests(status):
    client = SyntheticClient([(status, {"Retry-After": "60"}, b"")])
    for _ in range(2):
        with pytest.raises(AppError) as caught:
            client.list_repositories("synthetic")
        assert caught.value.code == "github_rate_limited"
    assert len(client.calls) == 1


def test_repository_identity_is_verified():
    client = SyntheticClient([(200, repository("other/private"))])
    with pytest.raises(AppError):
        client.retrieve_repository("synthetic", "owner/project")


def test_document_tree_skips_symlinks_submodules_binary_and_code():
    data = {"sha": "a" * 40, "truncated": False, "tree": [
        {"path": "README.md", "type": "blob", "mode": "100644", "sha": "b" * 40, "size": 5},
        {"path": "docs/guide.markdown", "type": "blob", "mode": "100644",
         "sha": "c" * 40, "size": 7},
        {"path": "secret.md", "type": "blob", "mode": "120000", "sha": "d" * 40},
        {"path": "module", "type": "commit", "mode": "160000", "sha": "e" * 40},
        {"path": "src/app.py", "type": "blob", "mode": "100644", "sha": "f" * 40},
    ]}
    branch = {"commit": {"sha": "f" * 40, "commit": {"tree": {"sha": "a" * 40}}}}
    result = SyntheticClient([(200, branch), (200, data)]).list_document_files(
        "synthetic", "owner/project", "main")
    assert result["snapshot_sha"] == "f" * 40
    assert [row["path"] for row in result["files"]] == ["README.md", "docs/guide.markdown"]


def test_truncated_tree_is_never_accepted_as_complete():
    branch = {"commit": {"sha": "f" * 40, "commit": {"tree": {"sha": "a" * 40}}}}
    client = SyntheticClient([(200, branch),
                              (200, {"sha": "a" * 40, "truncated": True, "tree": []})])
    with pytest.raises(AppError) as caught:
        client.list_document_files("synthetic", "owner/project", "main")
    assert caught.value.code == "github_incomplete"


def test_blob_download_is_bounded_decoded_and_verified():
    raw = "# Synthetic\nSQLite решение".encode()
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    value = {"sha": sha, "size": len(raw), "encoding": "base64",
             "content": base64.b64encode(raw).decode()}
    client = SyntheticClient([(200, value)])
    assert client.retrieve_document("synthetic", "owner/project", {"path": "README.md",
                                    "sha": sha, "size": len(raw)}) == raw.decode()


def test_blob_mismatch_is_explicit_failure():
    client = SyntheticClient([(200, {"sha": "a" * 40, "size": 1, "encoding": "base64",
                                    "content": "eA=="})])
    with pytest.raises(AppError):
        client.retrieve_document("synthetic", "owner/project",
                                 {"path": "README.md", "sha": "a" * 40, "size": 1})


def test_issue_and_pull_request_streams_have_separate_permissions_and_citations():
    client = SyntheticClient([(200, [issue(), issue(2, pull=True)]), (200, [issue(2)])])
    issues = client.list_issues("synthetic", "owner/project")
    pulls = client.list_pull_requests("synthetic", "owner/project")
    assert [row["is_pull_request"] for row in issues["items"]] == [False, True]
    assert pulls["items"][0]["is_pull_request"] is True
    assert pulls["items"][0]["url"] == "https://github.com/owner/project/pull/2"
    assert "/issues?" in client.calls[0][1] and "/pulls?" in client.calls[1][1]


@pytest.mark.parametrize("value", ["../private", "a/b/c", "https://github.com/a/b",
                                    "a/b?token=x", "a/..", "a/b\n"])
def test_repository_path_cannot_escape_scope(value):
    with pytest.raises(ValueError):
        normalize_repository(value)


@pytest.mark.parametrize("page", [0, True, "2", 10001])
def test_repository_page_is_strictly_bounded(page):
    with pytest.raises(ValueError):
        GitHubRepositoriesRequest(token="synthetic", page=page)


def test_private_write_or_arbitrary_endpoint_is_rejected_before_network():
    client = SyntheticClient([])
    with pytest.raises(AppError):
        client._request("synthetic", "https://evil.invalid/data")
    assert not client.calls


def test_cancellation_prevents_request():
    client = SyntheticClient([])
    def stop():
        raise AppError("sync_paused", "Paused")
    with pytest.raises(AppError):
        client.retrieve_repository("synthetic", "owner/project", check_stop=stop)
    assert not client.calls


def test_transport_pins_only_github_and_drops_unsafe_error_bodies(monkeypatch):
    from brain_settings import github_client as module

    observations = {}
    class Response:
        status = 200
        def read(self, limit):
            observations["limit"] = limit
            return b"[]"
        def getheader(self, key):
            return "1" if key == "X-RateLimit-Remaining" else None
    class Connection:
        def __init__(self, host, addresses, context, timeout):
            observations.update(host=host, addresses=addresses, timeout=timeout)
        def request(self, method, path, headers):
            observations.update(method=method, path=path, headers=headers)
        def getresponse(self):
            return Response()
        def close(self):
            observations["closed"] = True
    monkeypatch.setattr(module, "validate_public_host", lambda host: (("pinned-public-ip",),))
    monkeypatch.setattr(module, "PinnedHTTPSConnection", Connection)
    status, headers, raw = GitHubClient._send("synthetic", "/user/repos?page=1")
    assert status == 200 and raw == b"[]"
    assert observations["host"] == "api.github.com"
    assert observations["method"] == "GET" and observations["timeout"] <= 20
    assert observations["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
    assert observations["headers"]["Authorization"] == "Bearer synthetic"
    assert observations["limit"] == module.MAX_RESPONSE_BYTES + 1
    assert observations["closed"] and headers["X-RateLimit-Remaining"] == "1"
    Response.status = 403
    observations.pop("limit")
    assert GitHubClient._send("synthetic", "/user/repos")[2] == b""
    assert "limit" not in observations


@pytest.mark.parametrize("failure", ["large", "network"])
def test_transport_large_and_network_failures_are_safe_and_close(monkeypatch, failure):
    from brain_settings import github_client as module

    closed = []
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            if failure == "network":
                raise OSError("sensitive synthetic private connection detail")
        def getresponse(self):
            return self
        status = 200
        def read(self, limit):
            return b"x" * limit
        def close(self):
            closed.append(True)
    monkeypatch.setattr(module, "validate_public_host", lambda _: ())
    monkeypatch.setattr(module, "PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(AppError) as caught:
        GitHubClient._send("synthetic", "/user/repos")
    assert "sensitive" not in caught.value.message and closed == [True]


def test_unclassified_403_has_conservative_secondary_limit_cooldown():
    client = SyntheticClient([(403, {}, b"ignored-private-text")])
    with pytest.raises(AppError) as caught:
        client.list_repositories("synthetic")
    assert caught.value.code == "github_access"
    with pytest.raises(AppError) as caught:
        client.list_repositories("synthetic")
    assert caught.value.code == "github_rate_limited" and len(client.calls) == 1


def test_busy_lock_is_bounded_and_recovers_after_release():
    class BusyLock:
        def acquire(self, timeout):
            assert timeout <= 1
            return False
        def release(self):
            raise AssertionError("Unowned lock must never be released")
    client = SyntheticClient([(200, [])])
    original = client._lock
    client._lock = BusyLock()
    with pytest.raises(AppError) as caught:
        client.list_repositories("synthetic")
    assert caught.value.code == "github_busy" and not client.calls
    client._lock = original
    assert client.list_repositories("synthetic")["repositories"] == []


@pytest.mark.parametrize("body", [b"not-json", b"null", b'"private-string"'])
def test_bad_response_shape_is_not_used_as_source_content(body):
    with pytest.raises(AppError):
        SyntheticClient([(200, {}, body)]).list_repositories("synthetic")


@pytest.mark.parametrize("changes", [{"id": False}, {"description": 42},
                                     {"default_branch": ""}, {"private": "false"},
                                     {"created_at": None}, {"full_name": "../../secret"}])
def test_malformed_repository_metadata_is_not_returned(changes):
    with pytest.raises(AppError):
        SyntheticClient([(200, [{**repository(), **changes}])]).list_repositories("synthetic")


@pytest.mark.parametrize("changes", [{"number": True}, {"body": []}, {"state": "unknown"},
                                     {"labels": {}}, {"title": None}])
def test_malformed_item_page_never_advances_a_partial_result(changes):
    with pytest.raises(AppError):
        SyntheticClient([(200, [{**issue(), **changes}])]).list_issues("synthetic", "owner/project")
