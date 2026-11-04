"""Exact selected GitHub docs/issues/PR scope, bounded passes and durable continuation."""

from urllib.parse import quote

from .dialogue_parsers import chunk_turns, parse_note
from .errors import AppError
from .github_models import normalize_repository
from .github_progress import GitHubProgress
from .memory_text import redact_text

MAX_WORK_UNITS = 100
STOP_CODES = frozenset(("source_deleted", "memory_unavailable", "memory_cancelled", "sync_paused"))
GLOBAL_ERRORS = frozenset(("github_auth", "github_rate_limited", "github_busy", "github_network"))


def _check(source_id, store, check_stop):
    check_stop()
    if source_id in store.deleted_source_ids():
        raise AppError("source_deleted", "Источник удалён; импорт GitHub остановлен.", 404)


def _commit(store, source_id, key, title, text, uri, created, updated, stats):
    title = redact_text(title)
    text = redact_text("# " + title + "\n\n" + text)
    chunks = chunk_turns(parse_note(text))
    documents = [{"source_id": source_id, "source_kind": "github",
                  "source_object_id": key + f"::chunk:{index:06}",
                  "title": title + f" · {index + 1}", "text": chunk.text,
                  "source_uri": uri, "created_at": created, "updated_at": updated}
                 for index, chunk in enumerate(chunks)]
    results = store.replace_file_documents(source_id, key, documents, complete=True)
    return {**stats, "files_processed": stats["files_processed"] + 1,
            "bytes_read": stats["bytes_read"] + len(text.encode()),
            "imported": stats["imported"] + sum(
                item["changed"] and item["revision"] == 1 for item in results),
            "updated": stats["updated"] + sum(
                item["changed"] and item["revision"] > 1 for item in results),
            "unchanged": stats["unchanged"] + sum(not item["changed"] for item in results)}


def _error(stats, repo, error):
    return {**stats, "skipped": stats["skipped"] + 1,
            "files_skipped": stats["files_skipped"] + 1,
            "errors": [*stats["errors"], repo + ": " + redact_text(error.message)][:100]}


def _documents(source_id, repo, metadata, store, client, token, progress, budget, check, stats):
    check()
    listing = client.list_document_files(token, repo, metadata["default_branch"], check_stop=check)
    state = progress.get(repo, "docs")
    after = state.get("after", "")
    if not isinstance(after, str) or len(after) > 4096:
        raise AppError("github_progress", "Повреждено состояние синхронизации GitHub.", 503)
    files = [row for row in listing["files"] if row["path"] > after]
    selected = files[:budget]
    for file in selected:
        check()
        try:
            text = client.retrieve_document(token, repo, file, check_stop=check)
            check()
            key = repo + "::doc:" + file["path"]
            # Immutable snapshot citations still resolve after the default branch moves.
            uri = "https://github.com/" + repo + "/blob/" + listing["snapshot_sha"] + "/" + quote(
                file["path"], safe="/")
            stats = _commit(store, source_id, key, repo + " · " + file["path"], text, uri,
                            metadata["created_at"], metadata["updated_at"], stats)
        except AppError as error:
            if error.code in STOP_CODES | GLOBAL_ERRORS:
                raise
            stats = _error(stats, repo + "/" + file["path"], error)
        # Failed individual files are visible errors and retry on the next complete cycle.
        progress.save(repo, "docs", {"after": file["path"]})
    more = len(files) > len(selected)
    if not more:
        progress.save(repo, "docs", {"after": ""})
    return stats, len(selected), more, len(files) - len(selected)


