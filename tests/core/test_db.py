import sqlite3

import pytest

from core.file import File


def make_file(**overrides) -> File:
    data = {
        'creator_id': '111', 'creator_service': 'patreon', 'creator_name': 'Someone',
        'post_id': 'p1', 'post_title': 'Post One', 'published': 1000.0,
        'index': 0, 'hash': 'abc123', 'path': '/data/x/p1/000_file.jpg', 'name': 'file.jpg',
        'url': 'https://kemono.cr/x.jpg', 'type': 'attachment',
    }
    data.update(overrides)
    return File(data)


def test_get_connection_returns_same_object_on_repeat_calls(fresh_db):
    conn1 = fresh_db.get_connection()
    conn2 = fresh_db.get_connection()

    assert conn1 is conn2


def test_ensure_creator_upserts_on_conflict(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'Old Name', 100.0)
    fresh_db.ensure_creator('patreon', '111', 'New Name', 200.0)

    conn = fresh_db.get_connection()
    rows = conn.execute("SELECT * FROM creators").fetchall()

    assert len(rows) == 1
    assert rows[0]['name'] == 'New Name'
    assert rows[0]['last_imported'] == 200.0


def test_upsert_post_upserts_on_conflict(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'Someone', 100.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'Old Title', 1000.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'New Title', 2000.0)

    conn = fresh_db.get_connection()
    rows = conn.execute("SELECT * FROM posts").fetchall()

    assert len(rows) == 1
    assert rows[0]['title'] == 'New Title'
    assert rows[0]['published'] == 2000.0


def test_get_creator_hashes_scoped_per_creator(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'A', 0.0)
    fresh_db.ensure_creator('patreon', '222', 'B', 0.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'Post', 0.0)
    fresh_db.upsert_post('patreon', '222', 'p1', 'Post', 0.0)
    fresh_db.insert_file(make_file(creator_id='111', hash='hash-a', path='/data/x/p1/000_a.jpg'))
    fresh_db.insert_file(make_file(creator_id='222', hash='hash-b', path='/data/y/p1/000_b.jpg'))

    hashes = fresh_db.get_creator_hashes('patreon', '111')

    assert hashes == {'hash-a'}


def test_insert_file_and_file_row_to_dict_round_trip_matches_file_constructor(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'A', 0.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'Post', 0.0)
    file_id = fresh_db.insert_file(make_file())

    row = fresh_db.get_file_by_path('/data/x/p1/000_file.jpg')

    assert row is not None
    assert 'file_index' not in row
    assert row['index'] == 0
    # The dict must be usable directly as File(...) constructor input, per _file_row_to_dict's
    # documented contract.
    rebuilt = File(row)
    assert rebuilt.hash == 'abc123'
    assert rebuilt.id == file_id


def test_path_exists_in_db(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'A', 0.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'Post', 0.0)
    fresh_db.insert_file(make_file())

    assert fresh_db.path_exists_in_db('/data/x/p1/000_file.jpg') is True
    assert fresh_db.path_exists_in_db('/data/x/p1/999_nope.jpg') is False


def test_delete_file_removes_row(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'A', 0.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'Post', 0.0)
    file_id = fresh_db.insert_file(make_file())

    fresh_db.delete_file(file_id)

    assert fresh_db.path_exists_in_db('/data/x/p1/000_file.jpg') is False


def test_count_files_for_creator_distinguishes_archive_type(fresh_db):
    fresh_db.ensure_creator('patreon', '111', 'A', 0.0)
    fresh_db.upsert_post('patreon', '111', 'p1', 'Post', 0.0)
    fresh_db.insert_file(make_file(hash='h1', path='/data/x/p1/000_a.jpg', type='attachment'))
    fresh_db.insert_file(make_file(hash='h2', path='/data/x/p1/001_b.zip', type='archive'))
    fresh_db.insert_file(make_file(hash='h3', path='/data/x/p1/002_c.jpg', type='thumbnail'))

    total, archive = fresh_db.count_files_for_creator('patreon', '111')

    assert total == 3
    assert archive == 1


def test_foreign_key_constraint_is_enforced(fresh_db):
    # No ensure_creator()/upsert_post() call first - the files->posts FK should reject this
    # given PRAGMA foreign_keys=ON.
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.insert_file(make_file())
