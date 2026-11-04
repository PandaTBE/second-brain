"""Only safe, bounded provider diagnostics cross the transport boundary."""

import json
from datetime import UTC, datetime
from email.utils import format_datetime
from unittest.mock import Mock

import pytest

from brain_settings.errors import AppError
from brain_settings.provider_errors import MAX_ERROR_BYTES, ProviderHTTPError, rate_limit_error
from brain_settings.transport import ProviderTransport, _check_status


@pytest.mark.parametrize(("header", "expected"), [
    (None, 60), ("", 60), ("invalid", 60), ("nan", 60), ("inf", 60),
    ("-5", 60), ("0", 1), ("17", 17), ("86401", 86400), ("9" * 500, 86400),
])
def test_retry_after_seconds_are_bounded(header, expected):
    assert rate_limit_error(header).retry_after == expected


def test_retry_after_supports_http_dates_without_relying_on_local_timezone():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    header = format_datetime(datetime(2026, 11, 4, 0, 2, tzinfo=UTC), usegmt=True)
    assert rate_limit_error(header, now=now.timestamp()).retry_after == 120
    assert rate_limit_error(header, now=now.timestamp() + 300).retry_after == 1


def test_http_date_fallbacks_and_future_limit_are_bounded():
    now = datetime(2026, 11, 4, tzinfo=UTC).timestamp()
    assert rate_limit_error("Fri, 06 Nov 2026 00:00:00 GMT", now=now).retry_after == 86400
    assert rate_limit_error("Wed, 04 Nov 2026 00:01:00", now=now).retry_after == 60
    assert rate_limit_error("Wed, 04 Nov 2026 00:01:00 GMT", now=float("inf")).retry_after == 60


@pytest.mark.parametrize(("provider_code", "manual"), [
    ("RUBLE_QUOTA_EXCEEDED", False), ("TOKEN_QUOTA_EXCEEDED", False),
    ("API_KEY_RUBLE_BUDGET_EXCEEDED", True), ("PREPAID_RUBLE_BALANCE_EXCEEDED", True),
    ("insufficient_quota", True),
])
def test_only_known_provider_codes_are_preserved(provider_code, manual):
    body = json.dumps({"error": {"code": provider_code, "message": "private material"}}).encode()
    error = rate_limit_error("12", body)
    assert isinstance(error, AppError)
    assert error.code == "rate_limited"
    assert error.status_code == 429
    assert error.provider_code == provider_code
    assert error.requires_user_action is manual
    assert error.request_sent is True
    assert "private material" not in str(error)


@pytest.mark.parametrize("body", [
    b'{"error":{"code":"untrusted-value","message":"private material"}}',
    b'{"error":{"code":[],"message":"private material"}}',
    b'{"error":[]}', b'[]', b'broken', b'\xff',
    b'{"error":{"code":"TOKEN_QUOTA_EXCEEDED"},"padding":"' + b'x' * MAX_ERROR_BYTES,
])
def test_malformed_and_unknown_errors_fall_back_without_echoing_body(body):
    error = rate_limit_error(error_body=body)
    assert error.provider_code is None
    assert error.requires_user_action is False
    assert "untrusted-value" not in str(error)
    assert "private material" not in str(error)


def test_messages_distinguish_temporary_quota_and_manual_billing_action():
    temporary = str(rate_limit_error())
    assert "частоту" not in temporary
    quota = str(rate_limit_error(error_body=b'{"error":{"code":"TOKEN_QUOTA_EXCEEDED"}}'))
    budget = str(rate_limit_error(
        error_body=b'{"error":{"code":"API_KEY_RUBLE_BUDGET_EXCEEDED"}}'))
    assert len({temporary, quota, budget}) == 3


@pytest.fixture
def outbound(monkeypatch):
    import brain_settings.transport as module

    response = Mock(status=429)
    response.read.return_value = b'{"error":{"code":"TOKEN_QUOTA_EXCEEDED"}}'
    response.getheader.return_value = "75"
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(module, "PinnedHTTPSConnection", Mock(return_value=connection))
    monkeypatch.setattr(module, "validate_public_host", lambda hostname: ["93.184.216.34"])
    return connection, response


def test_post_429_propagates_safe_metadata_with_one_request_and_bounded_read(outbound):
    connection, response = outbound
    with pytest.raises(ProviderHTTPError) as caught:
        ProviderTransport().post_json("https://api.example.com/v1", "/chat/completions", {})
    assert caught.value.retry_after == 75
    assert caught.value.provider_code == "TOKEN_QUOTA_EXCEEDED"
    response.read.assert_called_once_with(MAX_ERROR_BYTES)
    response.getheader.assert_called_once_with("Retry-After")
    connection.request.assert_called_once()
    connection.close.assert_called_once()


def test_post_429_still_reports_rate_limit_if_diagnostics_cannot_be_read(outbound):
    connection, response = outbound
    response.read.side_effect = OSError("private material")
    with pytest.raises(ProviderHTTPError) as caught:
        ProviderTransport().post_json("https://api.example.com/v1", "/chat/completions", {})
    assert caught.value.retry_after == 75
    assert caught.value.provider_code is None
    assert "private material" not in str(caught.value)
    connection.request.assert_called_once()
    connection.close.assert_called_once()


def test_post_429_does_not_read_beyond_diagnostic_body_limit(outbound):
    connection, response = outbound
    body = (b'{"error":{"message":"' + b'x' * MAX_ERROR_BYTES
            + b'","code":"TOKEN_QUOTA_EXCEEDED"}}')
    response.read.side_effect = lambda size: body[:size]
    with pytest.raises(ProviderHTTPError) as caught:
        ProviderTransport().post_json("https://api.example.com/v1", "/chat/completions", {})
    assert caught.value.provider_code is None
    response.read.assert_called_once_with(MAX_ERROR_BYTES)
    connection.request.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize(("status", "code"), [
    (401, "authentication_failed"), (403, "authentication_failed"),
    (302, "redirect_blocked"), (500, "upstream_error"),
])
def test_other_statuses_keep_existing_safe_behavior(outbound, status, code):
    connection, response = outbound
    response.status = status
    with pytest.raises(AppError) as caught:
        ProviderTransport().post_json("https://api.example.com/v1", "/chat/completions", {})
    assert caught.value.code == code
    response.read.assert_not_called()
    connection.request.assert_called_once()
    connection.close.assert_called_once()


def test_status_only_429_check_remains_compatible():
    with pytest.raises(ProviderHTTPError) as caught:
        _check_status(429)
    assert caught.value.retry_after == 60
