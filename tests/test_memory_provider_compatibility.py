from brain_settings.providers import parse_models


def test_simpleclaw_picker_offers_only_supported_chat_completion_protocol():
    rows = {
        "data": [
            {"id": "compatible", "protocols": ["chat_completions", "messages"]},
            {"id": "messages-only", "protocols": ["messages"]},
            {"id": "responses-only", "protocols": ["responses"]},
        ]
    }
    assert [model["id"] for model in parse_models(rows, "simpleclaw")] == ["compatible"]


def test_known_openai_responses_only_models_are_not_offered_for_chat_cards():
    rows = {
        "data": [
            {"id": "gpt-6-mini"},
            {"id": "gpt-6-codex"},
            {"id": "codex-mini-latest"},
            {"id": "gpt-6-deep-research"},
            {"id": "explicitly-responses", "protocols": ["responses"]},
            {"id": "gpt-6-codex-chat"},
        ]
    }
    assert [model["id"] for model in parse_models(rows, "openai")] == [
        "gpt-6-mini",
        "gpt-6-codex-chat",
    ]
