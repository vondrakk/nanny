"""nanny.hostlogs — peeled from nanny.core (Phase 2)."""

import os
import json
import shlex
import subprocess
from nanny.config import *
from nanny.util import *
from nanny.log import *

__all__ = ['WRAPPER_TEMPLATE', '_provision_script', 'LOG_DISCOVERY_CMD', 'discover_host_logs', '_persist_allowlist', 'discover_logs', 'install', '_deprovision_script', 'uninstall', 'fetch_logs']


def fetch_logs(host: str, path: str, pattern: str | None = None, lines: int = 200) -> str:
    # Guardrail: refuse anything not on the effective allowlist before SSH.
    allow = _log_allowlist()
    if host not in allow or path not in allow[host]:
        return f"DENIED: {host}:{path} is not on the allowlist."
    lines = max(1, min(int(lines), MAX_LOG_LINES))

    # The forced-command wrapper on the host reads SSH_ORIGINAL_COMMAND and
    # expects: "<lines> <path> [pattern]". It re-validates everything itself,
    # so this is a structured request, not a shell command.
    parts = [str(lines), path] + ([pattern] if pattern else [])
    remote = " ".join(shlex.quote(p) for p in parts)

    # SSH as the least-privilege, read-only service account created by `install`.
    # Even if these creds leak, the host-side wrapper limits this key to reading
    # allowlisted log files — nothing else. BatchMode avoids interactive prompts
    # (important in a container) so a bad host key fails fast instead of hanging.
    try:
        out = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             f"{SVC_USER}@{host}", remote],
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout or out.stderr or "(no output)"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT reading {host}:{path}"


WRAPPER_TEMPLATE = '''#!/usr/bin/env python3
"""nanny log reader — pinned as the forced command for the {svc} SSH key.
Reads SSH_ORIGINAL_COMMAND: "<lines> <path> [pattern]". READ-ONLY."""
import os, sys, shlex, subprocess

ALLOWED = {allowed!r}
MAX_LINES = {max_lines}

req = os.environ.get("SSH_ORIGINAL_COMMAND", "")
try:
    parts = shlex.split(req)
except ValueError:
    sys.exit("nanny: malformed request")
if len(parts) < 2:
    sys.exit("usage: <lines> <path> [pattern]")
try:
    lines = max(1, min(int(parts[0]), MAX_LINES))
except ValueError:
    sys.exit("nanny: lines must be an integer")
path = parts[1]
if path not in ALLOWED:
    sys.exit("nanny: DENIED — %s is not allowlisted" % path)
pattern = parts[2] if len(parts) > 2 else None

text = subprocess.run(["tail", "-n", str(lines), path],
                      capture_output=True, text=True).stdout
if pattern:
    text = subprocess.run(["grep", "-F", "--", pattern],
                          input=text, capture_output=True, text=True).stdout
sys.stdout.write(text)
'''


def _provision_script(pubkey: str, paths: list[str]) -> str:
    """Bash run on the host (via sudo) to create the locked-down account."""
    wrapper = WRAPPER_TEMPLATE.format(svc=SVC_USER, allowed=paths, max_lines=MAX_LOG_LINES)
    # authorized_keys: pin the forced command and strip every other capability.
    opts = ("command=\"%s\",no-pty,no-port-forwarding,"
            "no-agent-forwarding,no-X11-forwarding,no-user-rc") % WRAPPER_PATH
    authkey = f"{opts} {pubkey.strip()}"
    # Read access for exactly the allowlisted files (best-effort ACL), plus the
    # `adm` group which conventionally reads /var/log on Debian/Ubuntu.
    acl_lines = "\n".join(
        f'setfacl -m u:{SVC_USER}:r {shlex.quote(p)} 2>/dev/null || true' for p in paths
    )
    return f"""set -euo pipefail

# 1. Create the service account if absent: real shell (forced command needs one),
#    but locked password so it can't be used for interactive/password login.
id {SVC_USER} >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash {SVC_USER}
passwd -l {SVC_USER} >/dev/null

# 2. Install the read-only wrapper, owned by root so {SVC_USER} can't edit it.
cat > {WRAPPER_PATH} <<'WRAPPER_EOF'
{wrapper}WRAPPER_EOF
chown root:root {WRAPPER_PATH}
chmod 0755 {WRAPPER_PATH}

# 3. Pin the bot key to the forced command; nothing else this key can do.
install -d -m 0700 -o {SVC_USER} -g {SVC_USER} /home/{SVC_USER}/.ssh
cat > /home/{SVC_USER}/.ssh/authorized_keys <<'KEY_EOF'
{authkey}
KEY_EOF
chown {SVC_USER}:{SVC_USER} /home/{SVC_USER}/.ssh/authorized_keys
chmod 0600 /home/{SVC_USER}/.ssh/authorized_keys

# 4. Grant least-privilege read on the logs.
usermod -aG adm {SVC_USER} 2>/dev/null || true
{acl_lines}

echo "nanny: provisioned {SVC_USER} on $(hostname) with {len(paths)} allowlisted path(s)."
echo "NOTE: log rotation can drop file ACLs — prefer group ownership or a logrotate"
echo "      'create' rule if any allowlisted path lives outside /var/log."
"""


LOG_DISCOVERY_CMD = (
    "sudo find /var/log -maxdepth 4 -type f "
    r"\( -name '*.log' -o -name syslog -o -name messages -o -name kern.log "
    r"-o -name auth.log \) ! -name '*.[0-9]' 2>/dev/null | sort -u | head -n 200"
)


