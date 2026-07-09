#!/usr/bin/env python3
"""Container entrypoint - decides one-shot vs. internally-scheduled, and (soon) will start a
remote-control API alongside the scheduler thread. Replaces supercronic + entrypoint.sh entirely.
"""
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
    if not config.CRON_EXPRESSION:
        # No API exists yet in this stage, so there's nothing to stay alive for without a
        # schedule - matches today's exact one-shot behavior. Once the API is built, this
        # branch needs to change: start the API and don't exit even without a schedule.
        sys.exit(scheduler.run_once())

    scheduler_thread = threading.Thread(target=scheduler.run_loop)
    scheduler_thread.start()

    # TODO: start the (future) remote-control API here once built.
    scheduler_thread.join()
    logger.info("App exiting")

if __name__ == '__main__':
    main()
