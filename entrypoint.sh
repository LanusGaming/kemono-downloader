#!/bin/sh
# Without CRON_EXPRESSION: run once and exit (original behavior, for external schedulers).
# With CRON_EXPRESSION: hand off to supercronic for self-managed scheduling inside the container.
set -e

if [ -z "$CRON_EXPRESSION" ]; then
    exec python3 main.py
fi

echo "CRON_EXPRESSION='$CRON_EXPRESSION' set - running on an internal schedule via supercronic"

if [ "$(echo "$RUN_IMMEDIATELY" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
    echo "RUN_IMMEDIATELY=true - running once now before the first scheduled run"
    python3 main.py
fi

echo "$CRON_EXPRESSION python3 main.py" > /tmp/crontab
exec supercronic /tmp/crontab
