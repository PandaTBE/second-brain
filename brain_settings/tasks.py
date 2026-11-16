"""Local task board and derived XP ledger, separate from searchable memory."""

import math
import re
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator

from .errors import AppError

STATS = ("strength", "agility", "intelligence", "vitality", "perception")
XP = {1: 10, 2: 25, 3: 50}
SCHEMA = """
CREATE TABLE IF NOT EXISTS tracker_tasks (
 id TEXT PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('todo','daily')),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 stat TEXT NOT NULL CHECK(stat IN ('strength','agility','intelligence','vitality','perception')),
 difficulty INTEGER NOT NULL CHECK(difficulty IN (1,2,3)),
 created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS tracker_progress (
 task_id TEXT NOT NULL REFERENCES tracker_tasks(id),
 period TEXT NOT NULL CHECK(period='' OR period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
 status TEXT NOT NULL CHECK(status IN ('doing','done')),
 xp INTEGER NOT NULL CHECK(xp>=0), updated_at TEXT NOT NULL,
 PRIMARY KEY(task_id,period)
);
"""


def migrate(connection):
    connection.executescript(SCHEMA)


def validate_day(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Invalid day format")
        if abs((date.fromisoformat(value) - datetime.now(UTC).date()).days) > 1:
            raise ValueError("Day outside local timezone window")
    except ValueError as error:
        raise AppError("task_day_invalid", "Проверьте дату и время на Mac.", 422) from error
    return value


class DayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    day: str

    @field_validator("day", mode="before")
    @classmethod
    def local_day(cls, value):
        return validate_day(value)


class TaskCreate(DayRequest):
    title: Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                            min_length=1, max_length=200, pattern=r"^[^\x00]+$")]
    kind: Literal["todo", "daily"]
    stat: Literal["strength", "agility", "intelligence", "vitality", "perception"]
    difficulty: StrictInt = Field(ge=1, le=3)


class StatusRequest(DayRequest):
    status: Literal["todo", "doing", "done"]


def _board(connection, day):
    rows = connection.execute(
        "SELECT t.id,t.title,t.kind,t.stat,t.difficulty,coalesce(p.status,'todo') AS status "
        "FROM tracker_tasks t LEFT JOIN tracker_progress p ON p.task_id=t.id "
        "AND p.period=CASE WHEN t.kind='todo' THEN '' ELSE ? END "
        "WHERE t.deleted_at IS NULL ORDER BY t.created_at,t.id", (day,),
    ).fetchall()
    tasks = [{**dict(row), "xp": XP[row["difficulty"]]} for row in rows]
    stat_xp = dict.fromkeys(STATS, 0)
    earned = connection.execute(
        "SELECT t.stat,sum(p.xp) AS xp FROM tracker_progress p "
        "JOIN tracker_tasks t ON t.id=p.task_id WHERE p.status='done' GROUP BY t.stat"
    ).fetchall()
    stat_xp = {**stat_xp, **{row["stat"]: row["xp"] for row in earned}}
    total = sum(stat_xp.values())
    level = 1 + math.isqrt(total // 50)
    return {"success": True, "data": {
        "day": day, "tasks": tasks, "player": {
            "xp": total, "level": level, "rank": "EDCBAS"[min(5, (level - 1) // 5)],
            "level_xp": 50 * (level - 1) ** 2, "next_level_xp": 50 * level ** 2,
            "stats": {stat: xp // 10 for stat, xp in stat_xp.items()},
        },
    }}


def _active_task(connection, identifier):
    task = connection.execute(
        "SELECT kind,difficulty FROM tracker_tasks WHERE id=? AND deleted_at IS NULL",
        (identifier,),
    ).fetchone()
    if task is None:
        raise AppError("task_not_found", "Задача не найдена.", 404)
    return task


def create_task_router(store):
    router = APIRouter(prefix="/api/tasks")

    @router.get("")
    def board(day: str):
        validate_day(day)
        with store._connection() as connection:
            connection.execute("BEGIN")
            return _board(connection, day)

    @router.post("")
    def create(request: TaskCreate):
        with store._connection(write=True) as connection:
            count = connection.execute(
                "SELECT count(*) FROM tracker_tasks WHERE deleted_at IS NULL"
            ).fetchone()[0]
            if count >= 500:
                raise AppError("tasks_limit", "Можно создать не более 500 активных задач.", 409)
            connection.execute(
                "INSERT INTO tracker_tasks(id,kind,title,stat,difficulty,created_at) "
                "VALUES (?,?,?,?,?,?)", (str(uuid4()), request.kind, request.title, request.stat,
                                        request.difficulty, datetime.now(UTC).isoformat()),
            )
            return _board(connection, request.day)

    @router.post("/{task_id}/status")
    def status(task_id: Annotated[str, Path(max_length=200)], request: StatusRequest):
        with store._connection(write=True) as connection:
            task = _active_task(connection, task_id)
            period = "" if task["kind"] == "todo" else request.day
            if request.status == "todo":
                connection.execute("DELETE FROM tracker_progress WHERE task_id=? AND period=?",
                                   (task_id, period))
            else:
                connection.execute(
                    "INSERT INTO tracker_progress(task_id,period,status,xp,updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(task_id,period) DO UPDATE SET "
                    "status=excluded.status,xp=excluded.xp,updated_at=excluded.updated_at",
                    (task_id, period, request.status, XP[task["difficulty"]],
                     datetime.now(UTC).isoformat()),
                )
            return _board(connection, request.day)

    @router.delete("/{task_id}")
    def delete(task_id: Annotated[str, Path(max_length=200)], request: DayRequest):
        with store._connection(write=True) as connection:
            _active_task(connection, task_id)
            connection.execute("UPDATE tracker_tasks SET deleted_at=? WHERE id=?",
                               (datetime.now(UTC).isoformat(), task_id))
            return _board(connection, request.day)

    return router
