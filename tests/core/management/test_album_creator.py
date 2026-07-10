from requests_mock import ANY as ANY_URL

from core.management.album_creator import trigger_album_creator


def test_success_returns_true(requests_mock):
    requests_mock.post(ANY_URL, status_code=200)

    assert trigger_album_creator("http://album-creator:8080/run") is True


def test_409_already_running_is_treated_as_success(requests_mock):
    requests_mock.post(ANY_URL, status_code=409)

    assert trigger_album_creator("http://album-creator:8080/run") is True


def test_other_error_status_returns_false(requests_mock):
    requests_mock.post(ANY_URL, status_code=500)

    assert trigger_album_creator("http://album-creator:8080/run") is False


def test_connection_error_returns_false(requests_mock):
    requests_mock.post(ANY_URL, exc=ConnectionError("unreachable"))

    assert trigger_album_creator("http://album-creator:8080/run") is False
