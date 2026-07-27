import os

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import core.config
import core.file
import core.external
from core.file import DEFAULT_FILE, File
from core.summary import NotFoundError, DownloadTimeoutError, QuotaExceededError


def make_file(**overrides) -> File:
    data = {
        'creator_id': '111', 'creator_service': 'patreon', 'creator_name': 'Someone',
        'post_id': 'p1', 'post_title': 'Post_One', 'published': 1000.0,
        'index': 2, 'name': 'file.jpg', 'url': 'https://kemono.cr/data/xx/hash.jpg',
        'type': 'attachment',
    }
    data.update(overrides)
    return File(data)


def test_init_fills_defaults_and_drops_unknown_keys():
    file = File({'name': 'x.jpg', 'bogus_key': 'ignored'})

    assert file.name == 'x.jpg'
    assert not hasattr(file, 'bogus_key')
    assert file.id is None  # untouched default


def test_get_data_round_trips_with_init():
    file = make_file()
    data = file.get_data()

    assert set(data.keys()) == set(DEFAULT_FILE.keys())
    assert File(data).get_data() == data


def test_download_404_fails_immediately_with_no_retry(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 60)
    file = make_file()
    requests_mock.get(file.url, status_code=404)

    with pytest.raises(NotFoundError):
        file.download()

    assert requests_mock.call_count == 1
    assert not os.path.exists(file.get_temp_download_path())


def test_download_502_always_waits_5s_regardless_of_retry_delay(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.config, "DOWNLOAD_RETRY_DELAY", 1)
    sleep_calls = []
    monkeypatch.setattr(core.file.time, "sleep", lambda s: sleep_calls.append(s))

    file = make_file()
    requests_mock.get(file.url, [
        {'status_code': 502},
        {'status_code': 200, 'content': b'file-bytes'},
    ])

    file.download()

    assert sleep_calls == [5]  # not DOWNLOAD_RETRY_DELAY's 1


def test_download_generic_error_retries_using_retry_delay_until_exhausted(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.config, "DOWNLOAD_RETRY_DELAY", 2)
    sleep_calls = []
    monkeypatch.setattr(core.file.time, "sleep", lambda s: sleep_calls.append(s))

    file = make_file()
    requests_mock.get(file.url, exc=RequestsConnectionError("boom"))

    with pytest.raises(DownloadTimeoutError):
        file.download()

    assert requests_mock.call_count == 3
    assert sleep_calls == [2, 2]  # slept between attempts, not after the last one
    assert not os.path.exists(file.get_temp_download_path())


def test_download_success_sets_hash_and_moves_file(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 60)
    file = make_file()
    requests_mock.get(file.url, status_code=200, content=b'hello world')

    file.download()

    assert os.path.exists(file.path)
    assert file.path == file.get_dest_download_path()

    from core.files import generate_hash
    assert file.hash == generate_hash(file.path)


def test_download_success_sets_utime_on_file_and_parent_folder(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 60)
    file = make_file(published=1_700_000_000.0, index=3)
    requests_mock.get(file.url, status_code=200, content=b'hello world')

    file.download()

    file_mtime = os.path.getmtime(file.path)
    folder_mtime = os.path.getmtime(os.path.dirname(file.path))

    assert file_mtime == pytest.approx(1_700_000_000.0 + 3)
    assert folder_mtime == pytest.approx(1_700_000_000.0)


# --- source='gdrive' ---

def test_download_gdrive_dispatches_to_drive_api_not_kemono_url(tmp_dirs, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 60)
    file = make_file(source='gdrive', ref_id='abc123', url='gdrive:abc123')

    calls = []
    def fake_download(ref_id, dest_path):
        calls.append(ref_id)
        with open(dest_path, 'wb') as f:
            f.write(b'drive-bytes')
    monkeypatch.setattr(core.external, "download_gdrive_file", fake_download)

    file.download()

    assert calls == ['abc123']
    assert os.path.exists(file.path)
    from core.files import generate_hash
    assert file.hash == generate_hash(file.path)


def test_download_gdrive_quota_exceeded_fails_immediately_with_no_retry(tmp_dirs, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 60)
    file = make_file(source='gdrive', ref_id='abc123', url='gdrive:abc123')

    calls = []
    def fake_download(ref_id, dest_path):
        calls.append(ref_id)
        raise QuotaExceededError("quota exceeded")
    monkeypatch.setattr(core.external, "download_gdrive_file", fake_download)

    with pytest.raises(QuotaExceededError):
        file.download()

    assert len(calls) == 1  # no retry - quota resets on its own schedule, not by waiting
    assert not os.path.exists(file.get_temp_download_path())


def test_download_gdrive_retries_generic_errors_until_exhausted(tmp_dirs, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.config, "DOWNLOAD_RETRY_DELAY", 0)
    file = make_file(source='gdrive', ref_id='abc123', url='gdrive:abc123')

    calls = []
    def fake_download(ref_id, dest_path):
        calls.append(ref_id)
        raise Exception("transient error")
    monkeypatch.setattr(core.external, "download_gdrive_file", fake_download)

    with pytest.raises(DownloadTimeoutError):
        file.download()

    assert len(calls) == 3


# --- finalize() ---

def test_finalize_moves_temp_path_and_sets_hash(tmp_dirs):
    file = make_file(published=1000.0, index=1)
    temp_path = file.get_temp_download_path()
    os.makedirs(os.path.dirname(temp_path), exist_ok=True)
    with open(temp_path, 'wb') as f:
        f.write(b'already-downloaded-bytes')

    file.finalize(temp_path)

    assert file.path == file.get_dest_download_path()
    assert os.path.exists(file.path)
    assert not os.path.exists(temp_path)
    from core.files import generate_hash
    assert file.hash == generate_hash(file.path)
