import sys

import pytest

import reconcile as reconcile_cli


def run_with_args(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["reconcile.py", *args])


def test_domain_overrides_are_applied(tmp_dirs, monkeypatch):
    monkeypatch.setattr(reconcile_cli.config, "SESSION_COOKIE", "test-cookie")
    monkeypatch.setattr(reconcile_cli, "get_creators_from_data_dir", lambda: [])
    run_with_args(monkeypatch, "--domain", "pawchive.pw", "--file-domain", "files.pawchive.pw", "--file-path-prefix", "/x")

    reconcile_cli.main()

    assert reconcile_cli.config.DOMAIN == "pawchive.pw"
    assert reconcile_cli.config.FILE_DOMAIN == "files.pawchive.pw"
    assert reconcile_cli.config.FILE_PATH_PREFIX == "/x"


def test_always_sources_from_data_dir_ignoring_creator_url_file(tmp_dirs, monkeypatch):
    # Unlike download.py, reconcile.py has no CREATORS_FROM_DATA/CREATOR_URL_FILE branching at
    # all - it always calls get_creators_from_data_dir() regardless of what's configured.
    monkeypatch.setattr(reconcile_cli.config, "SESSION_COOKIE", "test-cookie")
    monkeypatch.setattr(reconcile_cli.config, "CREATOR_URL_FILE", "/some/file/that/would/be/used/by/download.py")
    monkeypatch.setattr(reconcile_cli.config, "CREATORS_FROM_DATA", False)
    called = []
    monkeypatch.setattr(reconcile_cli, "get_creators_from_data_dir", lambda: called.append(1) or [])
    run_with_args(monkeypatch)

    reconcile_cli.main()

    assert called == [1]


def test_exits_if_no_session_cookie(tmp_dirs, monkeypatch):
    monkeypatch.setattr(reconcile_cli.config, "SESSION_COOKIE", "")
    called = []
    monkeypatch.setattr(reconcile_cli, "get_creators_from_data_dir", lambda: called.append(1) or [])
    run_with_args(monkeypatch)

    with pytest.raises(SystemExit):
        reconcile_cli.main()

    assert called == []


def test_continues_batch_after_one_creator_fails(tmp_dirs, monkeypatch):
    monkeypatch.setattr(reconcile_cli.config, "SESSION_COOKIE", "test-cookie")
    monkeypatch.setattr(reconcile_cli, "get_creators_from_data_dir", lambda: [('patreon', 'bad'), ('patreon', 'good')])
    run_with_args(monkeypatch)

    reconciled = []

    class FakeCreator:
        def __init__(self, service, id):
            if id == 'bad':
                raise RuntimeError("could not find creator")
            self.service, self.id = service, id

    monkeypatch.setattr(reconcile_cli, "Creator", FakeCreator)
    monkeypatch.setattr(reconcile_cli, "reconcile", lambda creator, add_favorites: reconciled.append(creator.id))

    reconcile_cli.main()  # must not raise despite the first creator failing

    assert reconciled == ['good']
