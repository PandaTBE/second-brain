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


@pytest.mark.parametrize("framing", [
    "It is false that {quote}", "If {quote}", "Earlier quote: {quote}",
    "Rejected proposal:\n{quote}", "> {quote}", "Hypothetical example:\n{quote}",
])
def test_inner_positive_quote_cannot_override_negative_conditional_or_quoted_source_frame(framing):
    quote = "Use Redis. supersedes Use Memcached."
    with pytest.raises(AppError):
        validate_card(card(quote, relation(quote)), framing.format(quote=quote))


def test_literal_negative_new_claim_can_still_explicitly_replace_old_decision():
    quote = "Do not use Redis. supersedes Use Memcached."
    value = card(quote, relation(quote))
    value = {**value, "atoms": [{**value["atoms"][0], "text": "Do not use Redis.",
                                "evidence": "Do not use Redis."}]}
    assert validate_card(value, quote)["atoms"][0]["relation"]["kind"] == "explicit_supersedes"


@pytest.mark.parametrize("source", [
    "```text\n{quote}\n```", "{quote}\nThis is false.",
])
def test_supersession_rejects_fenced_examples_and_following_rejection(source):
    quote = "Use Redis. supersedes Use Memcached."
    with pytest.raises(AppError):
        validate_card(card(quote, relation(quote)), source.format(quote=quote))
