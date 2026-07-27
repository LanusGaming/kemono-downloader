import pytest

import download
from core.summary import CreatorSummary


# --- resolve_creators(): true precedence, not just isolated branches ---

def test_creators_from_data_wins_even_with_creator_url_file_also_set(monkeypatch, tmp_path):
    monkeypatch.setattr(download, "get_creators_from_data_dir", lambda: [('patreon', '1')])
    monkeypatch.setattr(download, "get_creators_from_file", lambda path: [('fanbox', '2')])
    monkeypatch.setattr(download, "get_favorite_creators", lambda: [{'service': 'boosty', 'id': '3'}])

    result = download.resolve_creators(True, str(tmp_path / "creators.txt"))

    assert result == [('patreon', '1')]


def test_creator_url_file_wins_over_favorites_when_creators_from_data_false(monkeypatch, tmp_path):
    creator_file = tmp_path / "creators.txt"
    creator_file.write_text("https://kemono.cr/fanbox/user/2\n")
    monkeypatch.setattr(download, "get_creators_from_data_dir", lambda: [('patreon', '1')])
    monkeypatch.setattr(download, "get_favorite_creators", lambda: [{'service': 'boosty', 'id': '3'}])

    result = download.resolve_creators(False, str(creator_file))

    assert result == [('fanbox', '2')]


def test_favorites_used_when_neither_source_set(monkeypatch):
    monkeypatch.setattr(download, "get_favorite_creators", lambda: [{'service': 'boosty', 'id': '3'}])

    result = download.resolve_creators(False, '')

    assert result == [('boosty', '3')]


def test_creator_url_file_auto_created_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "get_creators_from_file", lambda path: [])
    missing_path = tmp_path / "does_not_exist_yet.txt"

    download.resolve_creators(False, str(missing_path))

    assert missing_path.exists()


# --- main() ---

def test_main_exits_before_resolving_creators_if_no_session_cookie(tmp_dirs, monkeypatch):
    monkeypatch.setattr(download.config, "SESSION_COOKIE", "")
    called = []
    monkeypatch.setattr(download, "resolve_creators", lambda *a, **k: called.append(1) or [])

    with pytest.raises(SystemExit):
        download.main()

    assert called == []


def test_main_continues_batch_after_one_creator_fails(tmp_dirs, monkeypatch):
    monkeypatch.setattr(download.config, "SESSION_COOKIE", "test-cookie")
    monkeypatch.setattr(download.config, "TRIGGER_ALBUM_CREATOR", False)
    monkeypatch.setattr(download, "resolve_creators", lambda *a, **k: [('patreon', 'bad'), ('patreon', 'good')])

    downloaded = []

    class FakeCreator:
        def __init__(self, service, id):
            if id == 'bad':
                raise RuntimeError("could not find creator")
            self.service, self.id = service, id

        def download(self):
            downloaded.append(self.id)
            return CreatorSummary(service=self.service, id=self.id, status='no_new_files')

    monkeypatch.setattr(download, "Creator", FakeCreator)

    download.main()  # must not raise despite the first creator failing

    assert downloaded == ['good']
