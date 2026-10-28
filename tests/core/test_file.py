import os

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import core.config
import core.file
import core.external
from core.file import DEFAULT_FILE, File
from core.summary import NotFoundError, DownloadTimeoutError, QuotaExceededError, MirrorBlockedError


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


def test_download_429_waits_30s_when_no_retry_after_header(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.config, "DOWNLOAD_RETRY_DELAY", 1)
    sleep_calls = []
    monkeypatch.setattr(core.file.time, "sleep", lambda s: sleep_calls.append(s))

    file = make_file()
    requests_mock.get(file.url, [
        {'status_code': 429},
        {'status_code': 200, 'content': b'file-bytes'},
    ])

    file.download()

    assert sleep_calls == [30]  # not DOWNLOAD_RETRY_DELAY's 1


def test_download_429_honors_retry_after_header(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.config, "DOWNLOAD_RETRY_DELAY", 1)
    sleep_calls = []
    monkeypatch.setattr(core.file.time, "sleep", lambda s: sleep_calls.append(s))

    file = make_file()
    requests_mock.get(file.url, [
        {'status_code': 429, 'headers': {'Retry-After': '12'}},
        {'status_code': 200, 'content': b'file-bytes'},
    ])

    file.download()

    assert sleep_calls == [12]  # server-provided wait, not the 30s fallback


def test_download_403_fails_immediately_as_mirror_blocked(tmp_dirs, requests_mock, monkeypatch):
    # The mirror's DDoS-Guard front answers 403 once it has flagged this IP as a bot - retrying
    # through it, even with a 30s backoff, only kept the block going.
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 20)
    sleep_calls = []
    monkeypatch.setattr(core.file.time, "sleep", lambda s: sleep_calls.append(s))

    file = make_file()
    requests_mock.get(file.url, status_code=403, text="Are you a bot or using download tools?")

    with pytest.raises(MirrorBlockedError):
        file.download()

    assert requests_mock.call_count == 1
    assert sleep_calls == []
    assert not os.path.exists(file.get_temp_download_path())


def test_download_retry_resumes_partial_temp_file_with_range_request(tmp_dirs, requests_mock, monkeypatch):
    # The mirror serves some large files slowly enough that connections drop mid-file - a retry
    # has to continue from what's already on disk rather than start the whole file over.
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.file.time, "sleep", lambda s: None)

    file = make_file()
    with open(file.get_temp_download_path(), 'wb') as f:
        f.write(b'first-half-')
    requests_mock.get(file.url, status_code=206, content=b'second-half')

    file.download()

    assert requests_mock.request_history[0].headers['Range'] == 'bytes=11-'
    with open(file.get_dest_download_path(), 'rb') as f:
        assert f.read() == b'first-half-second-half'


def test_download_interruptions_that_make_progress_do_not_use_up_attempts(tmp_dirs, monkeypatch):
    # The mirror dropped a 219 MB file's connection every few MB - with each drop counted as a
    # failed attempt, the file would run out of attempts while still steadily completing.
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(core.file.time, "sleep", lambda s: None)

    class PartialResponse:
        status_code = 206

        def __init__(self, chunk, then_break):
            self.chunk, self.then_break = chunk, then_break

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.chunk
            if self.then_break:
                raise RequestsConnectionError("Connection broken")

    responses = iter([PartialResponse(b'aa', True), PartialResponse(b'bb', True),
                      PartialResponse(b'cc', True), PartialResponse(b'dd', False)])
    monkeypatch.setattr(core.file.SESSION, "get", lambda *args, **kwargs: next(responses))

    file = make_file()
    file.download()

    with open(file.get_dest_download_path(), 'rb') as f:
        assert f.read() == b'aabbccdd'


def test_download_starts_over_when_server_ignores_range(tmp_dirs, requests_mock, monkeypatch):
    file = make_file()
    with open(file.get_temp_download_path(), 'wb') as f:
        f.write(b'stale-partial-')
    requests_mock.get(file.url, status_code=200, content=b'whole-file')

    file.download()

    with open(file.get_dest_download_path(), 'rb') as f:
        assert f.read() == b'whole-file'


def test_download_416_discards_partial_file_and_retries_from_scratch(tmp_dirs, requests_mock, monkeypatch):
    monkeypatch.setattr(core.config, "DOWNLOAD_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(core.file.time, "sleep", lambda s: None)

    file = make_file()
    with open(file.get_temp_download_path(), 'wb') as f:
        f.write(b'too-long-partial')
    requests_mock.get(file.url, [
        {'status_code': 416},
        {'status_code': 200, 'content': b'whole-file'},
    ])

    file.download()

    assert 'Range' not in requests_mock.request_history[1].headers
    with open(file.get_dest_download_path(), 'rb') as f:
        assert f.read() == b'whole-file'


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
