import os
import zipfile

import pyzipper
import pytest

import core.creator as creator_module
import core.external as external_module
from core.creator import Creator
from core.file import File
from core.summary import FileOutcome, ExtractionError, QuotaExceededError


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


def test_init_raises_if_ever_imported_is_false(make_creator, monkeypatch):
    monkeypatch.setattr(creator_module, "get_creator_data", lambda *a, **k: {
        'name': 'Someone', 'service': 'patreon', 'id': '111',
        'updated': '2024-01-01T00:00:00.000000', 'ever_imported': False,
    })

    with pytest.raises(RuntimeError, match="not yet imported"):
        Creator('patreon', '111')


def test_init_succeeds_if_ever_imported_is_true(make_creator):
    creator = make_creator(ever_imported=True)
    assert creator.name == 'Someone'


def test_init_succeeds_when_ever_imported_absent(make_creator):
    # kemono.cr itself has no ever_imported field on its profile response - absence must not
    # be treated as False.
    creator = make_creator()
    assert creator.name == 'Someone'


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
    post = make_post(attachments=[{'path': '/data/abc123def456ghi789jkl.gif', 'name': ''}])

    files, skipped = creator.detect_files_in_post(post)

    assert skipped == 0
    assert files[0].name == 'abc123def456ghi789jkl.gif'


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


def test_detect_files_handles_flat_post_data_shape_from_some_mirrors(make_creator, monkeypatch):
    # kemono.cr wraps the full post in {"post": {...}}, but at least one mirror (pawchive.pw)
    # returns it flat instead - get_post_data() must work with either shape.
    creator = make_creator()
    creator.ALLOWED_TYPES = ['embed']
    post = make_post(substring='has content')
    monkeypatch.setattr(creator_module, "get_post_data", lambda *a, **k: {
        'id': 'p1', 'content': '<img src="/data/e1.jpg">',  # no 'post' wrapper
    })

    files, skipped = creator.detect_files_in_post(post)

    assert [f.type for f in files] == ['embed']


def test_detect_files_uses_content_already_on_post_without_extra_api_call(make_creator, monkeypatch):
    # Some mirrors send full `content` directly in the post-list response - the extra
    # get_post_data() call should be skipped entirely in that case.
    creator = make_creator()
    creator.ALLOWED_TYPES = ['embed']
    post = make_post(content='<img src="/data/e1.jpg">', substring='has content')

    called = []
    monkeypatch.setattr(creator_module, "get_post_data", lambda *a, **k: called.append(1) or {})

    files, skipped = creator.detect_files_in_post(post)

    assert called == []
    assert [f.type for f in files] == ['embed']


# --- detect_files_in_post() - external links ---

