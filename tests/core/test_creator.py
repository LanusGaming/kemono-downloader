import os
import zipfile

import pyzipper
import pytest

import core.creator as creator_module
from core.creator import Creator
from core.file import File


def make_plain_zip(path, files):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def make_aes_zip(path, files, password):
    with pyzipper.AESZipFile(path, 'w', encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode())
        for name, content in files.items():
            zf.writestr(name, content)


# --- __init__ ---

def test_init_raises_if_creator_not_found(make_creator, monkeypatch):
    monkeypatch.setattr(creator_module, "get_creator_data", lambda *a, **k: {})

    with pytest.raises(RuntimeError, match="Could not find creator"):
        Creator('patreon', '111')


def test_init_uses_last_imported_when_present(make_creator):
    creator = make_creator(last_imported='2025-01-01T00:00:00.000000', updated='ignored')
    assert creator.last_imported == '2025-01-01T00:00:00.000000'


def test_init_falls_back_to_updated_when_last_imported_absent(make_creator):
    creator = make_creator(updated='2025-06-01T00:00:00.000000')
    assert creator.last_imported == '2025-06-01T00:00:00.000000'


# --- detect_files_in_post ---

def make_post(**overrides):
    post = {
        'id': 'p1', 'service': 'patreon', 'user': '111', 'title': 'A Post',
        'published': '2024-01-01T00:00:00.000000', 'file': None, 'attachments': [], 'substring': '',
    }
    post.update(overrides)
    return post


def test_detect_files_orders_thumbnail_then_attachments_then_embeds(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = []  # shipped default is ['attachment'] only - allow everything here
    post = make_post(
        file={'path': '/data/thumb.jpg', 'name': 'thumb.jpg'},
        attachments=[{'path': '/data/a1.jpg', 'name': 'a1.jpg'}, {'path': '/data/a2.jpg', 'name': 'a2.jpg'}],
        substring='has content',
    )
    monkeypatch.setattr(creator_module, "get_post_data", lambda *a, **k: {
        'post': {'content': '<img src="/data/e1.jpg"><img src="/data/e2.jpg">'}
    })

    files, skipped = creator.detect_files_in_post(post)

    assert skipped == 0
    types = [f.type for f in files]
    assert types == ['thumbnail', 'attachment', 'attachment', 'embed', 'embed']
    assert [f.index for f in files] == [0, 1, 2, 3, 4]


def test_detect_files_skips_embed_api_call_when_not_allowed(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = ['attachment']  # embeds not allowed
    post = make_post(attachments=[{'path': '/data/a1.jpg', 'name': 'a1.jpg'}], substring='has content')

    called = []
    monkeypatch.setattr(creator_module, "get_post_data", lambda *a, **k: called.append(1) or {})

    files, skipped = creator.detect_files_in_post(post)

    assert called == []
    assert [f.type for f in files] == ['attachment']


def test_detect_files_extension_used_for_filtering_prefers_name_falls_back_to_path(make_creator):
    # file_ext (derived from name's extension, falling back to path's if name has none) only
    # drives the ALLOWED_EXTENSIONS filter check - it does not override a non-empty file_name.
    creator = make_creator()
    creator.ALLOWED_EXTENSIONS = ['.gif']
    post = make_post(attachments=[
        {'path': '/data/xx.gif', 'name': 'named.png'},  # name's own extension (.png) is used for
                                                         # filtering, so this is correctly excluded
                                                         # despite the path itself being .gif
    ])

    files, skipped = creator.detect_files_in_post(post)

    assert skipped == 1
    assert files == []


def test_detect_files_synthesizes_name_from_hash_when_name_is_empty(make_creator):
    # file_name is only ever replaced when it's empty outright - falls back to path's extension
    # (via file_ext) for the synthetic hash-based name in that case.
    creator = make_creator()
    post = make_post(attachments=[{'path': '/data/abc123.gif', 'name': ''}])

    files, skipped = creator.detect_files_in_post(post)

    assert skipped == 0
    assert files[0].name == 'abc123.gif'


def test_detect_files_filters_by_allowed_extensions(make_creator):
    creator = make_creator()
    creator.ALLOWED_EXTENSIONS = ['.jpg']
    post = make_post(attachments=[
        {'path': '/data/a.jpg', 'name': 'a.jpg'},
        {'path': '/data/b.mov', 'name': 'b.mov'},
    ])

    files, skipped = creator.detect_files_in_post(post)

    assert skipped == 1
    assert len(files) == 1
    assert files[0].name == 'a.jpg'


# --- download() ---

def test_download_exclude_regex_has_zero_effect_when_include_regex_set(make_creator, monkeypatch):
    creator = make_creator()
    creator.INCLUDE_REGEX = 'Keep.*'
    creator.EXCLUDE_REGEX = 'Keep.*'  # would reject the same post if it had any effect

    post = make_post(title='Keep This', attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: True for f in files})[1])

    creator.download()

    assert len(downloaded) == 1


def test_download_dedups_by_hash(make_creator, monkeypatch):
    creator = make_creator()
    post = make_post(attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: True for f in files})[1])

    # Pre-seed the hash this file's URL would resolve to, simulating "already downloaded".
    from core.utils import get_hash_from_url
    file_url = "https://kemono.cr/data/a.jpg"
    creator.hashes.add(get_hash_from_url(file_url))

    creator.download()

    assert downloaded == []


