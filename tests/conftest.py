"""Shared pytest fixtures.

Env vars must be set before any core.* module is imported, since core.config reads them at
module level - pytest imports every test module during collection, before any fixture function
runs, so this has to be plain module-level code here rather than inside a fixture.
"""
import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix="kemono-test-")
os.environ.setdefault('DATA_DIR', f'{_ROOT}/data')
os.environ.setdefault('CONFIG_DIR', f'{_ROOT}/config')
os.environ.setdefault('TEMP_DIR', f'{_ROOT}/temp')
os.environ.setdefault('SESSION_COOKIE', 'test-session-cookie')

import pytest

import core.config
import core.db
import core.file
import core.files
import core.management.reconcile


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    """Points DATA_DIR/CONFIG_DIR/TEMP_DIR at a fresh tmp_path and creates them on disk, patching
    every module that captured these by value at its own import time (core.file, core.files,
    core.management.reconcile) in addition to core.config itself - core.config.DATA_DIR alone is
    not enough, since modules doing `from .config import DATA_DIR` took a value copy that a later
    patch on core.config won't reach. Returns a dict with 'data'/'config'/'temp' Path keys."""

    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    temp_dir = tmp_path / "temp"
    data_dir.mkdir()
    config_dir.mkdir()
    (config_dir / "logs").mkdir()
    (config_dir / "failed").mkdir()
    (config_dir / "summary").mkdir()
    temp_dir.mkdir()

    monkeypatch.setattr(core.config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(core.config, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(core.config, "TEMP_DIR", str(temp_dir))
    monkeypatch.setattr(core.config, "CONFIG_PATH", str(config_dir / "config.conf"))

    monkeypatch.setattr(core.file, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(core.file, "TEMP_DIR", str(temp_dir))
    monkeypatch.setattr(core.files, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(core.files, "TEMP_DIR", str(temp_dir))
    monkeypatch.setattr(core.management.reconcile, "DATA_DIR", str(data_dir))

    return {"data": data_dir, "config": config_dir, "temp": temp_dir}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Points core.db at a fresh, empty SQLite file for this test only, and closes/clears the
    module-level connection singleton afterward so it can't leak into the next test."""

    monkeypatch.setattr(core.db, "DB_PATH", str(tmp_path / "kemono.db"))
    monkeypatch.setattr(core.db, "_connection", None)
    yield core.db
    if core.db._connection is not None:
        core.db._connection.close()
        core.db._connection = None


@pytest.fixture
def make_creator(tmp_dirs, fresh_db, monkeypatch):
    """Builds a real Creator against isolated tmp_dirs/fresh_db, with get_creator_data() mocked
    to skip the network call. Shared by test_creator.py and management/test_reconcile.py."""

    import core.creator

    def _make(service='patreon', id='111', name='Someone', **profile_overrides):
        profile = {'name': name, 'service': service, 'id': id, 'updated': '2024-01-01T00:00:00.000000'}
        profile.update(profile_overrides)
        monkeypatch.setattr(core.creator, "get_creator_data", lambda *a, **k: profile)
        return core.creator.Creator(service, id)

    return _make
