"""Validate card provenance against the stored, already redacted document."""

from .errors import AppError
from .memory_text import redact_text


def validate_card(card, record) -> dict:
    if not isinstance(card, dict):
        raise AppError("invalid_card", "Модель вернула неподдерживаемую карточку.")
    title, summary = card.get("title", ""), card.get("summary", "")
    if not isinstance(title, str) or not isinstance(summary, str):
        raise AppError("invalid_card", "В карточке отсутствуют текстовые поля.")
    result = {"title": redact_text(title)[:500], "summary": redact_text(summary)[:4000]}
    for key in ("keywords", "questions"):
        values = card.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise AppError("invalid_card", "Поисковые поля карточки должны содержать текст.")
        result = {**result, key: [redact_text(value)[:500] for value in values[:30]]}
    atoms = card.get("atoms", [])
    if not isinstance(atoms, list) or len(atoms) > 50:
        raise AppError("invalid_card", "Карточка содержит слишком много утверждений.")
    clean_atoms = []
    for atom in atoms:
        if not isinstance(atom, dict) or not all(
            isinstance(atom.get(key), str) for key in ("text", "kind", "evidence")
        ):
            raise AppError("invalid_card", "У утверждения отсутствует подтверждение.")
        if atom["kind"] not in ("fact", "decision", "error", "preference", "task"):
            raise AppError("invalid_card", "Неизвестный тип утверждения в карточке.")
        evidence = redact_text(atom["evidence"])
        if not evidence.strip() or evidence not in record["text"]:
            raise AppError("invalid_evidence", "Подтверждение не найдено в исходной записи.")
        clean_atoms = [
            *clean_atoms,
            {
                "text": redact_text(atom["text"])[:2000],
                "kind": redact_text(atom["kind"])[:40],
                "evidence": evidence[:4000],
                "record_id": record["id"],
                "revision": record["revision"],
                "source_uri": record["source_uri"],
            },
        ]
    return {**result, "atoms": clean_atoms}