def test_download_missing_published_uses_next_post_when_first(make_creator, monkeypatch):
    creator = make_creator()
    post_no_date = make_post(id='p1', published=None, attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    post_with_date = make_post(id='p2', published='2024-06-01T00:00:00.000000', attachments=[])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post_no_date, post_with_date])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: True for f in files})[1])

    creator.download()

    assert len(downloaded) == 1
    from core.utils import get_post_time
    assert downloaded[0].published == pytest.approx(get_post_time('2024-06-01T00:00:00.000000') - 1000)


def test_download_missing_published_uses_previous_post_when_not_first(make_creator, monkeypatch):
    creator = make_creator()
    post_with_date = make_post(id='p1', published='2024-06-01T00:00:00.000000', attachments=[])
    post_no_date = make_post(id='p2', published=None, attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post_with_date, post_no_date])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: True for f in files})[1])

    creator.download()

    assert len(downloaded) == 1
    from core.utils import get_post_time
    assert downloaded[0].published == pytest.approx(get_post_time('2024-06-01T00:00:00.000000') - 1000)


def test_download_missing_published_falls_back_to_now_when_no_neighbors(make_creator, monkeypatch):
    creator = make_creator()
    post_no_date = make_post(id='p1', published=None, attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post_no_date])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(creator_module.time, "time", lambda: 999999.0)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: True for f in files})[1])

    creator.download()

    assert len(downloaded) == 1
    assert downloaded[0].published == 999999.0


# --- unpack() ---

def test_unpack_tries_known_password_before_archive_passwords(make_creator, monkeypatch, tmp_path):
    creator = make_creator()
    creator.ARCHIVE_PASSWORDS = ['other-pwd']
    monkeypatch.setattr(creator_module, "get_file_data", lambda h: {'password': 'known-pwd'})

    calls = []

    def fake_extract(path, password):
        calls.append(password)
        if password == 'known-pwd':
            os.makedirs(os.path.splitext(path)[0], exist_ok=True)  # extract() always does this
            return []
        raise RuntimeError("wrong password")

    monkeypatch.setattr(creator_module, "extract", fake_extract)

    file = File({'path': str(tmp_path / "archive.zip"), 'hash': 'x', 'published': 1000.0, 'index': 0})
    result = creator.unpack(file)

    assert result is True
    assert calls == ['known-pwd']  # only the known password was tried


def test_unpack_persists_newly_learned_password(make_creator, monkeypatch, tmp_path, fresh_db):
    creator = make_creator()
    creator.ARCHIVE_PASSWORDS = [None]  # does NOT already contain the archive's real password
    monkeypatch.setattr(creator_module, "get_file_data", lambda h: {'password': 'new-pwd'})

    archive_path = tmp_path / "archive.zip"
    make_aes_zip(archive_path, {'a.txt': b'content'}, 'new-pwd')

    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'Post', 1000.0)

    file = File({
        'path': str(archive_path), 'hash': 'x', 'published': 1000.0, 'index': 0,
        'post_id': 'p1', 'creator_id': creator.id, 'creator_service': creator.service,
    })
    result = creator.unpack(file)

    assert result is True
    assert 'new-pwd' in creator.ARCHIVE_PASSWORDS

    conf_path = creator._config_path()
    assert 'new-pwd' in open(conf_path).read()


def test_unpack_runtime_error_tries_next_password_other_exception_aborts(make_creator, monkeypatch, tmp_path):
    creator = make_creator()
    creator.ARCHIVE_PASSWORDS = ['A', 'B', 'C']
    monkeypatch.setattr(creator_module, "get_file_data", lambda h: {})

    calls = []

    def fake_extract(path, password):
        calls.append(password)
        if password == 'A':
            raise RuntimeError("wrong password")
        if password == 'B':
            raise ValueError("some other unexpected failure")
        raise AssertionError("should never reach password C")

    monkeypatch.setattr(creator_module, "extract", fake_extract)

    file = File({'path': str(tmp_path / "archive.zip"), 'hash': 'x', 'published': 1000.0, 'index': 0})
    result = creator.unpack(file)

    assert result is False
    assert calls == ['A', 'B']


def test_unpack_dedups_and_filters_extracted_entries(make_creator, monkeypatch, tmp_path, fresh_db):
    creator = make_creator()
    creator.ARCHIVE_PASSWORDS = [None]
    creator.ALLOWED_EXTENSIONS = ['.jpg']
    monkeypatch.setattr(creator_module, "get_file_data", lambda h: {})

    archive_path = tmp_path / "archive.zip"
    make_plain_zip(archive_path, {'keep.jpg': b'new content', 'dup.jpg': b'already have this', 'wrong.mov': b'video'})

    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'Post', 1000.0)

    from core.files import generate_hash
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(b'already have this')
        dup_source = tf.name
    creator.hashes.add(generate_hash(dup_source))

    file = File({
        'path': str(archive_path), 'hash': 'x', 'published': 1000.0, 'index': 0,
        'post_id': 'p1', 'creator_id': creator.id, 'creator_service': creator.service,
    })
    result = creator.unpack(file)

    assert result is True
    saved = fresh_db.get_files_for_creator(creator.service, creator.id)
    saved_names = {s['name'] for s in saved if s['type'] == 'archive'}
    assert saved_names == {'keep.jpg'}
