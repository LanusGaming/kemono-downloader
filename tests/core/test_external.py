import subprocess

import pytest

import core.config
import core.external as external
from core.external import find_external_links, ListingError
from core.summary import QuotaExceededError, UnsupportedLinkError, ExternalDownloadError


# --- find_external_links() ---

def test_finds_gdrive_file_link_with_password():
    content = (
        '<p>Download: <a href="https://drive.google.com/file/d/1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9/view?usp=sharing">'
        'https://drive.google.com/file/d/1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9/view?usp=sharing</a></p>'
        '<p>pass : raigyocreative</p>'
    )
    links = find_external_links(content)

    assert len(links) == 1
    assert links[0]['platform'] == 'gdrive'
    assert links[0]['kind'] == 'file'
    assert links[0]['ref_id'] == '1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9'
    assert links[0]['password'] == 'raigyocreative'


def test_finds_gdrive_folder_link_no_password():
    content = '<p>https://drive.google.com/drive/folders/1LF7YpCl1yyPGT8px15JiCIAJH1JuKO5A?usp=sharing</p>'
    links = find_external_links(content)

    assert len(links) == 1
    assert links[0]['kind'] == 'folder'
    assert links[0]['ref_id'] == '1LF7YpCl1yyPGT8px15JiCIAJH1JuKO5A'
    assert links[0]['password'] is None


def test_finds_mega_new_and_old_file_links():
    content = (
        '<p>https://mega.nz/file/GVpRiBCT#oHZyie5GJQ1CHmpH955ImkPD6ROYdMh3dR4PObsCVKY</p>'
        '<p>https://mega.nz/#!ViQi1K5L!35W8DIeusoAjAygylh8S1NLqXCBouafURsCPtO-jAn0</p>'
    )
    links = find_external_links(content)

    assert len(links) == 2
    assert all(l['platform'] == 'mega' and l['kind'] == 'file' for l in links)


def test_finds_mega_folder_link():
    content = '<p>https://mega.nz/folder/yAl0CTbK#1cqkX0-m7UuYWHHbPxHgUw</p>'
    links = find_external_links(content)

    assert len(links) == 1
    assert links[0]['kind'] == 'folder'
    assert links[0]['ref_id'] == 'yAl0CTbK'


def test_finds_space_broken_mega_link_used_to_dodge_auto_linkify():
    content = (
        '<p>Put this link into your browser and then enter the password: '
        'https://mega.nz/ #P!AgDkHxeMhlqxe_93nzk5mX00JLpCywQk9gUy0rU7rVS-BN9MBo5BRm_eEmBa5XIbpDSHlkkiUdaSBc8LjNiJa45E4mqiUcba4mdAT5vREMBedgx4mQ38wQ '
        'Password: ZglSAKdlc</p>'
    )
    links = find_external_links(content)

    assert len(links) == 1
    assert links[0]['kind'] == 'unsupported'  # Mega's link-level password feature - megatools can't open it


def test_password_scrape_requires_ascii_value_avoids_cjk_false_positive():
    # "パスタ" (pasta) starts with "パス" ("pass") but continues in kana, not ASCII - a real post
    # seen during development ("パスタが美味しくておすすめです") would otherwise false-positive.
    content = '<p>https://drive.google.com/file/d/1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9/view</p><p>パスタが美味しくておすすめです</p>'
    links = find_external_links(content)

    assert links[0]['password'] is None


def test_password_scrape_handles_cjk_labels_with_and_without_colon():
    content_colon = '<p>https://drive.google.com/file/d/1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9/view</p><p>パスワード：4654</p>'
    assert find_external_links(content_colon)[0]['password'] == '4654'

    content_no_colon = '<p>https://drive.google.com/file/d/1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9/view</p><p>密码0000</p>'
    assert find_external_links(content_no_colon)[0]['password'] == '0000'


def test_no_links_returns_empty_list():
    assert find_external_links('<p>just a normal post with no links</p>') == []
    assert find_external_links('') == []


def test_duplicate_link_only_returned_once():
    url = 'https://drive.google.com/file/d/1QHb6UPDJokQ0Bb6Hvp3E6hnr9OnvyYT9/view'
    content = f'<p><a href="{url}">{url}</a></p>'  # href + visible text duplicate the same URL
    assert len(find_external_links(content)) == 1


# --- Google Drive ---

def test_list_gdrive_requires_api_key(monkeypatch):
    monkeypatch.setattr(core.config, "GOOGLE_API_KEY", '')
    with pytest.raises(ListingError, match="GOOGLE_API_KEY"):
        external.list_gdrive({'kind': 'file', 'ref_id': 'abc'})


def test_list_gdrive_file_returns_single_entry(monkeypatch, requests_mock):
    monkeypatch.setattr(core.config, "GOOGLE_API_KEY", 'test-key')
    requests_mock.get(
        'https://www.googleapis.com/drive/v3/files/abc123',
        json={'id': 'abc123', 'name': 'archive.zip', 'size': '42'},
    )

    entries = external.list_gdrive({'kind': 'file', 'ref_id': 'abc123'})

    assert entries == [{'id': 'abc123', 'name': 'archive.zip', 'size': 42}]


