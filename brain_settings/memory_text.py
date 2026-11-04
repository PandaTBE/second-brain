"""Local redaction and deterministic search normalization; no external calls."""

import re

REDACTED = "[REDACTED]"
SECRET_PATTERNS = (
    re.compile(r"\b(?:ntn_|secret_)[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?:sk-[A-Za-z0-9]{32,}|sc_[A-Za-z0-9_-]{32,})"),
    re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|sc_[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9_-]{16,}|"
        r"gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
ASSIGNMENT = re.compile(
    r"""(?im)(["']?\b[\w-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"""
    r"""client[_-]?secret|secret[_-]?key|token)["']?\s*[:=]\s*)("[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)"""
)
AUTHORIZATION = re.compile(
    r"""(?im)(["']?\bauthorization["']?\s*[:=]\s*)("[^"\r\n]*"|'[^'\r\n]*'|[^\r\n,;}]+)"""
)
PRIVATE_BOUNDARIES = re.compile(r"-----(BEGIN|END) [A-Z ]*PRIVATE KEY-----")
URI_PASSWORD = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
WORDS = re.compile(r"[^\W]+", re.UNICODE)
RUSSIAN_ENDINGS = tuple(
    sorted(
        (
            "иями",
            "ями",
            "ами",
            "иях",
            "ого",
            "ему",
            "ыми",
            "ими",
            "ому",
            "ая",
            "яя",
            "ое",
            "ее",
            "ые",
            "ие",
            "ий",
            "ый",
            "ой",
            "ия",
            "ию",
            "ии",
            "ов",
            "ев",
            "ей",
            "ом",
            "ем",
            "ам",
            "ям",
            "ах",
            "ях",
            "ить",
            "ать",
            "ил",
            "ы",
            "и",
            "а",
            "я",
            "у",
            "ю",
            "е",
            "о",
            "ь",
        ),
        key=len,
        reverse=True,
    )
)


def _masked(value: str, preserve_length: bool) -> str:
    if not preserve_length:
        return REDACTED + "\n" * value.count("\n")
    first_line = value.find("\n")
    width = len(value) if first_line < 0 else first_line
    marker = REDACTED[:width]
    return marker + "".join("\n" if char == "\n" else " " for char in value[len(marker) :])


def _private_blocks(text: str, preserve_length: bool) -> str:
    pieces, start, private_start = [], 0, None
    for match in PRIVATE_BOUNDARIES.finditer(text):
        if match[1] == "BEGIN" and private_start is None:
            pieces = [*pieces, text[start : match.start()]]
            private_start = match.start()
        elif match[1] == "END" and private_start is not None:
            pieces = [*pieces, _masked(text[private_start : match.end()], preserve_length)]
            start, private_start = match.end(), None
    if private_start is None:
        pieces = [*pieces, text[start:]]
    else:
        pieces = [*pieces, _masked(text[private_start:], preserve_length)]
    return "".join(pieces)


def redact_text(text: str, *, preserve_length=False) -> str:
    result = str(text).replace("\x00", " " if preserve_length else "")
    result = _private_blocks(result, preserve_length)
    result = URI_PASSWORD.sub(
        lambda match: match[1] + _masked(match[0][len(match[1]) : -1], preserve_length) + "@",
        result,
    )
    result = AUTHORIZATION.sub(lambda match: match[1] + _masked(match[2], preserve_length), result)
    result = ASSIGNMENT.sub(lambda match: match[1] + _masked(match[2], preserve_length), result)
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(lambda match: _masked(match[0], preserve_length), result)
    return result


def normalize_word(word: str) -> str:
    normalized = word.casefold().replace("ё", "е")
    if re.fullmatch(r"[а-я]+", normalized):
        if normalized == "ошибок":
            return "ошибк"
        for ending in RUSSIAN_ENDINGS:
            if normalized.endswith(ending) and len(normalized) - len(ending) >= 3:
                return normalized[: -len(ending)]
    return normalized


def search_terms(text: str) -> list[str]:
    return [normalize_word(word) for word in WORDS.findall(text)]


def normalize_text(text: str) -> str:
    return " ".join(search_terms(text))
