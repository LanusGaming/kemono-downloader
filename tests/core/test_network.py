import logging

import pytest
from requests_mock import ANY as ANY_URL

import core.config
from core.network import call_api, call_api_action, get_domain_config


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr("core.network.time.sleep", lambda s: calls.append(s))
    return calls


def test_get_domain_config_builds_urls_from_current_config(monkeypatch):
    monkeypatch.setattr(core.config, "DOMAIN", "example.test")
    monkeypatch.setattr(core.config, "FILE_DOMAIN", "")
    monkeypatch.setattr(core.config, "FILE_PATH_PREFIX", "")

    result = get_domain_config()

    assert result['api_base'] == "https://example.test/api/v1"
    assert result['file_base_url'] == "https://example.test"


def test_get_domain_config_file_domain_overrides_when_set(monkeypatch):
    monkeypatch.setattr(core.config, "DOMAIN", "example.test")
    monkeypatch.setattr(core.config, "FILE_DOMAIN", "files.example.test")
    monkeypatch.setattr(core.config, "FILE_PATH_PREFIX", "/prefix")

    result = get_domain_config()

    assert result['file_base_url'] == "https://files.example.test/prefix"


def test_call_api_404_returns_none_with_no_retry(requests_mock, no_real_sleep):
    requests_mock.get(ANY_URL, status_code=404)

    result = call_api("some/endpoint", max_attempts=5)

    assert result is None
    assert requests_mock.call_count == 1
    assert no_real_sleep == []


def test_call_api_corrupt_gzip_falls_back_to_text(requests_mock):
    # Starts with the gzip magic bytes but isn't valid gzip data - gzip.decompress() should
    # raise BadGzipFile, which call_api() catches to fall back to response.text instead of
    # propagating the error.
    requests_mock.get(ANY_URL, content=b'\x1f\x8bnot-actually-gzipped')

    result = call_api("some/endpoint")

    assert result is not None
    assert len(result) > 0


def test_call_api_empty_body_returns_none(requests_mock):
    requests_mock.get(ANY_URL, text="   ")

    assert call_api("some/endpoint") is None


def test_call_api_retries_with_exponential_backoff(requests_mock, no_real_sleep):
    requests_mock.get(ANY_URL, status_code=500)

    result = call_api("some/endpoint", max_attempts=3)

    assert result is None
    assert requests_mock.call_count == 3
    assert no_real_sleep == [1, 2]  # 2**0, 2**1 - not called after the final attempt


def test_call_api_succeeds_after_transient_failure(requests_mock, no_real_sleep):
    requests_mock.get(ANY_URL, [
        {'status_code': 500},
        {'status_code': 200, 'text': 'ok'},
    ])

    result = call_api("some/endpoint", max_attempts=3)

    assert result == 'ok'
    assert no_real_sleep == [1]


def test_call_api_action_success_judged_by_status_code_alone(requests_mock):
    requests_mock.post(ANY_URL, status_code=200, text="")  # empty body, still success

    assert call_api_action("some/action") is True


def test_call_api_action_404_returns_false_with_no_retry(requests_mock, no_real_sleep):
    requests_mock.post(ANY_URL, status_code=404)

    result = call_api_action("some/action", max_attempts=5)

    assert result is False
    assert requests_mock.call_count == 1
    assert no_real_sleep == []


def test_call_api_action_409_is_not_treated_as_success(requests_mock, no_real_sleep):
    # call_api_action() itself has no special 409 handling - that's the caller's job
    # (album_creator.trigger_album_creator). raise_for_status() should treat it as any other
    # non-2xx failure and retry/fail normally.
    requests_mock.post(ANY_URL, status_code=409)

    result = call_api_action("some/action", max_attempts=2)

    assert result is False
    assert requests_mock.call_count == 2


def test_cookie_never_appears_in_logs(requests_mock, caplog, monkeypatch):
    monkeypatch.setattr(core.config, "SESSION_COOKIE", "super-secret-cookie-value")
    requests_mock.get(ANY_URL, text="ok")

    with caplog.at_level(logging.DEBUG, logger="downloader"):
        call_api("some/endpoint")

    assert "super-secret-cookie-value" not in caplog.text
    assert "***redacted***" in caplog.text