def _items(source_id, repo, scope, store, client, token, progress, budget, check, stats):
    state = progress.get(repo, scope)
    page, offset = state.get("page", 1), state.get("offset", 0)
    if (isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10000
            or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < 100):
        raise AppError("github_progress", "Повреждено состояние синхронизации GitHub.", 503)
    method = client.list_pull_requests if scope == "pull_requests" else client.list_issues
    check()
    response = method(token, repo, page=page, check_stop=check)
    rows = response["items"]
    selected = rows[offset:offset + budget]
    for item in selected:
        check()
        if scope == "issues" and item["is_pull_request"]:
            continue
        key = repo + ("::pull:" if scope == "pull_requests" else "::issue:") + str(item["number"])
        title = repo + (" PR #" if scope == "pull_requests" else " Issue #") + str(
            item["number"]) + " · " + item["title"]
        text = "State: " + item["state"] + "\nLabels: " + ", ".join(item["labels"]) + \
            "\n\n" + item["body"]
        stats = _commit(store, source_id, key, title, text, item["url"],
                        item["created_at"], item["updated_at"], stats)
    next_offset = offset + len(selected)
    more = next_offset < len(rows) or response["next_page"] is not None
    if next_offset < len(rows):
        next_state = {"page": page, "offset": next_offset}
    else:
        next_state = {"page": response["next_page"] or 1, "offset": 0}
    progress.save(repo, scope, next_state)
    return stats, len(selected), more, int(more)


def import_github(source: dict, store, client, token: str, check_stop=lambda: None) -> dict:
    source_id = source["id"]
    selected = source.get("github_repositories", ())
    content = source.get("github_content", ("docs", "issues", "pull_requests"))
    try:
        if (not isinstance(selected, (list, tuple)) or not 1 <= len(selected) <= 20
                or not isinstance(content, (list, tuple)) or not 1 <= len(content) <= 3
                or any(scope not in ("docs", "issues", "pull_requests") for scope in content)):
            raise ValueError
        repositories = tuple(dict.fromkeys(normalize_repository(value) for value in selected))
        scopes = tuple(dict.fromkeys(content))
    except (ValueError, TypeError) as exc:
        raise AppError("github_scope", "Выберите репозитории GitHub и виды материалов.") from exc
    def check():
        _check(source_id, store, check_stop)
    check()
    progress = GitHubProgress(store, source_id)
    tasks = [(repo, scope) for repo in repositories for scope in scopes]
    position = progress.get("", "cursor").get("position", 0)
    if (isinstance(position, bool) or not isinstance(position, int)
            or not 0 <= position < len(tasks)):
        position = 0
    ordered = tasks[position:] + tasks[:position]
    stats = {"imported": 0, "updated": 0, "unchanged": 0, "skipped": 0,
             "errors": [], "warnings": [], "source_id": source_id, "files_processed": 0,
             "files_unchanged": 0, "files_skipped": 0, "files_deferred": 0, "bytes_read": 0,
             "processed_paths": [], "deferred_paths": [], "has_more": False}
    budget, metadata_cache = MAX_WORK_UNITS, {}
    for index, (repo, scope) in enumerate(ordered):
        check()
        if budget <= 0:
            stats = {**stats, "has_more": True,
                     "files_deferred": stats["files_deferred"] + len(ordered) - index}
            break
        try:
            if repo not in metadata_cache:
                metadata_cache = {**metadata_cache, repo: client.retrieve_repository(
                    token, repo, check_stop=check)}
            if scope == "docs":
                stats, used, more, deferred = _documents(
                    source_id, repo, metadata_cache[repo], store, client, token, progress,
                    budget, check, stats)
            else:
                stats, used, more, deferred = _items(
                    source_id, repo, scope, store, client, token, progress, budget, check, stats)
            budget -= used
            stats = {**stats, "has_more": stats["has_more"] or more,
                     "files_deferred": stats["files_deferred"] + deferred}
        except AppError as error:
            if error.code in STOP_CODES:
                raise
            stats = _error(stats, repo, error)
            if error.code in GLOBAL_ERRORS:
                stats = {**stats, "has_more": True,
                         "files_deferred": stats["files_deferred"] + len(ordered) - index}
                break
        # Round robin avoids a large docs/issue archive starving the other chosen scopes.
        progress.save("", "cursor", {"position": (position + index + 1) % len(tasks)})
    if stats["has_more"]:
        stats = {**stats, "warnings": ["Часть GitHub отложена до следующего прохода; "
                                      "продолжение сохранено в SQLite."]}
    return stats
