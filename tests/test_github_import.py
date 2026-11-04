"""Bounded GitHub archive reconciliation against synthetic responses and temporary SQLite."""

from uuid import uuid4

import pytest

from brain_settings.errors import AppError
from brain_settings.github_import import import_github
from brain_settings.memory_store import MemoryStore


class FakeGitHub:
    def __init__(self, documents=2, issues=2, pulls=1):
        self.documents, self.issues, self.pulls = documents, issues, pulls
        self.calls = []
        self.text = "Synthetic SQLite decision"
        self.error = None

    def retrieve_repository(self, token, full_name, check_stop=lambda: None):
        check_stop()
        self.calls.append(("repository", full_name))
        return {"id": 12, "full_name": full_name, "description": "Synthetic", "private": True,
                "default_branch": "main", "url": "https://github.com/" + full_name,
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z"}

    def list_document_files(self, token, repo, branch, check_stop=lambda: None):
        check_stop()
        return {"snapshot_sha": "a" * 40, "files": [
            {"path": f"docs/{index:05}.md", "sha": "b" * 40, "size": 10}
            for index in range(self.documents)]}

    def retrieve_document(self, token, repo, file, check_stop=lambda: None):
        check_stop()
        self.calls.append(("document", repo, file["path"]))
        if self.error:
            raise self.error
        return self.text

    def _items(self, repo, page, count, pull=False):
        rows = [{"number": index + 1, "title": f"Synthetic item {index}",
                 "body": self.text, "state": "closed", "labels": ["decision"],
                 "is_pull_request": pull, "url": "https://github.com/" + repo +
                 ("/pull/" if pull else "/issues/") + str(index + 1),
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z"}
                for index in range((page - 1) * 100, min(page * 100, count))]
        return {"items": rows, "next_page": page + 1 if page * 100 < count else None}

    def list_issues(self, token, repo, page=1, check_stop=lambda: None):
        check_stop()
        self.calls.append(("issues", repo, page))
        return self._items(repo, page, self.issues)

    def list_pull_requests(self, token, repo, page=1, check_stop=lambda: None):
        check_stop()
        self.calls.append(("pulls", repo, page))
        return self._items(repo, page, self.pulls, pull=True)


@pytest.fixture
def setup(tmp_path):
    return {"id": str(uuid4()), "kind": "github", "github_repositories": ["owner/project"],
            "github_content": ["docs", "issues", "pull_requests"]}, MemoryStore(tmp_path)


def test_selected_scopes_import_share_search_and_update_without_duplicates(setup):
    source, store = setup
    client = FakeGitHub()
    first = import_github(source, store, client, "synthetic")
    assert first["imported"] == 5 and first["has_more"] is False
    assert len(store.search("SQLite")) == 5
    assert import_github(source, store, client, "synthetic")["unchanged"] == 5
    client.text += " Updated."
    assert import_github(source, store, client, "synthetic")["updated"] == 5


def test_only_explicit_repositories_and_scopes_are_read(setup):
    source, store = setup
    client = FakeGitHub()
    result = import_github({**source, "github_content": ["pull_requests"]}, store,
                           client, "synthetic")
    assert result["imported"] == 1
    assert all(call[0] in ("repository", "pulls") for call in client.calls)


def test_large_archive_resumes_durably_and_roundrobin_does_not_starve_scopes(setup):
    source, store = setup
    client = FakeGitHub(documents=125, issues=215, pulls=1)
    result = import_github(source, store, client, "synthetic")
    assert result["has_more"] is True and result["files_deferred"] > 0
    assert result["files_processed"] <= 100
    for _ in range(9):
        # Reconstruct the store to prove pagination is not process-local.
        store = MemoryStore(store.path.parent)
        import_github(source, store, client, "synthetic")
    assert store.status()["documents"] == 341
    assert any(call[0] == "pulls" for call in client.calls)
    assert any(call[0] == "document" and call[2] == "docs/00124.md" for call in client.calls)
    assert any(call[0] == "issues" and call[2] == 3 for call in client.calls)


def test_error_never_replaces_previous_complete_file(setup):
    source, store = setup
    source = {**source, "github_content": ["docs"]}
    client = FakeGitHub(documents=1)
    import_github(source, store, client, "synthetic")
    client.error = AppError("github_incomplete", "Incomplete document")
    result = import_github(source, store, client, "synthetic")
    assert result["errors"] and store.search("SQLite")


@pytest.mark.parametrize("code", ["sync_paused", "source_deleted", "memory_cancelled"])
def test_stop_and_disconnection_are_not_swallowed(setup, code):
    source, store = setup
    client = FakeGitHub()
    def stop():
        raise AppError(code, "Stop synthetic")
    with pytest.raises(AppError) as caught:
        import_github(source, store, client, "synthetic", check_stop=stop)
    assert caught.value.code == code and client.calls == []


def test_deleted_source_never_reads_remote_or_recreates_progress(setup):
    source, store = setup
    store.delete_source(source["id"])
    client = FakeGitHub()
    with pytest.raises(AppError):
        import_github(source, store, client, "synthetic")
    assert not client.calls


def test_secrets_are_redacted_before_chunking(setup):
    source, store = setup
    client = FakeGitHub(documents=1, issues=0, pulls=0)
    secret = "github_pat_" + "SYNTHETIC" * 8
    client.text = "a" * 11990 + "\n" + secret + "\n" + "z" * 12000
    import_github(source, store, client, "synthetic")
    for record in store.browse(limit=50)["results"]:
        assert secret not in store.get_record(record["id"])["text"]


def test_unicode_checkpoint_round_trip_stays_readable(setup):
    from brain_settings.github_progress import GitHubProgress
    source, store = setup
    progress = GitHubProgress(store, source["id"])
    path = "docs/" + "📚" * 4000 + ".md"
    progress.save("owner/project", "docs", {"after": path})
    assert progress.get("owner/project", "docs") == {"after": path}
