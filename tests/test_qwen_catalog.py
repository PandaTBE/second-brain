"""The documented Qwen model catalog has a native, paginated response."""

import pytest

from brain_settings.errors import AppError
from brain_settings.qwen_catalog import fetch_catalog


def model(identifier, **changes):
    return {"model": identifier, "name": identifier, "capabilities": ["TG"],
            "inference_metadata": {"response_modality": ["Text"]},
            "model_info": {"context_window": 32768}, **changes}


def test_catalog_uses_native_endpoint_and_never_assumes_price_currency():
    calls = []

    def request(base, path, token):
        calls.append((base, path, token))
        return {"output": {"models": [model("qwen-plus", prices=[{"price": "2"}])]}}

    result = fetch_catalog(request, "synthetic-key")
    assert calls == [("https://dashscope-intl.aliyuncs.com/api/v1",
                      "/models?providers=qwen&capabilities=TG&page_size=100&page_no=1",
                      "synthetic-key")]
    assert result["models"] == [{"id": "qwen-plus", "name": "qwen-plus",
                                 "context_length": 32768, "input_price": None,
                                 "output_price": None}]
    assert result["truncated"] is False


def test_catalog_pagination_is_bounded_and_reports_truncation():
    calls = []

    def request(base, path, token):
        calls.append(path)
        return {"output": {"models": [model(f"qwen-{len(calls)}-{index}")
                                       for index in range(100)]}}

    result = fetch_catalog(request, "synthetic-key")
    assert len(calls) == 5 and len(result["models"]) == 500
    assert result["truncated"] is True and "частично" in result["message"]


def test_catalog_filters_unsupported_rows_and_preserves_distinct_text_ids():
    rows = [None, {}, model("qwen-plus"), model("qwen-plus"),
            model("embedding", capabilities=["EMBEDDING"]),
            model("image-only", inference_metadata={"response_modality": ["Image"]}),
            model("qwen-flash", model_info={"context_window": True})]
    result = fetch_catalog(lambda *_: {"output": {"models": rows}}, "synthetic-key")
    assert [item["id"] for item in result["models"]] == ["qwen-plus", "qwen-flash"]
    assert result["models"][1]["context_length"] is None


@pytest.mark.parametrize("payload", [{}, {"output": {}}, {"output": {"models": "bad"}}])
def test_catalog_malformed_response_has_a_safe_error(payload):
    with pytest.raises(AppError) as caught:
        fetch_catalog(lambda *_: payload, "synthetic-key")
    assert caught.value.code == "invalid_catalog"
    assert "synthetic-key" not in str(caught.value)


@pytest.mark.parametrize("identifier,expected", [
    ("qwen-plus", True), ("qwen-flash-latest", True), ("qwen3.7-max", True),
    ("qwen3.8-flash", True), ("qwen3.8-plus", False),
    ("qwen3.7-max-thinking", False), ("qwen3-coder-plus", False),
    ("qwen3.7-max-preview", False), ("qwen3.7-max-2026-01-01", False),
])
def test_non_thinking_parameter_is_only_used_for_documented_hybrid_aliases(identifier, expected):
    from brain_settings.qwen_catalog import supports_non_thinking

    assert supports_non_thinking(identifier) is expected