def test_detect_files_external_not_implied_by_empty_allowed_types(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = []  # allows attachment/thumbnail/embed, but NOT external
    post = make_post(content='<p>https://drive.google.com/file/d/abc123def456ghi789jkl/view</p>', substring='x')

    called = []
    monkeypatch.setattr(external_module, "list_gdrive", lambda *a, **k: called.append(1) or [])

    files, skipped = creator.detect_files_in_post(post)

    assert called == []
    assert [f.type for f in files] == []


def test_detect_files_external_gdrive_file_produces_file_with_scraped_password(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = ['external']
    post = make_post(
        content='<p>https://drive.google.com/file/d/abc123def456ghi789jkl/view</p><p>pass: hunter2</p>',
        substring='x',
    )
    monkeypatch.setattr(external_module, "list_gdrive", lambda link: [{'id': 'abc123def456ghi789jkl', 'name': 'archive.zip', 'size': 100}])

    files, skipped = creator.detect_files_in_post(post)

    assert len(files) == 1
    file = files[0]
    assert file.type == 'external'
    assert file.source == 'gdrive'
    assert file.ref_id == 'abc123def456ghi789jkl'
    assert file.name == 'archive.zip'
    assert file.password == 'hunter2'
    assert file.url == 'gdrive:abc123def456ghi789jkl'


def test_detect_files_external_dedups_against_previously_recorded_urls(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = ['external']
    creator.external_urls = {'gdrive:abc123def456ghi789jkl'}
    post = make_post(content='<p>https://drive.google.com/file/d/abc123def456ghi789jkl/view</p>', substring='x')
    monkeypatch.setattr(external_module, "list_gdrive", lambda link: [{'id': 'abc123def456ghi789jkl', 'name': 'archive.zip', 'size': 100}])

    files, skipped = creator.detect_files_in_post(post)

    assert files == []


def test_detect_files_external_listing_failure_is_skipped_not_raised(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = ['external']
    post = make_post(content='<p>https://drive.google.com/file/d/abc123def456ghi789jkl/view</p>', substring='x')

    def raise_listing_error(link):
        raise external_module.ListingError("GOOGLE_API_KEY is not set")
    monkeypatch.setattr(external_module, "list_gdrive", raise_listing_error)

    files, skipped = creator.detect_files_in_post(post)

    assert files == []
    assert skipped == 1


def test_detect_files_external_mega_unsupported_link_is_skipped(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = ['external']
    post = make_post(
        content='<p>https://mega.nz/ #P!AgDkHxeMhlqxe_93nzk5mX00JLpCywQk9gUy0rU7rVS-BN9MBo5BRm_eEmBa5XIbpDSHlkkiUdaSBc8LjNiJa45E4mqiUcba4mdAT5vREMBedgx4mQ38wQ</p>',
        substring='x',
    )

    files, skipped = creator.detect_files_in_post(post)

    assert files == []
    assert skipped == 1


def test_detect_files_external_mega_folder_produces_one_file_per_entry(make_creator, monkeypatch):
    creator = make_creator()
    creator.ALLOWED_TYPES = ['external']
    post = make_post(content='<p>https://mega.nz/folder/abc123def456ghi789jkl#key</p>', substring='x')
    monkeypatch.setattr(external_module, "list_mega_folder", lambda url: [
        {'number': 2, 'name': 'a.zip', 'path': 'Set/a.zip'},
        {'number': 3, 'name': 'b.zip', 'path': 'Set/b.zip'},
    ])

    files, skipped = creator.detect_files_in_post(post)

    assert len(files) == 2
    assert [f.source for f in files] == ['mega', 'mega']
    assert [f.ref_id for f in files] == ['Set/a.zip', 'Set/b.zip']
    assert [f.url for f in files] == ['mega:abc123def456ghi789jkl:Set/a.zip', 'mega:abc123def456ghi789jkl:Set/b.zip']


# --- download() ---

def test_download_skips_post_with_has_full_false(make_creator, monkeypatch):
    creator = make_creator()
    post = make_post(has_full=False, attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

    creator.download()

    assert downloaded == []


def test_download_does_not_skip_post_when_has_full_absent(make_creator, monkeypatch):
    # kemono.cr itself has no has_full field on its posts - absence must not be treated as False.
    creator = make_creator()
    post = make_post(attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

    creator.download()

    assert len(downloaded) == 1


def test_download_exclude_regex_has_zero_effect_when_include_regex_set(make_creator, monkeypatch):
    creator = make_creator()
    creator.INCLUDE_REGEX = 'Keep.*'
    creator.EXCLUDE_REGEX = 'Keep.*'  # would reject the same post if it had any effect

    post = make_post(title='Keep This', attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

    creator.download()

    assert len(downloaded) == 1


def test_download_dedups_by_hash(make_creator, monkeypatch):
    creator = make_creator()
    post = make_post(attachments=[{'path': '/data/a.jpg', 'name': 'a.jpg'}])
    monkeypatch.setattr(creator_module, "get_all_posts_from_creator", lambda *a, **k: [post])
    monkeypatch.setattr(creator_module.time, "sleep", lambda s: None)
    downloaded = []
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

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
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

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
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

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
    monkeypatch.setattr(Creator, "download_all_files", lambda self, files: (downloaded.extend(files), {f: FileOutcome('success') for f in files})[1])

    creator.download()

    assert len(downloaded) == 1
    assert downloaded[0].published == 999999.0


# --- download_mega_link() ---

def make_mega_file(creator, ref_id, link_url, **overrides):
    data = {
        'creator_id': creator.id, 'creator_service': creator.service, 'creator_name': creator.name,
        'post_id': 'p1', 'post_title': 'Post', 'published': 1000.0, 'index': 0,
        'name': os.path.basename(ref_id) if ref_id else 'solo.jpg',
        'url': f'mega:folderref:{ref_id}' if ref_id else 'mega:folderref',
        'type': 'external', 'source': 'mega', 'ref_id': ref_id, 'link_url': link_url,
    }
    data.update(overrides)
    return File(data)


def test_download_mega_link_single_file_downloads_directly(make_creator, monkeypatch, fresh_db):
    creator = make_creator()
    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'Post', 1000.0)

    file = make_mega_file(creator, ref_id='', link_url='https://mega.nz/file/x#y', name='solo.jpg')

    def fake_download_mega_file(url, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, 'solo.jpg')
        with open(path, 'wb') as f:
            f.write(b'content')
        return path
    monkeypatch.setattr(creator_module.external, "download_mega_file", fake_download_mega_file)

    results = creator.download_mega_link('https://mega.nz/file/x#y', [file])

    assert results[file].status == 'success'
    assert os.path.exists(file.path)


def test_download_mega_link_folder_selects_new_files_by_fresh_listing(make_creator, monkeypatch, fresh_db):
    creator = make_creator()
    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'Post', 1000.0)

    file_a = make_mega_file(creator, ref_id='Set/a.bin', link_url='https://mega.nz/folder/x#y', name='a.bin')
    file_b = make_mega_file(creator, ref_id='Set/b.bin', link_url='https://mega.nz/folder/x#y', name='b.bin')

    # Numbers are re-resolved from a fresh listing at download time, not reused from detection -
    # folder contents can change in the gap between the two (see Creator.download_mega_link's
    # docstring).
    monkeypatch.setattr(creator_module.external, "list_mega_folder", lambda url: [
        {'number': 2, 'name': 'a.bin', 'path': 'Set/a.bin'},
        {'number': 3, 'name': 'b.bin', 'path': 'Set/b.bin'},
    ])

    selection_calls = []
    def fake_selection(url, numbers, dest_dir):
        selection_calls.append(numbers)
        os.makedirs(os.path.join(dest_dir, 'Set'), exist_ok=True)
        with open(os.path.join(dest_dir, 'Set/a.bin'), 'wb') as f:
            f.write(b'a')
        with open(os.path.join(dest_dir, 'Set/b.bin'), 'wb') as f:
            f.write(b'b')
    monkeypatch.setattr(creator_module.external, "download_mega_selection", fake_selection)

    results = creator.download_mega_link('https://mega.nz/folder/x#y', [file_a, file_b])

    assert selection_calls == [[2, 3]]
    assert results[file_a].status == 'success'
    assert results[file_b].status == 'success'


def test_download_mega_link_file_no_longer_in_folder_fails_that_file_only(make_creator, monkeypatch, fresh_db):
    creator = make_creator()
    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'Post', 1000.0)

    file_a = make_mega_file(creator, ref_id='Set/a.bin', link_url='https://mega.nz/folder/x#y', name='a.bin')
    file_gone = make_mega_file(creator, ref_id='Set/gone.bin', link_url='https://mega.nz/folder/x#y', name='gone.bin')

    monkeypatch.setattr(creator_module.external, "list_mega_folder", lambda url: [
        {'number': 2, 'name': 'a.bin', 'path': 'Set/a.bin'},
    ])

    def fake_selection(url, numbers, dest_dir):
        assert numbers == [2]
        os.makedirs(os.path.join(dest_dir, 'Set'), exist_ok=True)
        with open(os.path.join(dest_dir, 'Set/a.bin'), 'wb') as f:
            f.write(b'a')
    monkeypatch.setattr(creator_module.external, "download_mega_selection", fake_selection)

    results = creator.download_mega_link('https://mega.nz/folder/x#y', [file_a, file_gone])

    assert results[file_a].status == 'success'
    assert results[file_gone].status == 'failed'


def test_download_mega_link_download_error_fails_all_files_in_link(make_creator, monkeypatch, fresh_db):
    creator = make_creator()
    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'Post', 1000.0)

    file_a = make_mega_file(creator, ref_id='Set/a.bin', link_url='https://mega.nz/folder/x#y', name='a.bin')
    file_b = make_mega_file(creator, ref_id='Set/b.bin', link_url='https://mega.nz/folder/x#y', name='b.bin')

    monkeypatch.setattr(creator_module.external, "list_mega_folder", lambda url: [
        {'number': 2, 'name': 'a.bin', 'path': 'Set/a.bin'},
        {'number': 3, 'name': 'b.bin', 'path': 'Set/b.bin'},
    ])

    def fake_selection(url, numbers, dest_dir):
        raise QuotaExceededError("bandwidth quota exceeded")
    monkeypatch.setattr(creator_module.external, "download_mega_selection", fake_selection)

    results = creator.download_mega_link('https://mega.nz/folder/x#y', [file_a, file_b])

    assert results[file_a].status == 'failed'
    assert results[file_a].reason == 'external_quota_exceeded'
    assert results[file_b].status == 'failed'


# --- unpack() ---

def test_unpack_tries_scraped_external_password_before_archive_passwords(make_creator, monkeypatch, tmp_path):
    creator = make_creator()
    creator.ARCHIVE_PASSWORDS = ['other-pwd']
    monkeypatch.setattr(creator_module, "get_file_data", lambda h: {})

    calls = []

    def fake_extract(path, password):
        calls.append(password)
        if password == 'scraped-pwd':
            os.makedirs(os.path.splitext(path)[0], exist_ok=True)
            return []
        raise RuntimeError("wrong password")

    monkeypatch.setattr(creator_module, "extract", fake_extract)

    file = File({
        'path': str(tmp_path / "archive.zip"), 'hash': 'x', 'published': 1000.0, 'index': 0,
        'type': 'external', 'password': 'scraped-pwd',
    })
    creator.unpack(file)

    assert calls == ['scraped-pwd']


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
    creator.unpack(file)

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
    creator.unpack(file)

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
    with pytest.raises(ExtractionError):
        creator.unpack(file)

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
    creator.unpack(file)

    saved = fresh_db.get_files_for_creator(creator.service, creator.id)
    saved_names = {s['name'] for s in saved if s['type'] == 'archive'}
    assert saved_names == {'keep.jpg'}
