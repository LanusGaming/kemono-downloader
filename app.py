#!/usr/bin/env python3
"""Container entrypoint - runs once or starts the scheduler thread based on CRON_EXPRESSION.
Will also host a remote-control API once built."""

import logging, signal, sys, threading

from core import config
import scheduler

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] APP %(levelname)s: %(message)s",
                     datefmt="%y-%m-%d %H:%M:%S", stream=sys.stdout)
logger = logging.getLogger("app")

def _handle_signal(signum, _frame):
    logger.info(f"Received {signal.Signals(signum).name} - shutting down")
    scheduler.stop(signum)

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

def main():
    config.init()

    if not config.CRON_EXPRESSION:
        # No API yet, so nothing to stay alive for without a schedule - once built, this
        # branch must start the API and stay running even here.
        sys.exit(scheduler.run_once())

    scheduler_thread = threading.Thread(target=scheduler.run_loop)
    scheduler_thread.start()

    # TODO: start the remote-control API here once built.
    scheduler_thread.join()
    logger.info("App exiting")

if __name__ == '__main__':
    main()
