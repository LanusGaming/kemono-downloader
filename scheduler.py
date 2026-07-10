#!/usr/bin/env python3
"""Runs download.py once (no CRON_EXPRESSION) or on a repeating schedule.

download.py always runs as a subprocess: core.config's logger setup only runs once per process,
so running it in-process here wouldn't get a fresh log file each run."""

import logging, subprocess, sys, threading
from datetime import datetime

from croniter import croniter

from core import config

logger = logging.getLogger("scheduler")

IDLE_POLL_SECONDS = 30           # how often to recheck for a schedule once CRON_EXPRESSION is empty
MAX_SLEEP_INCREMENT_SECONDS = 2  # how often to recheck CRON_EXPRESSION while waiting - cheap
                                  # in-memory read, kept short for quick pickup of a change

_shutdown = threading.Event()
_current_process: subprocess.Popen | None = None

def stop(signum):
    """Requests run_loop() stop and forwards signum to an in-progress download.py subprocess,
    so it isn't killed uncleanly."""

    _shutdown.set()
    if _current_process is not None and _current_process.poll() is None:
        logger.info(f"Forwarding signal to in-progress download.py (pid {_current_process.pid})")
        try:
            _current_process.send_signal(signum)
        except ProcessLookupError:
            pass

def run_once() -> int:
    """Runs download.py to completion as a subprocess and returns its exit code."""

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
    """Sleeps in bounded increments, re-checking config.CRON_EXPRESSION each time. Returns True
    if `target` was reached undisturbed, False if interrupted (schedule changed or shutdown)."""

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
    """Computes the next run from CRON_EXPRESSION, sleeps until then, and runs download.py -
    repeating until stop() is called."""

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
