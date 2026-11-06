"""Canonical JSON character accounting and bounded duplicate provenance."""

import json

from .errors import AppError

MAX_CITATIONS = 20
MAX_SUPPORT = 4


def encoded_size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def sized(value: dict) -> dict:
    result = value | {"used_chars": 0}
    for _ in range(5):
        size = encoded_size(result)
        if result["used_chars"] == size:
            return result
        result = result | {"used_chars": size}
    return result


def _groups(current: list[dict]) -> list[list[dict]]:
    groups = {}
    for item in current:
        groups = groups | {item["quote"]: [*groups.get(item["quote"], []), item]}
    return list(groups.values())


def _supported(items: list[dict]) -> tuple[dict, list[str]]:
    support = [
        {
            key: value
            for key, value in item.items()
            if key not in ("quote", "context_before", "context_after")
        }
        for item in items[1 : MAX_SUPPORT + 1]
    ]
    citation = items[0] | ({"also_supported_by": support} if support else {})
    return citation, ["support_limit"] if len(items) > MAX_SUPPORT + 1 else []


def _fit(
    packet: dict, citations: list[dict], citation: dict, reasons: list[str], budget: int
) -> tuple[dict | None, list[str]]:
    if (
        sized(packet | {"citations": citations + [citation], "reasons": reasons})["used_chars"]
        <= budget - 200
    ):
        return citation, reasons
    if citation.get("also_supported_by"):
        citation = {key: value for key, value in citation.items() if key != "also_supported_by"}
        reasons = [*reasons, "support_budget"]
    if (
        sized(packet | {"citations": citations + [citation], "reasons": reasons})["used_chars"]
        <= budget - 200
    ):
        return citation, reasons
    return None, [*reasons, "context_budget"]


def pack_citations(packet: dict, current: list[dict], reasons: list[str]) -> dict:
    budget, citations = packet["budget_chars"], []
    for items in _groups(current):
        citation, extra = _supported(items)
        reasons = list(dict.fromkeys([*reasons, *extra]))
        if len(citations) >= MAX_CITATIONS:
            reasons = [*reasons, "citation_limit"]
            break
        citation, reasons = _fit(packet, citations, citation, reasons, budget)
        if citation is not None:
            citations = [*citations, citation]
    if not citations:
        reasons = [*reasons, "no_usable_evidence"]
    final = sized(
        packet
        | {
            "citations": citations,
            "reasons": list(dict.fromkeys(reasons)),
            "incomplete": bool(reasons),
        }
    )
    if final["used_chars"] > budget:
        raise AppError("invalid_evidence_budget", "Увеличьте бюджет для метаданных запроса.")
    return final
