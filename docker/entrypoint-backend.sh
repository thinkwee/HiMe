#!/bin/sh
set -e

# Ensure bind-mounted directories exist and are writable by the hime user.
# Docker creates missing host directories as root; this fixes ownership
# so the non-root hime user can write to them at runtime.
for dir in /app/data/data_stores /app/memory /app/logs /app/ios/Server; do
    mkdir -p "$dir"
done
chown -R hime:hime /app/data /app/memory /app/logs

exec gosu hime "$@"
