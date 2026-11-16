"""Task boards use synthetic temporary databases, never memory documents."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from brain_settings.memory_store import MemoryStore

DAY = "2026-11-16"
STATS = ("strength", "agility", "intelligence", "vitality", "perception")


@pytest.fixture(autouse=True)
def fixed_task_clock(monkeypatch):
    from brain_settings import tasks

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 16, 12, tzinfo=UTC)

    monkeypatch.setattr(tasks, "datetime", Clock)


def create(harness, **changes):
    response = harness.post("/api/tasks", {
        "day": DAY, "title": "Synthetic task", "kind": "todo", "stat": "strength",
        "difficulty": 1, **changes,
    })
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    return response.json()["data"]


def move(harness, identifier, status, day=DAY):
    response = harness.post(f"/api/tasks/{identifier}/status", {"day": day, "status": status})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def board(harness, day=DAY):
    response = harness.client.get("/api/tasks", params={"day": day})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_empty_board_contract(harness):
    assert board(harness) == {"day": DAY, "tasks": [], "player": {
        "xp": 0, "level": 1, "rank": "E", "level_xp": 0, "next_level_xp": 50,
        "stats": dict.fromkeys(STATS, 0),
    }}


@pytest.mark.parametrize(("difficulty", "xp"), [(1, 10), (2, 25), (3, 50)])
def test_reward_undo_redo_and_delete_preserve_earned_xp(harness, difficulty, xp):
    task = create(harness, title="  Synthetic title  ", difficulty=difficulty)["tasks"][0]
    assert task == {"id": task["id"], "title": "Synthetic title", "kind": "todo",
                    "stat": "strength", "difficulty": difficulty, "status": "todo", "xp": xp}
    for status, expected in (("done", xp), ("done", xp), ("doing", 0), ("done", xp),
                             ("todo", 0), ("done", xp)):
        result = move(harness, task["id"], status)
        assert result["tasks"][0]["status"] == status
        assert result["player"]["xp"] == expected
        assert result["player"]["stats"]["strength"] == expected // 10
    response = harness.client.request("DELETE", f"/api/tasks/{task['id']}",
                                      json={"day": DAY}, headers=harness.headers)
    assert response.status_code == 200
    assert response.json()["data"]["tasks"] == []
    assert response.json()["data"]["player"]["xp"] == xp
    assert harness.post(f"/api/tasks/{task['id']}/status",
                        {"day": DAY, "status": "done"}).status_code == 404
    assert harness.client.request("DELETE", f"/api/tasks/{task['id']}", json={"day": DAY},
                                  headers=harness.headers).status_code == 404


def test_daily_rollover_todo_period_and_restart(harness):
    daily = create(harness, kind="daily", stat="vitality", difficulty=2)["tasks"][0]
    todo = next(task for task in create(harness)["tasks"] if task["kind"] == "todo")
    move(harness, daily["id"], "done")
    move(harness, todo["id"], "done")
    tomorrow = "2026-11-17"
    result = board(harness, tomorrow)
    assert {task["kind"]: task["status"] for task in result["tasks"]} == {
        "daily": "todo", "todo": "done",
    }
    assert result["player"]["xp"] == 35
    assert move(harness, daily["id"], "done", tomorrow)["player"]["xp"] == 60
    MemoryStore(harness.data_dir)
    assert board(harness, tomorrow)["player"]["xp"] == 60
    assert move(harness, daily["id"], "todo", tomorrow)["player"]["xp"] == 35
    assert next(task for task in board(harness)["tasks"] if task["kind"] == "daily")[
        "status"
    ] == "done"
    with MemoryStore(harness.data_dir)._connection() as connection:
        assert connection.execute("SELECT count(*) FROM tracker_progress").fetchone()[0] == 2


def test_concurrent_done_has_one_reward_and_no_memory_side_effect(harness):
    from brain_settings.mcp_server import MemoryTools

    store = harness.client.app.state.memory_service.store
    with store._connection() as connection:
        before = connection.execute("SELECT generation FROM memory_index_generation").fetchone()[0]
    task = create(harness)["tasks"][0]
    headers = harness.headers

    def complete(_):
        return harness.client.post(f"/api/tasks/{task['id']}/status",
                                   json={"day": DAY, "status": "done"}, headers=headers)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(complete, range(16)))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["data"]["player"]["xp"] == 10 for response in responses)
    assert store.search("Synthetic") == []
    assert MemoryTools(store, harness.data_dir).search_memory("Synthetic")["results"] == []
    with store._connection() as connection:
        generation = connection.execute(
            "SELECT generation FROM memory_index_generation"
        ).fetchone()[0]
        assert generation == before
        assert connection.execute("SELECT count(*) FROM tracker_progress").fetchone()[0] == 1


@pytest.mark.parametrize(("total", "level", "rank"), [
    (0, 1, "E"), (49, 1, "E"), (50, 2, "E"), (199, 2, "E"), (200, 3, "E"),
    (1250, 6, "D"), (5000, 11, "C"), (11250, 16, "B"), (20000, 21, "A"),
    (31250, 26, "S"), (80000, 41, "S"),
])
def test_player_boundaries(harness, total, level, rank):
    task = create(harness)["tasks"][0]
    with harness.client.app.state.memory_service.store._connection(write=True) as connection:
        connection.execute("INSERT INTO tracker_progress VALUES (?, '', 'done', ?, ?)",
                           (task["id"], total, DAY))
    player = board(harness)["player"]
    assert player["xp"] == total
    assert player["level"] == level and player["rank"] == rank
    assert player["level_xp"] == 50 * (level - 1) ** 2
    assert player["next_level_xp"] == 50 * level ** 2


@pytest.mark.parametrize("stat", STATS)
def test_all_stats(harness, stat):
    task = create(harness, stat=stat, difficulty=3)["tasks"][0]
    assert move(harness, task["id"], "done")["player"]["stats"] == {
        name: 5 if name == stat else 0 for name in STATS
    }


@pytest.mark.parametrize("day", ["20261116", "2026-2-03", "2026-02-30", "2026-11-14",
                                  "2026-11-18", "2026-11-16T00:00:00", "x" * 100])
def test_invalid_day_on_every_endpoint(harness, day):
    task = create(harness)["tasks"][0]
    responses = [
        harness.client.get("/api/tasks", params={"day": day}),
        harness.post("/api/tasks", {"day": day, "title": "X", "kind": "todo",
                                    "stat": "strength", "difficulty": 1}),
        harness.post(f"/api/tasks/{task['id']}/status", {"day": day, "status": "done"}),
        harness.client.request("DELETE", f"/api/tasks/{task['id']}", json={"day": day},
                               headers=harness.headers),
    ]
    for response in responses:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "task_day_invalid"
    assert board(harness)["tasks"][0]["status"] == "todo"


def test_day_window_is_inclusive(harness):
    for offset in (-1, 0, 1):
        day = (datetime.fromisoformat(DAY) + timedelta(days=offset)).date().isoformat()
        assert board(harness, day)["day"] == day


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"title": "x" * 201}, {"title": "\x00"}, {"kind": "weekly"},
    {"stat": "luck"},
    {"difficulty": 0}, {"difficulty": 4}, {"difficulty": True}, {"difficulty": "1"},
    {"difficulty": 1.0}, {"extra": "bad"}, {"day": None}, {"title": 123},
])
def test_create_validation(harness, changes):
    response = harness.post("/api/tasks", {"day": DAY, "title": "Synthetic", "kind": "todo",
                                           "stat": "strength", "difficulty": 1, **changes})
    assert response.status_code == 422
    assert board(harness)["tasks"] == []


def test_missing_fields_unknown_ids_and_csrf(harness):
    assert harness.client.get("/api/tasks").status_code == 422
    assert harness.post("/api/tasks", {}).status_code == 422
    for identifier in ("unknown", "x" * 201):
        response = harness.post(f"/api/tasks/{identifier}/status", {"day": DAY, "status": "done"})
        assert response.status_code in (404, 422)
        response = harness.client.request("DELETE", f"/api/tasks/{identifier}",
                                           json={"day": DAY}, headers=harness.headers)
        assert response.status_code in (404, 422)
    task = create(harness)["tasks"][0]
    for payload in ({"status": "done"}, {"day": DAY, "status": "bad"},
                    {"day": DAY, "status": "done", "extra": 1}):
        assert harness.post(f"/api/tasks/{task['id']}/status", payload).status_code == 422
    assert harness.client.request("DELETE", f"/api/tasks/{task['id']}", json={},
                                  headers=harness.headers).status_code == 422
    assert harness.client.request("DELETE", f"/api/tasks/{task['id']}",
                                  json={"day": DAY, "extra": 1},
                                  headers=harness.headers).status_code == 422
    assert harness.client.post("/api/tasks", json={"day": DAY}).status_code == 403
    assert harness.client.post(f"/api/tasks/{task['id']}/status",
                               json={"day": DAY, "status": "done"}).status_code == 403
    assert harness.client.request("DELETE", f"/api/tasks/{task['id']}",
                                  json={"day": DAY}).status_code == 403


def test_limit_is_atomic_and_deleted_slot_is_reusable(harness):
    store = harness.client.app.state.memory_service.store
    with store._connection(write=True) as connection:
        connection.executemany("INSERT INTO tracker_tasks VALUES (?, 'todo', 'Synthetic', "
                               "'strength', 1, ?, NULL)", [(str(i), DAY) for i in range(499)])
    headers = harness.headers

    def add(_):
        return harness.client.post("/api/tasks", json={"day": DAY, "title": "Synthetic",
                                   "kind": "todo", "stat": "strength", "difficulty": 1},
                                   headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add, range(2)))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["error"]["code"] == "tasks_limit"
    assert harness.client.request("DELETE", "/api/tasks/0", json={"day": DAY},
                                  headers=headers).status_code == 200
    assert len(create(harness)["tasks"]) == 500


def test_restarted_router_reads_persisted_tasks_and_xp(harness):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from brain_settings.tasks import create_task_router

    task = create(harness, difficulty=3)["tasks"][0]
    expected = move(harness, task["id"], "done")
    application = FastAPI()
    application.include_router(create_task_router(MemoryStore(harness.data_dir)))
    with TestClient(application) as client:
        assert client.get("/api/tasks", params={"day": DAY}).json()["data"] == expected


def test_task_endpoints_honor_restore_exclusion(harness):
    from brain_settings.maintenance import exclusive

    task = create(harness)["tasks"][0]
    headers = harness.headers
    with exclusive(harness.data_dir):
        responses = [
            harness.client.get("/api/tasks", params={"day": DAY}),
            harness.client.post("/api/tasks", json={"day": DAY, "title": "Synthetic",
                                "kind": "todo", "stat": "strength", "difficulty": 1},
                                headers=headers),
            harness.client.post(f"/api/tasks/{task['id']}/status",
                                json={"day": DAY, "status": "done"}, headers=headers),
            harness.client.request("DELETE", f"/api/tasks/{task['id']}",
                                   json={"day": DAY}, headers=headers),
        ]
    for response in responses:
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "memory_maintenance"
    assert board(harness)["tasks"][0]["status"] == "todo"
