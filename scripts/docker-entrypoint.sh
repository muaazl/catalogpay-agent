#!/bin/sh
# Persistent disks (e.g. Render's /data) are often mounted root-owned. When started as root,
# make the data directory writable for the unprivileged "app" user, then drop privileges.
set -eu

DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R app:app "$DATA_DIR"
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi

exec "$@"
