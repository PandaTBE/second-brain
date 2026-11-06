import pytest

from brain_settings.errors import AppError
from brain_settings.llm_cards import validate_card


def card(quote, relation=None):
    atom = {"kind": "decision", "text": "Use Redis.", "evidence": "Use Redis."}
    if relation is not None:
        atom = {**atom, "relation": relation}
    return {
        "title": "Synthetic", "summary": "Source-backed extraction.",
        "keywords": [], "questions": [], "atoms": [atom],
    }


def relation(quote, **changes):
    return {
        "kind": "explicit_supersedes", "target_kind": "decision",
        "target_text": "Use Memcached.", "evidence": quote, **changes,
    }


@pytest.mark.parametrize("quote", [
    "Use Redis. supersedes Use Memcached.",
    "Use Redis. заменяет Use Memcached.",
])
def test_relation_schema_accepts_only_source_proven_directional_supersession(quote):
    clean = validate_card(card(quote, relation(quote)), quote)
    assert clean["atoms"][0]["relation"]["kind"] == "explicit_supersedes"


@pytest.mark.parametrize("quote", [
    "Use Memcached. supersedes Use Redis.",
    "Use Redis. was discussed after Use Memcached.",
    "Use Redis. does not supersede Use Memcached.",
    "Use Redis. не заменяет Use Memcached.",
    "Use Redis.\nQuoted suggestion: supersedes Use Memcached.",
])
def test_relation_rejects_reverse_order_date_inference_negation_or_ambiguous_syntax(quote):
    with pytest.raises(AppError):
        validate_card(card(quote, relation(quote)), quote)


def test_relation_quote_must_be_an_exact_source_substring_and_contain_both_claims():
    quote = "Use Redis. supersedes Use Memcached."
    with pytest.raises(AppError):
        validate_card(card(quote, relation(quote)), "Use Redis.")
    with pytest.raises(AppError):
        validate_card(card(quote, relation("Use Redis.")), quote)


def test_possible_conflict_retains_uncertain_relation_kind_and_source_evidence():
    quote = "Use Redis. Use Memcached."
    clean = validate_card(card(quote, relation(quote, kind="possible_conflict")), quote)
    assert clean["atoms"][0]["relation"]["kind"] == "possible_conflict"


def test_legacy_card_without_relations_remains_valid_and_does_not_invent_one():
    clean = validate_card(card("Use Redis."), "Use Redis.")
    assert clean["atoms"][0].get("relation") is None


@pytest.mark.parametrize("changes", [
    {"kind": "certain_contradiction"}, {"target_kind": "instruction"},
    {"target_text": ""}, {"target_text": "x" * 1501}, {"evidence": "x" * 4001},
])
def test_relation_metadata_is_strict_and_bounded(changes):
    quote = "Use Redis. supersedes Use Memcached."
    with pytest.raises(AppError):
        validate_card(card(quote, relation(quote, **changes)), quote)
