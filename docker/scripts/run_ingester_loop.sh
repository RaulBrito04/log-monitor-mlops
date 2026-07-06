#!/bin/sh
set -e

LOG_FILE="${LOG_FILE:-/app/logs/app.log}"
LOG_FORMAT="${INGESTER_LOG_FORMAT:-json}"
STATE_FILE="/tmp/ingester_last_line"
BATCH_SIZE="${INGESTER_BATCH_SIZE:-100}"
POLL_INTERVAL="${INGESTER_POLL_INTERVAL:-10}"
TMP_FILE="/tmp/ingester_batch.log"

last_line=0
if [ -f "$STATE_FILE" ]; then
    last_line=$(cat "$STATE_FILE")
fi

while true; do
    if [ ! -f "$LOG_FILE" ]; then
        sleep "$POLL_INTERVAL"
        continue
    fi

    total_lines=$(wc -l < "$LOG_FILE")

    if [ "$total_lines" -gt "$last_line" ]; then
        start_line=$((last_line + 1))
        tail -n +"$start_line" "$LOG_FILE" > "$TMP_FILE"

        if [ -s "$TMP_FILE" ]; then
            python src/log_processor/ingester.py "$TMP_FILE" --batch-size "$BATCH_SIZE" --format "$LOG_FORMAT"
            last_line=$total_lines
            echo "$last_line" > "$STATE_FILE"
        fi
    fi

    sleep "$POLL_INTERVAL"
done
