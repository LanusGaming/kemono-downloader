#!/usr/bin/env python3
"""Scheduling logic for running download.py - either once (no CRON_EXPRESSION, matching an
external scheduler like host cron/Unraid) or repeatedly on a schedule. app.py is the actual
entrypoint/PID 1 - signal handling must happen on the main thread, so it lives there; this module
just runs on a background thread app.py starts, and exposes stop() for app.py's signal handler.

download.py always runs as a subprocess, never an in-process call: core.config's automatic logger
setup runs once per process, so re-running download.py's own logic in-process within this
long-lived interpreter wouldn't get a fresh per-run log file each time.
"""
import logging, subprocess, sys, threading
from datetime import datetime

from croniter import croniter

from core import config

logger = logging.getLogger("scheduler")

IDLE_POLL_SECONDS = 30           # how often to recheck for a schedule once CRON_EXPRESSION is empty
MAX_SLEEP_INCREMENT_SECONDS = 2  # how often to recheck CRON_EXPRESSION while waiting for the next
                                  # scheduled run - cheap in-memory read, kept short for near-
                                  # instant pickup of a change made through the future API

_shutdown = threading.Event()
_current_process: subprocess.Popen | None = None

def stop(signum):
    """Called by app.py's signal handler on the main thread - requests run_loop() stop, and
    forwards the signal to an in-progress download.py subprocess so it isn't killed uncleanly."""
    _shutdown.set()
    if _current_process is not None and _current_process.poll() is None:
        logger.info(f"Forwarding signal to in-progress download.py (pid {_current_process.pid})")
        try:
            _current_process.send_signal(signum)
        except ProcessLookupError:
            pass

def run_once() -> int:
    global _current_process
    logger.info("Starting download.py run")
    _current_process = subprocess.Popen([sys.executable, "download.py"])
    returncode = _current_process.wait()
    _current_process = None
    if returncode == 0:
        logger.info("download.py run finished successfully")
    elif not _shutdown.is_set():
        logger.error(f"download.py exited with code {returncode} - see config/logs and config/failed")
    return returncode

def _sleep_until(target: datetime, cron_expr: str) -> bool:
    """Sleeps in bounded increments, re-checking config.CRON_EXPRESSION each time (a plain
    in-memory read - always current in this process). Returns True if `target` was reached
    undisturbed, False if interrupted early (schedule changed or shutdown)."""
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            return True
        if _shutdown.wait(timeout=min(remaining, MAX_SLEEP_INCREMENT_SECONDS)):
            return False
        if config.CRON_EXPRESSION != cron_expr:
            logger.info("CRON_EXPRESSION changed while waiting - recomputing next run time")
            return False

def run_loop():
    """The recurring scheduling loop - called by app.py on a background thread, only once
    app.py has confirmed CRON_EXPRESSION is set (the no-cron case is handled directly by app.py,
    on the main thread, so the whole process can exit - see app.py)."""
    logger.info(f"CRON_EXPRESSION='{config.CRON_EXPRESSION}' set - running on an internal schedule")
    if config.RUN_IMMEDIATELY:
        logger.info("RUN_IMMEDIATELY=true - running once now before the first scheduled tick")
        run_once()

    while not _shutdown.is_set():
        cron_expr = config.CRON_EXPRESSION

        if not cron_expr:
            logger.info("CRON_EXPRESSION is now empty - idling until a schedule is set again")
            _shutdown.wait(timeout=IDLE_POLL_SECONDS)
            continue

        try:
            next_run = croniter(cron_expr, datetime.now()).get_next(datetime)
        except Exception as e:
            logger.error(f"Invalid CRON_EXPRESSION '{cron_expr}' ({e}) - idling until corrected")
            _shutdown.wait(timeout=IDLE_POLL_SECONDS)
            continue

        logger.info(f"Next run scheduled for {next_run:%Y-%m-%d %H:%M:%S}")
        reached = _sleep_until(next_run, cron_expr)
        if _shutdown.is_set():
            break
        if not reached:
            continue  # schedule changed mid-wait - recompute from the top

        run_once()

    logger.info("Scheduler loop exiting")
