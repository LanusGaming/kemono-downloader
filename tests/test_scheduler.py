from datetime import datetime, timedelta

import pytest

import scheduler


@pytest.fixture(autouse=True)
def reset_scheduler_state():
    """scheduler._shutdown is a module-level threading.Event created once at import - without
    resetting it, one test calling stop()/_shutdown.set() would leave every later test seeing
    an already-shut-down scheduler."""

    scheduler._shutdown.clear()
    scheduler._current_process = None
    yield
    scheduler._shutdown.clear()
    scheduler._current_process = None


class FakeProcess:
    def __init__(self, returncode=0, still_running=False):
        self.returncode = returncode
        self._still_running = still_running
        self.pid = 12345
        self.signals_received = []

    def poll(self):
        return None if self._still_running else self.returncode

    def wait(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals_received.append(signum)


# --- stop() ---

def test_stop_sets_shutdown_event():
    scheduler.stop(15)
    assert scheduler._shutdown.is_set()


def test_stop_forwards_signal_to_running_subprocess():
    proc = FakeProcess(still_running=True)
    scheduler._current_process = proc

    scheduler.stop(15)

    assert proc.signals_received == [15]


def test_stop_does_not_signal_an_already_finished_subprocess():
    proc = FakeProcess(still_running=False)
    scheduler._current_process = proc

    scheduler.stop(15)

    assert proc.signals_received == []


def test_stop_swallows_process_lookup_error():
    class VanishingProcess(FakeProcess):
        def send_signal(self, signum):
            raise ProcessLookupError()

    scheduler._current_process = VanishingProcess(still_running=True)

    scheduler.stop(15)  # must not raise


# --- run_once() ---

def test_run_once_spawns_download_py_and_returns_its_exit_code(monkeypatch):
    proc = FakeProcess(returncode=3)
    popen_calls = []
    monkeypatch.setattr(scheduler.subprocess, "Popen", lambda *a, **k: popen_calls.append(a) or proc)

    result = scheduler.run_once()

    assert result == 3
    assert popen_calls[0][0] == [scheduler.sys.executable, "download.py"]
    assert scheduler._current_process is None  # cleared after completion


# --- _sleep_until() ---

def test_sleep_until_returns_true_when_target_already_reached():
    past = datetime.now() - timedelta(seconds=1)
    assert scheduler._sleep_until(past, "* * * * *") is True


def test_sleep_until_returns_false_on_shutdown(monkeypatch):
    monkeypatch.setattr(scheduler._shutdown, "wait", lambda timeout: True)
    future = datetime.now() + timedelta(seconds=10)

    assert scheduler._sleep_until(future, "* * * * *") is False


def test_sleep_until_returns_false_when_cron_expression_changes(monkeypatch):
    monkeypatch.setattr(scheduler._shutdown, "wait", lambda timeout: False)
    monkeypatch.setattr(scheduler.config, "CRON_EXPRESSION", "0 0 * * *")
    future = datetime.now() + timedelta(seconds=10)

    assert scheduler._sleep_until(future, "* * * * *") is False  # differs from the changed value


# --- run_loop() ---

def test_run_loop_runs_immediately_when_configured(monkeypatch):
    monkeypatch.setattr(scheduler.config, "RUN_IMMEDIATELY", True)
    monkeypatch.setattr(scheduler.config, "CRON_EXPRESSION", "")
    run_once_calls = []
    monkeypatch.setattr(scheduler, "run_once", lambda: run_once_calls.append(1))
    # Idle-wait immediately requests shutdown so the while loop exits after one iteration.
    monkeypatch.setattr(scheduler._shutdown, "wait", lambda timeout: scheduler._shutdown.set())

    scheduler.run_loop()

    assert run_once_calls == [1]


def test_run_loop_idles_on_invalid_cron_expression_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(scheduler.config, "RUN_IMMEDIATELY", False)
    monkeypatch.setattr(scheduler.config, "CRON_EXPRESSION", "not a valid cron expression")
    monkeypatch.setattr(scheduler._shutdown, "wait", lambda timeout: scheduler._shutdown.set())

    scheduler.run_loop()  # must not raise
