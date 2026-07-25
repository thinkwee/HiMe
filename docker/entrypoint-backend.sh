#!/bin/sh
set -e

# Ensure bind-mounted directories exist and are writable by the hime user.
# Docker creates missing host directories as root; this fixes ownership
# so the non-root hime user can write to them at runtime.
for dir in /app/data/data_stores /app/data/personalised_pages /app/memory /app/logs /app/ios/Server /app/prompts /app/skills; do
    mkdir -p "$dir"
done

# Only recurse when the top-level owner is actually wrong. Once a directory
# belongs to hime the `-R` walk is pure overhead, and it grows with the data.
# /app/ios/Server is included because the backend opens watch.db in WAL mode
# and needs to create the -shm/-wal sidecars next to it.
for dir in /app/data /app/memory /app/logs /app/ios/Server /app/prompts /app/skills; do
    if [ "$(stat -c '%u:%g' "$dir")" != "1500:1500" ]; then
        chown -R hime:hime "$dir"
    fi
done

exec gosu hime "$@"
