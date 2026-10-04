#!/bin/sh
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
mkdir -p /config /library
chown "$PUID:$PGID" /config /library 2>/dev/null || echo "entrypoint: chown failed (host filesystem may not support it)"

# Can the target user write the state folder? If not (e.g. Windows/NAS bind mounts), run as root rather than crash.
if setpriv --reuid="$PUID" --regid="$PGID" --clear-groups sh -c 'touch /config/.write-test && rm /config/.write-test' 2>/dev/null; then
  echo "entrypoint: running as $PUID:$PGID"
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
echo "entrypoint: $PUID:$PGID cannot write /config; running as root instead (set PUID/PGID to the folder owner to fix)"
ls -ld /config
exec "$@"
