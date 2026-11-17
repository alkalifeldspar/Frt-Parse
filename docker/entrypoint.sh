#!/bin/bash
# Runs MySQL (through the official image's entrypoint, which handles first-time
# initialisation from MYSQL_ROOT_PASSWORD) and the Node admin UI side by side.
# If either process exits, the other is stopped and the container exits so
# Docker's restart policy can take over.
set -euo pipefail

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set}"
: "${UI_PASSWORD:?UI_PASSWORD must be set}"
export FRT_DB_NAME="${FRT_DB_NAME:-FRT}"
export MYSQL_DATABASE="${FRT_DB_NAME}"   # created by the official entrypoint on first start

mkdir -p /data
chown frt:frt /data

mysql_pid=""
node_pid=""

shutdown() {
  echo "[entrypoint] stopping…"
  [ -n "$node_pid" ] && kill -TERM "$node_pid" 2>/dev/null || true
  [ -n "$mysql_pid" ] && kill -TERM "$mysql_pid" 2>/dev/null || true
}
trap shutdown TERM INT

docker-entrypoint.sh mysqld &
mysql_pid=$!

# During first-time init the official entrypoint runs a temporary server with
# networking disabled, so a TCP ping only succeeds once the real server is up.
echo "[entrypoint] waiting for MySQL…"
until MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin ping -h127.0.0.1 -uroot --silent >/dev/null 2>&1; do
  if ! kill -0 "$mysql_pid" 2>/dev/null; then
    echo "[entrypoint] mysqld exited during startup" >&2
    wait "$mysql_pid" || true
    exit 1
  fi
  sleep 2
done
echo "[entrypoint] MySQL is up; starting admin UI"

gosu frt node /app/ui/server.js &
node_pid=$!

set +e
wait -n "$mysql_pid" "$node_pid"
status=$?
shutdown
wait "$node_pid" 2>/dev/null
wait "$mysql_pid" 2>/dev/null
exit "$status"
