import logging
import os

import pytest

import core.config as config


@pytest.fixture(autouse=True)
def clean_loggers():
    """_setup_loggers() only ever adds handlers, never removes them - without this, repeated
    calls across tests would accumulate FileHandlers pointing at deleted tmp_path directories."""

    downloader_before = list(config.logger.handlers)
    failure_before = list(config.failure_logger.handlers)
    yield
    for handler in list(config.logger.handlers):
        if handler not in downloader_before:
            handler.close()
            config.logger.removeHandler(handler)
    for handler in list(config.failure_logger.handlers):
        if handler not in failure_before:
            handler.close()
            config.failure_logger.removeHandler(handler)


def test_init_creates_expected_directories(tmp_dirs):
    config.init()

    assert os.path.isdir(tmp_dirs["config"])
    assert os.path.isdir(tmp_dirs["config"] / "logs")
    assert os.path.isdir(tmp_dirs["config"] / "failed")
    assert os.path.isdir(tmp_dirs["config"] / "summary")
    assert os.path.isdir(tmp_dirs["data"])
    assert os.path.isdir(tmp_dirs["temp"])


def test_init_wipes_temp_dir(tmp_dirs):
    stray = tmp_dirs["temp"] / "leftover-from-a-crashed-run.tmp"
    stray.write_text("stale")

    config.init()

    assert not stray.exists()
    assert os.path.isdir(tmp_dirs["temp"])


def test_load_creates_config_conf_from_template_when_missing(tmp_dirs):
    assert not (tmp_dirs["config"] / "config.conf").exists()

    config.load()

    assert (tmp_dirs["config"] / "config.conf").exists()


def test_load_applies_overrides_from_existing_config_conf(tmp_dirs):
    (tmp_dirs["config"] / "config.conf").write_text(
        "DOWNLOAD_MAX_ATTEMPTS=5\nCREATORS_FROM_DATA=true\n"
    )

    config.load()

    assert config.DOWNLOAD_MAX_ATTEMPTS == 5
    assert config.CREATORS_FROM_DATA is True


def test_load_tolerates_oserror_and_keeps_previous_values(tmp_dirs, monkeypatch):
    (tmp_dirs["config"] / "config.conf").write_text("DOWNLOAD_MAX_ATTEMPTS=5\n")
    config.DOWNLOAD_MAX_ATTEMPTS = 999  # sentinel simulating "value from a previous load()"

    monkeypatch.setattr(config.conf, "read", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    config.load()

    assert config.DOWNLOAD_MAX_ATTEMPTS == 999  # untouched, not crashed and not reset


def test_prune_old_logs_removes_only_files_older_than_retention(tmp_dirs):
    old_log = tmp_dirs["config"] / "logs" / "old.log"
    new_log = tmp_dirs["config"] / "logs" / "new.log"
    old_failed = tmp_dirs["config"] / "failed" / "old.json"
    old_summary = tmp_dirs["config"] / "summary" / "old.json"
    old_log.write_text("old")
    new_log.write_text("new")
    old_failed.write_text("old")
    old_summary.write_text("old")

    now = os.path.getmtime(new_log)
    old_time = now - 30 * 86400
    os.utime(old_log, (old_time, old_time))
    os.utime(old_failed, (old_time, old_time))
    os.utime(old_summary, (old_time, old_time))

    config._prune_old_logs(retention_days=14)

    assert not old_log.exists()
    assert not old_failed.exists()
    assert not old_summary.exists()
    assert new_log.exists()


def test_prune_old_logs_disabled_when_retention_days_not_positive(tmp_dirs):
    old_log = tmp_dirs["config"] / "logs" / "old.log"
    old_log.write_text("old")
    old_time = os.path.getmtime(old_log) - 30 * 86400
    os.utime(old_log, (old_time, old_time))

    config._prune_old_logs(retention_days=0)

    assert old_log.exists()


def test_setup_loggers_rejects_invalid_log_level(tmp_dirs):
    with pytest.raises(ValueError):
        config._setup_loggers("NOT_A_LEVEL", 14)


def test_setup_loggers_attaches_stdout_and_file_handlers(tmp_dirs):
    before = len(config.logger.handlers)

    config._setup_loggers("INFO", 14)

    assert len(config.logger.handlers) == before + 2
    assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in config.logger.handlers)
    assert any(isinstance(h, logging.FileHandler) for h in config.logger.handlers)
    assert any(isinstance(h, logging.FileHandler) for h in config.failure_logger.handlers)
