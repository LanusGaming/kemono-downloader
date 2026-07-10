import pytest

import app


def test_main_runs_once_and_exits_when_cron_expression_empty(tmp_dirs, monkeypatch):
    monkeypatch.setattr(app.config, "CRON_EXPRESSION", "")
    monkeypatch.setattr(app.scheduler, "run_once", lambda: 7)

    with pytest.raises(SystemExit) as exc_info:
        app.main()

    assert exc_info.value.code == 7


def test_main_starts_scheduler_thread_when_cron_expression_set(tmp_dirs, monkeypatch):
    # config.init() (called inside main()) re-loads config.conf, which would clobber a directly
    # monkeypatched attribute - write the actual file instead, like a real deployment would.
    (tmp_dirs["config"] / "config.conf").write_text("CRON_EXPRESSION=0 * * * *\n")
    run_loop_calls = []
    monkeypatch.setattr(app.scheduler, "run_loop", lambda: run_loop_calls.append(1))

    app.main()  # the real thread runs run_loop() (a no-op here) and main() joins it

    assert run_loop_calls == [1]


def test_handle_signal_calls_scheduler_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(app.scheduler, "stop", lambda signum: calls.append(signum))

    app._handle_signal(15, None)

    assert calls == [15]