def test_list_gdrive_folder_recurses_and_skips_google_docs(monkeypatch, requests_mock):
    monkeypatch.setattr(core.config, "GOOGLE_API_KEY", 'test-key')

    def respond(request, context):
        if "'root' in parents" in request.qs.get('q', [''])[0]:
            return {'files': [
                {'id': 'f1', 'name': 'a.jpg', 'size': '10', 'mimeType': 'image/jpeg'},
                {'id': 'sub', 'name': 'subfolder', 'mimeType': 'application/vnd.google-apps.folder'},
                {'id': 'doc1', 'name': 'notes', 'mimeType': 'application/vnd.google-apps.document'},
            ]}
        return {'files': [{'id': 'f2', 'name': 'b.jpg', 'size': '20', 'mimeType': 'image/jpeg'}]}

    requests_mock.get('https://www.googleapis.com/drive/v3/files', json=respond)

    entries = external.list_gdrive({'kind': 'folder', 'ref_id': 'root'})

    assert {e['id'] for e in entries} == {'f1', 'f2'}


def test_download_gdrive_file_raises_quota_exceeded(monkeypatch, requests_mock):
    monkeypatch.setattr(core.config, "GOOGLE_API_KEY", 'test-key')
    requests_mock.get(
        'https://www.googleapis.com/drive/v3/files/abc123',
        status_code=403, text='Download quota exceeded for this file',
    )

    with pytest.raises(QuotaExceededError):
        external.download_gdrive_file('abc123', '/tmp/whatever')


# --- Mega ---

MEGA_FOLDER_LISTING_STDOUT = (
    "1. Ilias Alcyone legends 0.7.10/\n"
    "|--\x1b[0m2. Ilias-0.7.10-mac.zip (1.5 GiB)\n"
    "|--\x1b[0m3. Ilias-0.7.10-pc.zip (1.5 GiB)\n"
    "|--\x1b[0m4. ilias-0.7.10-android.apk (1.6 GiB)\n"
    "Enter numbers of files or folders to download separated by spaces "
    "(or type 'all' to download everything, or a range with two numbers separated by '-'):\n"
    "> WARNING: Skipping non-numeric value 'q'\n"
    "WARNING: Nothing was selected\n"
)


def test_list_mega_folder_parses_real_choose_files_output(monkeypatch):
    monkeypatch.setattr(
        external.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=MEGA_FOLDER_LISTING_STDOUT, stderr=''),
    )

    entries = external.list_mega_folder('https://mega.nz/folder/yAl0CTbK#1cqkX0-m7UuYWHHbPxHgUw')

    assert entries == [
        {'number': 2, 'name': 'Ilias-0.7.10-mac.zip', 'path': 'Ilias Alcyone legends 0.7.10/Ilias-0.7.10-mac.zip'},
        {'number': 3, 'name': 'Ilias-0.7.10-pc.zip', 'path': 'Ilias Alcyone legends 0.7.10/Ilias-0.7.10-pc.zip'},
        {'number': 4, 'name': 'ilias-0.7.10-android.apk', 'path': 'Ilias Alcyone legends 0.7.10/ilias-0.7.10-android.apk'},
    ]


def test_list_mega_folder_raises_on_dead_link(monkeypatch):
    monkeypatch.setattr(
        external.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout='', stderr="ERROR: API call 'g' failed: Server returned error ENOENT"),
    )

    with pytest.raises(ListingError):
        external.list_mega_folder('https://mega.nz/file/dead#link')


def test_megatools_not_installed_raises_listing_error(monkeypatch):
    def raise_not_found(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(external.subprocess, "run", raise_not_found)

    with pytest.raises(ListingError, match="megatools binary not found"):
        external.list_mega_folder('https://mega.nz/folder/x#y')


def test_download_mega_selection_raises_quota_exceeded(monkeypatch, tmp_path):
    monkeypatch.setattr(
        external.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout='', stderr='ERROR: Transfer quota exceeded'),
    )

    with pytest.raises(QuotaExceededError):
        external.download_mega_selection('https://mega.nz/folder/x#y', [2, 3], str(tmp_path))


def test_download_mega_file_returns_new_file_path(monkeypatch, tmp_path):
    def fake_run(*a, **k):
        (tmp_path / "downloaded.zip").write_bytes(b"content")
        return subprocess.CompletedProcess(a, 0, stdout='', stderr='')
    monkeypatch.setattr(external.subprocess, "run", fake_run)

    path = external.download_mega_file('https://mega.nz/file/x#y', str(tmp_path))

    assert path == str(tmp_path / "downloaded.zip")


def test_download_mega_file_raises_when_nothing_appears(monkeypatch, tmp_path):
    monkeypatch.setattr(
        external.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='', stderr=''),
    )

    with pytest.raises(ExternalDownloadError, match="no file appeared"):
        external.download_mega_file('https://mega.nz/file/x#y', str(tmp_path))
