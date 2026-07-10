import core.management.reconcile as reconcile_module
from core.file import File
from core.files import generate_hash
from core.management.reconcile import reconcile


def make_post(**overrides):
    post = {
        'id': 'p1', 'service': 'patreon', 'user': '111', 'title': 'A Post',
        'published': '2024-01-01T00:00:00.000000', 'file': None, 'attachments': [], 'substring': '',
    }
    post.update(overrides)
    return post


def test_no_folder_on_disk_makes_no_api_calls(make_creator, monkeypatch, tmp_dirs):
    creator = make_creator()
    called = []
    monkeypatch.setattr(reconcile_module, "get_all_posts_from_creator", lambda *a, **k: called.append(1) or [])

    reconcile(creator)

    assert called == []


def test_matched_file_recorded_and_archive_children_tracked(make_creator, monkeypatch, tmp_dirs, fresh_db):
    creator = make_creator()
    creator.ALLOWED_TYPES = []

    folder = tmp_dirs["data"] / f"{creator.name}_{creator.service}_{creator.id}"
    post_dir = folder / "A_Post_p1"
    post_dir.mkdir(parents=True)

    archive_path = post_dir / "000_bundle.zip"
    archive_path.write_bytes(b'archive content')
    archive_hash = generate_hash(str(archive_path))

    # The archive's extracted-children folder, matching extract()'s naming convention.
    extracted_dir = post_dir / "000_bundle"
    extracted_dir.mkdir()
    child_path = extracted_dir / "000_page.jpg"
    child_path.write_bytes(b'page content')

    post = make_post(attachments=[{'path': '/data/bundle.zip', 'name': 'bundle.zip'}])
    monkeypatch.setattr(reconcile_module, "get_all_posts_from_creator", lambda *a, **k: [post])

    # Make detect_files_in_post() resolve to a File whose URL hashes to our real archive hash.
    monkeypatch.setattr(
        reconcile_module, "get_hash_from_url",
        lambda url: archive_hash if 'bundle.zip' in url else 'unrelated-hash',
    )

    reconcile(creator)

    saved = fresh_db.get_files_for_creator(creator.service, creator.id)
    matched = [f for f in saved if f['path'] == str(archive_path)]
    assert len(matched) == 1
    assert matched[0]['type'] == 'attachment'

    child = [f for f in saved if f['path'] == str(child_path)]
    assert len(child) == 1
    assert child[0]['type'] == 'archive'
    assert child[0]['parent_archive_id'] == matched[0]['id']


def test_unmatched_file_recorded_as_straggler(make_creator, monkeypatch, tmp_dirs, fresh_db):
    creator = make_creator()

    folder = tmp_dirs["data"] / f"{creator.name}_{creator.service}_{creator.id}"
    post_dir = folder / "A_Post_p1"
    post_dir.mkdir(parents=True)
    stray_path = post_dir / "000_orphan.jpg"
    stray_path.write_bytes(b'no matching post')

    monkeypatch.setattr(reconcile_module, "get_all_posts_from_creator", lambda *a, **k: [])

    reconcile(creator)

    saved = fresh_db.get_files_for_creator(creator.service, creator.id)
    assert len(saved) == 1
    assert saved[0]['path'] == str(stray_path)
    assert saved[0]['post_id'] is None
    assert saved[0]['type'] == 'attachment'


def test_already_recorded_file_is_skipped(make_creator, monkeypatch, tmp_dirs, fresh_db):
    creator = make_creator()

    folder = tmp_dirs["data"] / f"{creator.name}_{creator.service}_{creator.id}"
    post_dir = folder / "A_Post_p1"
    post_dir.mkdir(parents=True)
    existing_path = post_dir / "000_existing.jpg"
    existing_path.write_bytes(b'already tracked')

    fresh_db.ensure_creator(creator.service, creator.id, creator.name, creator.last_imported)
    fresh_db.upsert_post(creator.service, creator.id, 'p1', 'A Post', 1000.0)
    fresh_db.insert_file(File({
        'creator_id': creator.id, 'creator_service': creator.service, 'post_id': 'p1',
        'published': 1000.0, 'index': 0, 'hash': 'whatever', 'path': str(existing_path),
        'name': 'existing.jpg', 'type': 'attachment',
    }))

    monkeypatch.setattr(reconcile_module, "get_all_posts_from_creator", lambda *a, **k: [])

    reconcile(creator)

    saved = fresh_db.get_files_for_creator(creator.service, creator.id)
    assert len(saved) == 1  # not duplicated


def test_filenames_with_colon_are_skipped(make_creator, monkeypatch, tmp_dirs, fresh_db):
    creator = make_creator()

    folder = tmp_dirs["data"] / f"{creator.name}_{creator.service}_{creator.id}"
    post_dir = folder / "A_Post_p1"
    post_dir.mkdir(parents=True)
    (post_dir / "000_file.jpg:Zone.Identifier").write_bytes(b'windows ads artifact')

    monkeypatch.setattr(reconcile_module, "get_all_posts_from_creator", lambda *a, **k: [])

    reconcile(creator)

    assert fresh_db.get_files_for_creator(creator.service, creator.id) == []


def test_malformed_filename_is_skipped_not_fatal(make_creator, monkeypatch, tmp_dirs, fresh_db):
    creator = make_creator()

    folder = tmp_dirs["data"] / f"{creator.name}_{creator.service}_{creator.id}"
    post_dir = folder / "A_Post_p1"
    post_dir.mkdir(parents=True)
    (post_dir / "no_underscore_prefix_convention.jpg").write_bytes(b'x')  # "no" isn't an int
    (post_dir / "000_valid.jpg").write_bytes(b'y')

    monkeypatch.setattr(reconcile_module, "get_all_posts_from_creator", lambda *a, **k: [])

    reconcile(creator)  # must not raise

    saved = fresh_db.get_files_for_creator(creator.service, creator.id)
    assert len(saved) == 1
    assert saved[0]['name'] == 'valid.jpg'
