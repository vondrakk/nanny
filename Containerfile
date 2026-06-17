# Containerfile — nanny, the supervised on-call triage bot.
#
#   Build:   podman build -t nanny -f Containerfile .
#
#   Shell (interactive, like a local console):
#     podman run --rm -it --userns=keep-id \
#       --env-file ./nanny.env \
#       -v ~/.ssh/nanny_key:/home/nanny/.ssh/id_ed25519:ro,Z \
#       -v ~/.ssh/known_hosts:/home/nanny/.ssh/known_hosts:ro,Z \
#       nanny shell
#
#   Demo (self-contained: synthetic incidents + ntfy, no real integrations):
#     podman run --rm -p 9099:9099 \
#       -e NANNY_NTFY_TOPIC=my-nanny-demo -e NANNY_DATA_DIR=/tmp/nanny \
#       nanny demo
#     (see deploy/demo/ for a compose file + walkthrough)
#
#   Run (long-lived Slack watcher):
#     podman run -d --name nanny --restart=on-failure --userns=keep-id \
#       --env-file ./nanny.env \
#       -v ~/.ssh/nanny_key:/home/nanny/.ssh/id_ed25519:ro,Z \
#       -v ~/.ssh/known_hosts:/home/nanny/.ssh/known_hosts:ro,Z \
#       nanny run
#
#   Install the read-only log account on a host (needs an admin key, not the bot
#   key, plus the bot's PUBLIC key to deploy):
#     podman run --rm -it --userns=keep-id \
#       -v ~/.ssh/admin_key:/home/nanny/.ssh/id_ed25519:ro,Z \
#       -v ./nanny_key.pub:/keys/bot.pub:ro,Z \
#       nanny install --host web-01 --admin-user ubuntu --pubkey /keys/bot.pub

FROM python:3.12-slim

# openssh-client: needed for the read-only log fetch and for install/uninstall.
# tini: clean signal handling (graceful Ctrl-C in shell, clean stop in run mode).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The code lives in the nanny/ package; nanny.py is a thin back-compat shim that
# imports nanny.core, so both must be present. db/ holds the (optional) Postgres
# schema applied at startup when DATABASE_URL is set.
COPY nanny.py .
COPY nanny/ ./nanny/
COPY db/ ./db/

# Run as a non-root user. Home is /home/nanny, so SSH looks for keys in
# /home/nanny/.ssh — mount the bot key there at runtime (see header).
RUN useradd --create-home --uid 10001 nanny \
    && mkdir -p /home/nanny/.ssh \
    && chown -R nanny:nanny /home/nanny/.ssh \
    && chmod 700 /home/nanny/.ssh
USER nanny

# `python nanny.py <mode>` — append the mode as the run command.
ENTRYPOINT ["tini", "--", "python", "nanny.py"]
CMD ["shell"]
