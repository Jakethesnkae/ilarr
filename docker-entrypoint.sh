#!/bin/sh
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
# Bind mounts are created root-owned by Docker; make sure the app user can write its state.
mkdir -p /config /library
chown "$PUID:$PGID" /config /library 2>/dev/null || true
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
