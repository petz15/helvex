#!/bin/sh
set -e

echo "Running Alembic migrations…"
attempt=0
max_attempts=12
until alembic upgrade head; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "Migration failed after $max_attempts attempts — aborting" >&2
        exit 1
    fi
    echo "Migration attempt $attempt/$max_attempts failed — retrying in 5s…"
    sleep 5
done

echo "Starting application…"
exec "$@"
