"""Synthetic GigaCode envelopes; no CLI, credentials or archive data are used."""

import json

import pytest

from brain_settings.errors import AppError
from brain_settings.gigacode_output import (
    MAX_EVENTS,
    MAX_OUTPUT_BYTES,
    SAFE_MODE_WARNING,
    normalize_gigacode_output,
)


def envelope(text="OK", **result):
    return [
        {"type": "system", "subtype": "init", "tools": ["run_shell_command"],
         "mcp_servers": []},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": text}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": text,
         "num_turns": 1, "permission_denials": [],
         "stats": {"tools": {"totalCalls": 0, "totalSuccess": 0, "byName": {}},
                   "skills": {"totalCalls": 0}}, **result},
    ]


def normalize(events, prefix=""):
    return normalize_gigacode_output(prefix + json.dumps(events, ensure_ascii=False))


def test_observed_array_normalizes_final_result_and_usage_without_mutating_input():
    events = envelope("Цитата 🧠", usage={"input_tokens": 17, "output_tokens": 4,
                                         "total_tokens": 21, "cache_read_input_tokens": 3})
    before = json.dumps(events, ensure_ascii=False)
    actual = normalize(events)
    assert actual == {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": "Цитата 🧠"}}], "usage": {
            "prompt_tokens": 17, "completion_tokens": 4, "total_tokens": 21}}
    assert json.dumps(events, ensure_ascii=False) == before


def test_only_explicit_final_result_is_used():
    events = envelope("final")
    events[1]["message"]["content"][0]["text"] = "intermediate"
    assert normalize(events)["choices"][0]["message"]["content"] == "final"


@pytest.mark.parametrize("prefix", ["", " \n", SAFE_MODE_WARNING + "\n",
                                  SAFE_MODE_WARNING + "\r\n"])
def test_only_known_safe_mode_warning_is_tolerated(prefix):
    assert normalize(envelope(), prefix)["choices"][0]["message"]["content"] == "OK"


def test_utf8_bytes_and_maximum_content_size_are_supported():
    actual = normalize_gigacode_output(json.dumps(envelope("x" * 32000)).encode())
    assert len(actual["choices"][0]["message"]["content"]) == 32000


@pytest.mark.parametrize("value", [None, [], {}, 7, b"\xff", "", "not JSON",
                                  "[] trailing", "WARNING unknown\n[]",
                                  "```json\n[]\n```", "{}", "[]", "[null]",
                                  '[{"type":"result","type":"system"}]',
                                  '[{"type":"result","value":NaN}]',
                                  "[" * 2000 + "]" * 2000,
                                  "\ud800"])
def test_malformed_output_is_rejected_with_safe_error(value):
    with pytest.raises(AppError) as caught:
        normalize_gigacode_output(value)
    assert caught.value.code == "gigacode_response_invalid"
    assert caught.value.status_code == 502
    assert "not JSON" not in caught.value.message


@pytest.mark.parametrize("events", [
    envelope()[:-1], envelope() + [envelope()[-1]],
    [envelope()[-1], {"type": "system"}],
    [{"type": "unknown"}, envelope()[-1]],
    envelope()[:-1] + ["invalid"],
])
def test_result_must_be_unique_final_success_event(events):
    with pytest.raises(AppError):
        normalize(events)


@pytest.mark.parametrize("update", [
    {"subtype": "error"}, {"is_error": True}, {"is_error": 0},
    {"result": ""}, {"result": " "}, {"result": None}, {"result": {}},
    {"result": "x" * 32001}, {"permission_denials": ["read_file"]},
    {"permission_denials": None}, {"stats": []},
    {"stats": {"tools": {"totalCalls": 1}}},
    {"stats": {"tools": {"totalCalls": True}}},
    {"stats": {"tools": {"totalCalls": "0"}}},
    {"stats": {"tools": {"totalCalls": -1}}},
    {"stats": {"tools": {"totalSuccess": 1}}},
    {"stats": {"tools": {"byName": {"read_file": {"count": 1}}}}},
    {"stats": {"tools": None}}, {"stats": {"skills": {"totalCalls": 1}}},
])
def test_errors_and_reported_tool_activity_are_rejected(update):
    with pytest.raises(AppError):
        normalize(envelope(**update))


@pytest.mark.parametrize("event", [
    {"type": "tool_call"}, {"type": "tool_result"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
    {"type": "assistant", "message": {"tool_calls": [{"id": "synthetic"}]}},
    {"type": "assistant", "message": {"role": "tool", "content": "output"}},
    {"type": "assistant", "message": {"function_call": {"name": "read_file"}}},
])
def test_nested_or_top_level_tool_events_are_rejected(event):
    with pytest.raises(AppError):
        normalize([event, envelope()[-1]])


def test_missing_usage_is_unknown_and_partial_usage_is_not_invented():
    assert "usage" not in normalize(envelope())
    assert normalize(envelope(usage={"input_tokens": 7}))["usage"] == {"prompt_tokens": 7}


def test_nested_type_values_do_not_crash_and_deep_metadata_is_rejected():
    events = envelope()
    events[1]["message"]["content"][0]["metadata"] = {"type": []}
    assert normalize(events)["choices"][0]["message"]["content"] == "OK"
    nested = "value"
    for _ in range(70):
        nested = [nested]
    events[1]["metadata"] = nested
    with pytest.raises(AppError):
        normalize(events)


@pytest.mark.parametrize("usage", [None, [], {"input_tokens": True},
                                   {"output_tokens": -1}, {"total_tokens": 1.5},
                                   {"input_tokens": "3"}])
def test_malformed_reported_usage_is_rejected(usage):
    with pytest.raises(AppError):
        normalize(envelope(usage=usage))


def test_output_and_event_count_are_bounded():
    for output in (" " * (MAX_OUTPUT_BYTES + 1), "🧠" * (MAX_OUTPUT_BYTES // 4 + 1)):
        with pytest.raises(AppError):
            normalize_gigacode_output(output)
    with pytest.raises(AppError):
        normalize([{"type": "system"}] * MAX_EVENTS + [envelope()[-1]])


def test_normalized_card_is_accepted_by_existing_card_pipeline():
    from brain_settings.llm_cards import _response_content, validate_card

    text = "A synthetic source quotation."
    card = {"title": "Synthetic", "summary": "A test", "keywords": ["test"],
            "questions": ["What is tested?"], "atoms": []}
    content = _response_content(normalize(envelope(json.dumps(card))))
    assert validate_card(json.loads(content), text) == card


@pytest.mark.parametrize("decisions", [
    {}, {"accept": 0, "reject": 0, "modify": 0, "auto_accept": 0},
])
def test_zero_decision_map_is_compatible_metadata(decisions):
    events = envelope(stats={"tools": {
        "totalCalls": 0, "totalSuccess": 0, "totalFail": 0,
        "totalDurationMs": 0, "totalDecisions": decisions, "byName": {},
    }, "skills": {"totalCalls": 0, "totalSuccess": 0, "totalFail": 0, "byName": {}}})
    assert normalize(events)["choices"][0]["message"]["content"] == "OK"


@pytest.mark.parametrize("decisions", [
    {"accept": 1}, {"reject": 1}, {"modify": 1}, {"auto_accept": 1},
    {"accept": True}, {"accept": False}, {"accept": -1}, {"accept": "0"},
    {"accept": 0.0}, {"unknown": 0}, {"accept": {}},
])
def test_nonzero_or_malformed_decision_maps_still_fail(decisions):
    with pytest.raises(AppError):
        normalize(envelope(stats={"tools": {"totalCalls": 0, "totalDecisions": decisions}}))
