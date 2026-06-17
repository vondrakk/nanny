#!/bin/sh
# Start as root, make the mounted /data (and an optional SSH key) usable by the
# nanny user, then drop privileges. Fails fast with a clear message if /data
# can't be made writable, instead of letting a child panic cryptically.
set -e

ensure_data_writable() {
  if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/targets /data/prometheus /data/alertmanager
    # chown only if nanny can't already write the dirs (covers fresh bind mounts
    # and volumes left with root-owned subdirs by earlier runs).
    if ! gosu nanny test -w /data \
       || ! gosu nanny test -w /data/prometheus \
       || ! gosu nanny test -w /data/alertmanager \
       || ! gosu nanny test -w /data/targets; then
      chown -R nanny:nanny /data 2>/dev/null || true
    fi
    if ! gosu nanny test -w /data/prometheus; then
      echo "nanny: FATAL — /data is not writable by the app user and chown failed." >&2
      echo "  The /data volume was likely created by an earlier ROOTFUL run, so its" >&2
      echo "  files are owned by a uid this rootless container can't change." >&2
      echo "  Recreate the volume (it only holds targets/TSDB/history):" >&2
      echo "      podman volume rm nanny-data" >&2
      exit 1
    fi
    # Install the bot's SSH key (if mounted) where nanny can read it.
    if [ -f /run/secrets/nanny_key ]; then
      install -d -m 700 -o nanny -g nanny /home/nanny/.ssh
      install -m 600 -o nanny -g nanny /run/secrets/nanny_key /home/nanny/.ssh/id_ed25519
    fi
  else
    # Non-root (e.g. --userns=keep-id): we can't fix ownership ourselves.
    if ! test -w /data/prometheus 2>/dev/null; then
      echo "nanny: FATAL — running as uid $(id -u) (non-root) and /data isn't writable." >&2
      echo "  Drop '--userns=keep-id' from your run command so the container starts" >&2
      echo "  as root and can set up /data — OR map yourself onto the app user with" >&2
      echo "  '--userns=keep-id:uid=10001,gid=10001'." >&2
      exit 1
    fi
  fi
}

run_as() {
  if [ "$(id -u)" = "0" ]; then exec gosu nanny env HOME=/home/nanny "$@"; else exec "$@"; fi
}

case "${1:-bundle}" in
  bundle|supervisor|"")
    if [ "$(id -u)" != "0" ]; then
      echo "nanny: FATAL — the bundle must start as root inside the container so" >&2
      echo "  supervisord can open service logs and drop each one to the nanny user." >&2
      echo "  Remove '--userns=keep-id' from your run command." >&2
      exit 1
    fi
    ensure_data_writable
    : "${ALERT_CHANNEL:=#alerts}"
    export PAGERDUTY_ROUTING_KEY SLACK_WEBHOOK_URL ALERT_CHANNEL
    envsubst '${PAGERDUTY_ROUTING_KEY} ${SLACK_WEBHOOK_URL} ${ALERT_CHANNEL}' \
      < /etc/nanny/alertmanager.yml.tmpl > /tmp/alertmanager.yml
    chmod 0644 /tmp/alertmanager.yml    # root writes it; nanny (alertmanager) reads it
    # supervisord stays root so it can open /dev/stdout for child logs AND drop
    # each program to user=nanny (see supervisord.conf). Children run as nanny.
    exec supervisord -c /etc/nanny/supervisord.conf
    ;;
  server|webhook|monitor|discover|report|demo)
    ensure_data_writable
    run_as python /app/nanny.py "$@"
    ;;
  run|shell|console|selftest|discover-logs|install|uninstall|panel|tui)
    run_as python /app/nanny.py "$@"
    ;;
  *)
    run_as "$@"
    ;;
esac
