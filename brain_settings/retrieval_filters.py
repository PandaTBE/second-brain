"""Validated SQL filters shared by lexical, vector and evidence retrieval."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .errors import AppError

DATE_BASIS = "message_at, then source_modified_at, then source_created_at; undated excluded"


def _boundary(value: str | None, *, upper: bool = False) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise AppError("invalid_date_filter", "Укажите дату ISO 8601.")
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            # Date-only upper bounds include the whole named day, using an exclusive next day.
            return datetime.combine(day, datetime.min.time(), UTC) + (
                timedelta(days=1) if upper else timedelta()
            )
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, OverflowError):
        raise AppError("invalid_date_filter", "Укажите дату ISO 8601 с часовым поясом.") from None


@dataclass(frozen=True)
class SearchFilters:
    project: str | None = None
    date_from: str | None = None
    date_to: str | None = None

    def __post_init__(self) -> None:
        if self.project is not None and (
            not isinstance(self.project, str) or not self.project.strip() or len(self.project) > 256
        ):
            raise AppError("invalid_project_filter", "Укажите проект длиной до 256 символов.")
        if self.project is not None:
            try:
                self.project.encode("utf-8")
            except UnicodeError:
                raise AppError(
                    "invalid_project_filter", "Проект должен содержать корректный Unicode."
                ) from None
        lower, upper = _boundary(self.date_from), _boundary(self.date_to, upper=True)
        if lower and upper and (lower > upper or (lower == upper and len(self.date_to) == 10)):
            raise AppError("invalid_date_filter", "Начало периода должно предшествовать концу.")

    def clause(self, alias: str = "d") -> tuple[str, tuple]:
        if alias not in ("d", "documents"):
            raise ValueError("Unsupported document alias")
        sql, arguments = "", ()
        if self.project is not None:
            sql += f" AND {alias}.project=?"
            arguments += (self.project,)
        expression = (
            f"julianday(COALESCE({alias}.message_at,{alias}.source_modified_at,"
            f"{alias}.source_created_at))"
        )
        lower, upper = _boundary(self.date_from), _boundary(self.date_to, upper=True)
        if lower:
            sql += f" AND {expression}>=julianday(?)"
            arguments += (lower.isoformat(),)
        if upper:
            operation = "<" if len(self.date_to) == 10 else "<="
            sql += f" AND {expression}{operation}julianday(?)"
            arguments += (upper.isoformat(),)
        return sql, arguments

    def matches(self, record: dict) -> bool:
        if self.project is not None and record.get("project") != self.project:
            return False
        if self.date_from is None and self.date_to is None:
            return True
        value = next(
            (
                record.get(key)
                for key in ("message_at", "source_modified_at", "source_created_at")
                if record.get(key)
            ),
            None,
        )
        if value is None:
            return False
        try:
            current = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        if current.tzinfo is None:
            return False
        lower, upper = _boundary(self.date_from), _boundary(self.date_to, upper=True)
        return not (
            (lower and current < lower)
            or (upper and (current >= upper if len(self.date_to) == 10 else current > upper))
        )

    def public(self) -> dict:
        return {
            "project": self.project,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "date_basis": DATE_BASIS,
        }
