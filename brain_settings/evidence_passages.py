"""Deterministic source passages ranked by query coverage, without inference."""

import hashlib
import json
import re
from collections import Counter

from .memory_text import normalize_word, search_terms
from .source_dates import DATE_FIELDS

QUOTE_SIZE = 420
MAX_ALTERNATIVES = 3


def _positions(text: str, terms: list[str]) -> list[tuple[int, int, frozenset[str]]]:
    positions = []
    for token in re.finditer(r"\w+", text):
        word = normalize_word(token.group())
        matched = frozenset(term for term in terms if word.startswith(term))
        if matched:
            positions.append((token.start(), token.end(), matched))
    return positions


def _window(text: str, terms: list[str], size: int = QUOTE_SIZE) -> tuple[int, int, int]:
    positions = _positions(text, terms)
    right, counts, best = 0, Counter(), None
    selected = (0, min(size, len(text)), 0)
    for left, (start, _, _) in enumerate(positions):
        while right < len(positions) and positions[right][1] <= start + size:
            counts.update(positions[right][2])
            right += 1
        if right > left:
            end = positions[right - 1][1]
            distinct = sum(count > 0 for count in counts.values())
            score = (distinct, right - left, -(end - start))
            if best is None or score > best:
                context = min(80, size - (end - start))
                offset = max(0, min(start - context, len(text) - size))
                selected, best = (offset, min(len(text), offset + size), distinct), score
            counts.subtract(positions[left][2])
        else:
            right = left + 1
    return selected


def _atoms(record: dict, terms: list[str]) -> list[tuple[int, dict]]:
    card = record.get("card") or {}
    valid = [
        atom
        for atom in card.get("atoms", [])
        if (
            atom.get("record_id") == record["id"]
            and atom.get("revision") == record["revision"]
            and atom.get("evidence")
            and atom["evidence"] in record["text"]
        )
    ]
    ranked = [
        (len(set(terms) & set(search_terms(atom.get("text", "") + " " + atom["evidence"]))), atom)
        for atom in valid
    ]
    return sorted(ranked, key=lambda pair: -pair[0])


def _atom_range(record: dict, atom: dict, terms: list[str]) -> tuple[int, int]:
    proof = atom["evidence"]
    offset = record["text"].index(proof)
    start, end, coverage = _window(proof, terms)
    if not coverage:
        statement = proof.find(atom.get("text", ""))
        start = max(0, min(statement - 80, len(proof) - QUOTE_SIZE))
        end = min(len(proof), start + QUOTE_SIZE)
    return offset + start, offset + end


def _citation(record: dict, start: int, end: int, selection: str) -> dict:
    text = record["text"]
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    quote = text[start:end]
    identity = json.dumps([record["id"], record["revision"], start, end, quote], ensure_ascii=False)
    return {
        "citation_id": "sb_" + hashlib.sha256(identity.encode()).hexdigest()[:32],
        "record_id": record["id"],
        "revision": record["revision"],
        "source_id": record["source_id"],
        "source_kind": record["source_kind"],
        "title": record["title"][:256],
        "source_uri": record["source_uri"],
        "source_object_id": record["source_object_id"],
        "project": record.get("project"),
        "dates": {name: record.get(name) for name in DATE_FIELDS},
        "start_char": start,
        "end_char": end,
        "quote": quote,
        "context_before": text[max(0, start - 100) : start],
        "context_after": text[end : end + 100],
        "quote_selection": selection,
        "quote_relevance": "uncertain"
        if selection in ("card_evidence_ambiguous", "source_excerpt")
        else "extractive",
    }


def quote_candidates(record: dict, terms: list[str]) -> list[dict]:
    start, end, coverage = _window(record["text"], terms)
    atoms = _atoms(record, terms)
    if atoms and (atoms[0][0] or not coverage):
        ambiguous = not atoms[0][0] and len(atoms) > 1
        choices = atoms[:MAX_ALTERNATIVES] if ambiguous else atoms[:1]
        selection = "card_evidence_ambiguous" if ambiguous else "card_evidence"
        return [
            _citation(record, *_atom_range(record, atom, terms), selection) for _, atom in choices
        ]
    return [_citation(record, start, end, "source_match" if coverage else "source_excerpt")]