def discover_host_logs(host: str, admin_user: str, limit: int = 50) -> list:
    """SSH to the host as admin and list candidate log files under /var/log."""
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         f"{admin_user}@{host}", LOG_DISCOVERY_CMD],
        capture_output=True, text=True, timeout=30)
    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError((p.stderr or "").strip()[:200] or f"exit {p.returncode}")
    paths = [ln.strip() for ln in p.stdout.splitlines() if ln.strip().startswith("/")]
    return paths[:limit]


def _persist_allowlist(host: str, paths: list):
    """Record a host's allowlist so the running server knows the paths."""
    fp = _data_path("log_allowlist.json")
    try:
        data = json.load(open(fp))
    except (OSError, ValueError):
        data = {}
    data[host] = paths
    os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
    with open(fp, "w") as fh:
        json.dump(data, fh, indent=2)
    print(_c(f"recorded {len(paths)} path(s) for {host} in {fp}", "2"))


def discover_logs(host: str, admin_user: str):
    """Preview the log files nanny would monitor on a host (no changes made)."""
    print(_c(f"discovering logs on {host} (as {admin_user})…", "1"))
    try:
        paths = discover_host_logs(host, admin_user)
    except Exception as e:
        sys.exit(f"discover-logs: failed on {host}: {e}")
    for p in paths:
        print(f"  {p}")
    print(f"\n{len(paths)} log file(s). Run `install` to grant read access and bake "
          f"these into the host wrapper, or pass a subset with `install --logs`.")


def install(host: str, admin_user: str, pubkey_path: str, dry_run: bool,
            logs: list = None, discover: bool = True):
    """Create the read-only log account on `host`, reachable as the admin user.
    Discovers the host's logs automatically unless --logs or --no-discover-logs."""
    if logs:
        paths = logs
    elif discover:
        try:
            paths = discover_host_logs(host, admin_user)
        except Exception as e:
            sys.exit(f"install: log discovery failed on {host}: {e} "
                     f"(use --logs to set paths manually).")
        if not paths:
            sys.exit(f"install: discovered no logs on {host}; pass --logs explicitly.")
        print(_c(f"discovered {len(paths)} log file(s) on {host}:", "1"))
        for p in paths:
            print(f"  {p}")
    else:
        paths = _log_allowlist().get(host)
        if not paths:
            sys.exit(f"install: no logs for {host} — discover them or pass --logs.")

    try:
        pubkey = open(os.path.expanduser(pubkey_path)).read().strip()
    except OSError as e:
        sys.exit(f"install: can't read pubkey {pubkey_path}: {e}")
    if not pubkey.startswith(("ssh-", "ecdsa-", "sk-")):
        sys.exit("install: that doesn't look like an SSH public key.")

    script = _provision_script(pubkey, paths)
    if dry_run:
        print(f"# would run on {admin_user}@{host} via: ssh ... sudo bash -s\n")
        print(script)
        return

    print(f"Provisioning {SVC_USER} on {host} (as {admin_user}, via sudo)…")
    proc = subprocess.run(
        ["ssh", "-t", f"{admin_user}@{host}", "sudo", "bash", "-s"],
        input=script, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"install: provisioning failed on {host} (exit {proc.returncode}).")
    _persist_allowlist(host, paths)


def _deprovision_script(paths: list[str]) -> str:
    """Bash run on the host (via sudo) to cleanly remove the nanny account.

    Order matters: strip the ACL entries while the username still resolves, then
    remove the wrapper, then delete the account and its home (which carries the
    authorized_keys). Every step is idempotent — safe to re-run, and safe on a
    host that was never provisioned.
    """
    # Remove our ACL entries before userdel, so we don't leave orphaned numeric
    # UID entries behind on the log files.
    acl_lines = "\n".join(
        f'setfacl -x u:{SVC_USER} {shlex.quote(p)} 2>/dev/null || true' for p in paths
    ) or "# (no allowlisted paths known for this host — skipping ACL cleanup)"
    return f"""set -euo pipefail

# 1. Remove read ACLs we granted (while the name still resolves).
{acl_lines}

# 2. Remove the host-side wrapper.
rm -f {WRAPPER_PATH}

# 3. Delete the account and its home dir (this removes authorized_keys too).
if id {SVC_USER} >/dev/null 2>&1; then
    pkill -u {SVC_USER} 2>/dev/null || true   # nothing should be running, but be safe
    userdel -r {SVC_USER} 2>/dev/null || userdel {SVC_USER}
    echo "nanny: removed {SVC_USER} from $(hostname)."
else
    echo "nanny: {SVC_USER} not present on $(hostname) — nothing to remove."
fi
"""


def uninstall(host: str, admin_user: str, dry_run: bool):
    """Remove the read-only log account and key from `host`."""
    # Pull paths if we still know them, so ACL cleanup is precise; otherwise the
    # script just skips that step (the account removal is what matters).
    paths = _log_allowlist().get(host, [])
    script = _deprovision_script(paths)

    if dry_run:
        print(f"# would run on {admin_user}@{host} via: ssh ... sudo bash -s\n")
        print(script)
        return

    print(f"Removing {SVC_USER} from {host} (as {admin_user}, via sudo)…")
    proc = subprocess.run(
        ["ssh", "-t", f"{admin_user}@{host}", "sudo", "bash", "-s"],
        input=script, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"uninstall: removal failed on {host} (exit {proc.returncode}).")
