"""Conservative source-proven relations; source text is data, never instructions."""

import hashlib
import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import AppError


class FactRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["possible_conflict", "explicit_supersedes"]
    target_kind: Literal["fact", "decision"] = "decision"
    target_text: str = Field(min_length=1, max_length=1500)
    evidence: str = Field(min_length=1, max_length=4000)


def canonical_claim(text: str) -> str:
    return unicodedata.normalize("NFC", text.strip())


def identity(*values) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def fact_id(kind: str, text: str) -> str:
    return "fact_" + identity(kind, canonical_claim(text))


def _standalone_assertion(quote: str, source: str) -> bool:
    start = source.find(quote)
    if start < 0:
        return False
    before, after = source[:start], source[start + len(quote):]
    if before.rsplit("\n", 1)[-1].strip() or after.split("\n", 1)[0].strip():
        return False
    if before.count("```") % 2 or before.count("~~~") % 2:
        return False
    framing = "\n".join((before.rstrip().rsplit("\n", 1)[-1],
                          after.lstrip().split("\n", 1)[0])).casefold()
    uncertain = (
        r"\b(?:false|not|reject\w*|proposal|if|hypothet\w*|example|quot\w*|suppose|maybe|"
        r"лож\w*|невер\w*|предлож\w*|если|цитат\w*|отклон\w*|возможно|пример)\b"
    )
    return re.search(uncertain, framing) is None


def support_status(claim: str, quote: str, source: str) -> str:
    # Canonical comparisons do not change saved quote text or citation offsets.
    claim, quote, source = (unicodedata.normalize("NFC", value)
                            for value in (claim, quote, source))
    if not claim.strip() or claim.strip() not in quote:
        return "candidate"
    start = source.find(quote) + quote.find(claim.strip())
    prefix = source[:start]
    frame = prefix.rsplit("\n", 1)[-1] or prefix.rstrip().rsplit("\n", 1)[-1]
    uncertain = (
        r"\b(?:false|not|reject\w*|proposal|if|hypothet\w*|example|quot\w*|suppose|maybe|"
        r"лож\w*|невер\w*|предлож\w*|если|цитат\w*|отклон\w*|возможно|пример)\b"
    )
    if prefix.count("```") % 2 or prefix.count("~~~") % 2 or re.search(
        uncertain, frame.casefold()
    ):
        return "candidate"
    return "source_supported"


def validate_relation(atom: dict, source_text: str) -> dict | None:
    value = atom.get("relation")
    if value is None:
        return None
    try:
        relation = FactRelation.model_validate(value)
        if relation.evidence not in source_text:
            raise ValueError
        claim = canonical_claim(atom["text"])
        target = canonical_claim(relation.target_text)
        quote = unicodedata.normalize("NFC", relation.evidence)
        normalized_source = unicodedata.normalize("NFC", source_text)
        if (not claim or not target or claim == target
                or claim not in quote or target not in quote):
            raise ValueError
        if relation.kind == "explicit_supersedes":
            # A deliberately narrow positive syntax excludes negation, proposals,
            # dates, inverse directions and instructions surrounding the relation.
            pattern = re.escape(claim) + r"[ \t]+(?:supersedes|заменяет)[ \t]+" + re.escape(target)
            if (re.fullmatch(pattern, quote.strip(), flags=re.IGNORECASE) is None
                    or not _standalone_assertion(quote, normalized_source)):
                raise ValueError
        return relation.model_dump() | {"target_text": target}
    except (ValidationError, ValueError, TypeError, KeyError):
        raise AppError(
            "invalid_fact_relation", "Связь решений не подтверждена точной цитатой."
        ) from None
