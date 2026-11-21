"""
Build Monitoring Service (cli/services/monitor.py)
Collects the remote build dashboard, follows logs, scans compiler errors and
stops an active build.  Mirrors the ``view_build_logs`` / ``stop_build``
behaviour of the original shell menu while talking to the VM over multiplexed
SSH instead of repeatedly calling the GCP API.
"""

from __future__ import annotations

import shlex

from cli.services.vm import vm_exec, vm_exec_tty, is_vm_running

# Every status/log lives at the root of one of the three build mounts.
STATUS_PATHS = (
    "/mnt/a16/build.status",
    "/mnt/a17/build.status",
    "/mnt/recovery/build.status",
)
LOG_PATHS = (
    "/mnt/a16/build.log",
    "/mnt/a17/build.log",
    "/mnt/recovery/build.log",
)

_STATUS_SCRIPT = r"""
if ! mountpoint -q /mnt/a16 && ! mountpoint -q /mnt/a17 && ! mountpoint -q /mnt/recovery; then
  for d in /dev/disk/by-id/google-rom-general /dev/disk/by-id/google-rom-*; do
    if [ -b "$d" ]; then
      mkdir -p /mnt/a16
      sudo mount -o discard,defaults,noatime,nofail "$d" /mnt/a16 2>/dev/null || true
      if [ -f /mnt/a16/.last_mount ]; then
        LMP=$(cat /mnt/a16/.last_mount 2>/dev/null | tr -d '\r\n ')
        if [ -n "$LMP" ] && [ "$LMP" != "/mnt/a16" ]; then
          sudo umount /mnt/a16 2>/dev/null || true
          mkdir -p "$LMP"
          sudo mount -o discard,defaults,noatime,nofail "$d" "$LMP" 2>/dev/null || true
        fi
      fi
      break
    fi
  done
fi

ST=""
STATUS_FILE=""
for f in $(ls -t /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status 2>/dev/null); do
  [ -f "$f" ] && STATUS_FILE="$f" && break
done
BUILD_DIR=""
LOG=""
if [ -n "$STATUS_FILE" ]; then
  BUILD_DIR=$(dirname "$STATUS_FILE")
  ST=$(cat "$STATUS_FILE" 2>/dev/null)
  if [ -s "$BUILD_DIR/build.log" ]; then
    LOG="$BUILD_DIR/build.log"
  elif [ -s "$BUILD_DIR/build.log.failed" ]; then
    LOG="$BUILD_DIR/build.log.failed"
  elif [ -s "$BUILD_DIR/build.log.prev" ]; then
    LOG="$BUILD_DIR/build.log.prev"
  fi
else
  LOG=$(ls -t /mnt/a16/build.log /mnt/a16/build.log.failed /mnt/a17/build.log /mnt/a17/build.log.failed /mnt/recovery/build.log 2>/dev/null | head -n 1)
  [ -n "$LOG" ] && BUILD_DIR=$(dirname "$LOG")
  ST="NO_BUILD_RECORDED"
fi
[ -z "$ST" ] && ST="NO_BUILD_RECORDED"

# Bracket the first character of each pattern so this command line itself does
# not count as a running compiler process.
PROCS_COUNT=$(pgrep -af "([s]oong_ui|[s]iso|[n]inja|[c]kati|[r]om_build[.]sh|[g]cp_build_script[.]sh)" 2>/dev/null | wc -l)

WD="INACTIVE"
[ -f /run/idle_watchdog.status ] && WD=$(cat /run/idle_watchdog.status)

_sum_net() {
  rx=0; tx=0
  while IFS=: read -r iface line; do
    [ -z "$line" ] && continue
    iface=$(printf "%s" "$iface" | tr -d " ")
    case "$iface" in
      ""|lo|veth*|docker*|br-*|virbr*) continue ;;
    esac
    set -- $line
    rx=$((rx + ${1:-0}))
    tx=$((tx + ${9:-0}))
  done < /proc/net/dev
  echo "$rx $tx"
}
read -r R1 T1 <<EOF_N1
$(_sum_net)
EOF_N1
sleep 1
read -r R2 T2 <<EOF_N2
$(_sum_net)
EOF_N2
NET_SPEED=$(awk -v dr="$((R2 - R1))" -v dt="$((T2 - T1))" "function fmt(b){if(b>=1048576)return sprintf(\"%.1f MB/s\",b/1048576);if(b>=1024)return sprintf(\"%.0f KB/s\",b/1024);return sprintf(\"%.0f B/s\",b)}BEGIN{if(dr<0)dr=0;if(dt<0)dt=0;printf \"DL %s | UL %s\",fmt(dr),fmt(dt)}" 2>/dev/null || echo "N/A")

LOG_SIZE="N/A"
PROGRESS="N/A"
LAST_LINES=""
ROOT_ERR=""
if [ -n "$LOG" ] && [ -f "$LOG" ]; then
  LOG_SIZE=$(du -h "$LOG" 2>/dev/null | cut -f1)
  # PTY/typescript logs encode progress with ANSI cursor moves, not newlines.
  # Strip colours and cap width so a single "line" cannot flood the dashboard.
  PROGRESS=$(tr "\r" "\n" < "$LOG" 2>/dev/null | sed -E "s/\x1b\[[0-9;?]*[A-Za-z]//g" | grep -E "^\[ *[0-9]+%|^Syncing:|Fetching:|Checking out:|Checking for bloat:" | tail -n 1 | sed -E "s/ +[0-9]+:[0-9]{2} .*//" | cut -c1-220)
  LAST_LINES=$(tr "\r" "\n" < "$LOG" 2>/dev/null | sed -E "s/\x1b\[[0-9;?]*[A-Za-z]//g" | grep -v "^$" | tail -n 20 | cut -c1-300)
  ROOT_ERR=$(tr "\r" "\n" < "$LOG" 2>/dev/null | sed -E "s/\x1b\[[0-9;?]*[A-Za-z]//g" | grep -iE "FAILED:|ninja: build stopped|error:" | tail -n 3 | cut -c1-300)
fi

TARGET_LABEL=""
if [ -n "$BUILD_DIR" ] && [ -f "$BUILD_DIR/.gcp_build_target" ]; then
  TARGET_LABEL=$(cat "$BUILD_DIR/.gcp_build_target" 2>/dev/null)
fi

echo "===META_START==="
echo "STATUS: $ST"
echo "STATUS_FILE: $STATUS_FILE"
echo "BUILD_DIR: $BUILD_DIR"
echo "TARGET: $TARGET_LABEL"
echo "PROCS_COUNT: $PROCS_COUNT"
echo "WATCHDOG: $WD"
echo "NET_SPEED: $NET_SPEED"
echo "PROGRESS: $PROGRESS"
echo "LOG_PATH: $LOG"
echo "LOG_SIZE: $LOG_SIZE"
echo "===META_END==="
echo "===ERROR_START==="
echo "$ROOT_ERR"
echo "===ERROR_END==="
echo "===LOG_START==="
echo "$LAST_LINES"
echo "===LOG_END==="
"""


def _parse_sections(raw: str) -> dict:
    """Parse the marker-separated output produced by the remote script."""
    data = {
        "status": "UNKNOWN", "status_file": "", "build_dir": "", "target": "",
        "procs": "0", "watchdog": "UNKNOWN", "net_speed": "N/A",
        "progress": "N/A", "log_path": "", "log_size": "N/A",
        "errors": [], "tail": [],
    }
    meta: dict[str, str] = {}
    errors: list[str] = []
    tail: list[str] = []
    section = ""
    for raw_line in (raw or "").splitlines():
        line = raw_line.rstrip("\r\n")
        if line == "===META_START===":
            section = "meta"
            continue
        if line == "===META_END===":
            section = ""
            continue
        if line == "===ERROR_START===":
            section = "errors"
            continue
        if line == "===ERROR_END===":
            section = ""
            continue
        if line == "===LOG_START===":
            section = "tail"
            continue
        if line == "===LOG_END===":
            section = ""
            continue
        if section == "meta":
            if ": " in line:
                key, value = line.split(": ", 1)
                meta[key.strip()] = value.strip()
        elif section == "errors" and line.strip():
            errors.append(line)
        elif section == "tail":
            tail.append(line)

    data["status"] = meta.get("STATUS", "UNKNOWN") or "UNKNOWN"
    data["status_file"] = meta.get("STATUS_FILE", "")
    data["build_dir"] = meta.get("BUILD_DIR", "")
    data["target"] = meta.get("TARGET", "")
    data["procs"] = meta.get("PROCS_COUNT", "0") or "0"
    data["watchdog"] = meta.get("WATCHDOG", "INACTIVE") or "INACTIVE"
    data["net_speed"] = meta.get("NET_SPEED", "N/A") or "N/A"
    data["progress"] = meta.get("PROGRESS", "N/A") or "N/A"
    data["log_path"] = meta.get("LOG_PATH", "")
    data["log_size"] = meta.get("LOG_SIZE", "N/A") or "N/A"
    data["errors"] = errors
    data["tail"] = tail
    try:
        data["proc_count"] = int(str(data["procs"]).strip() or 0)
    except ValueError:
        data["proc_count"] = 0
    return data


def collect_build_status(vm: dict) -> dict:
    """Fetch and parse the full build dashboard in one SSH round-trip."""
    if not is_vm_running(vm):
        return _parse_sections("")
    res = vm_exec(vm, _STATUS_SCRIPT, check=False, timeout=60)
    return _parse_sections(res.stdout or "")


def get_active_log_path(vm: dict) -> str:
    """Return the log belonging to the newest build status file or latest log file."""
    script = (
        'if ! mountpoint -q /mnt/a16 && ! mountpoint -q /mnt/a17 && ! mountpoint -q /mnt/recovery; then '
        'for d in /dev/disk/by-id/google-rom-general /dev/disk/by-id/google-rom-*; do '
        'if [ -b "$d" ]; then mkdir -p /mnt/a16; sudo mount -o discard,defaults,noatime,nofail "$d" /mnt/a16 2>/dev/null || true; break; fi; done; '
        'fi; '
        'for f in $(ls -t /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status 2>/dev/null); do '
        'd=$(dirname "$f"); '
        'if [ -s "$d/build.log" ]; then echo "$d/build.log"; exit 0; '
        'elif [ -s "$d/build.log.failed" ]; then echo "$d/build.log.failed"; exit 0; '
        'elif [ -s "$d/build.log.prev" ]; then echo "$d/build.log.prev"; exit 0; '
        'fi; done; '
        'ls -t /mnt/a16/build.log /mnt/a16/build.log.failed /mnt/a17/build.log /mnt/recovery/build.log 2>/dev/null | head -n 1'
    )
    res = vm_exec(vm, script, check=False, timeout=20)
    lines = (res.stdout or "").replace("\r", "").strip().splitlines()
    return lines[-1].strip() if lines else ""


def list_available_build_logs(vm: dict) -> list[str]:
    """Return a list of all existing build logs across VM mounts."""
    script = (
        'if ! mountpoint -q /mnt/a16 && ! mountpoint -q /mnt/a17 && ! mountpoint -q /mnt/recovery; then '
        'for d in /dev/disk/by-id/google-rom-general /dev/disk/by-id/google-rom-*; do '
        'if [ -b "$d" ]; then mkdir -p /mnt/a16; sudo mount -o discard,defaults,noatime,nofail "$d" /mnt/a16 2>/dev/null || true; break; fi; done; '
        'fi; '
        'ls -t /mnt/a16/build.log /mnt/a16/build.log.failed /mnt/a16/build.log.prev /mnt/a16/logs/build*.log '
        '/mnt/a17/build.log /mnt/a17/build.log.failed /mnt/a17/build.log.prev /mnt/a17/logs/build*.log '
        '/mnt/recovery/build.log /mnt/recovery/build.log.failed /mnt/recovery/build.log.prev /mnt/recovery/logs/build*.log 2>/dev/null || true'
    )
    res = vm_exec(vm, script, check=False, timeout=20)
    return [
        l.strip() for l in (res.stdout or "").replace("\r", "").splitlines()
        if l.strip() and (l.strip().endswith(".log") or l.strip().endswith(".failed") or l.strip().endswith(".prev"))
    ]


def get_metadata_build_status(vm: dict) -> dict:
    """Read last known build status & error from GCP instance metadata (works while VM is offline)."""
    if not vm or not vm.get("instance") or not vm.get("zone"):
        return {}
    import json
    from cli.services.vm import run_gcloud
    args = [
        "compute", "instances", "describe", vm["instance"],
        f"--zone={vm['zone']}",
        "--format=json",
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    res = run_gcloud(args, check=False, timeout=15)
    if res.returncode != 0 or not res.stdout:
        return {}
    try:
        info = json.loads(res.stdout)
    except Exception:
        return {}
    out = {}
    for item in info.get("metadata", {}).get("items", []) or []:
        k = item.get("key")
        v = item.get("value") or ""
        if k in ("last-build-status", "last-build-error", "last-build-timestamp", "upload-links"):
            out[k] = v
    return out


def write_metadata_build_status(vm: dict, status: str, error: str = "") -> bool:
    """Save build status & root error to GCP instance metadata from the Mac."""
    if not vm or not vm.get("instance") or not vm.get("zone") or not status:
        return False
    import tempfile
    import time
    from cli.services.vm import run_gcloud
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    args = [
        "compute", "instances", "add-metadata", vm["instance"],
        f"--zone={vm['zone']}",
        f"--metadata=last-build-status={status.strip()},last-build-timestamp={stamp}",
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")

    tf_path = None
    try:
        if error:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
                tf.write(error.strip() + "\n")
                tf_path = tf.name
            args.append(f"--metadata-from-file=last-build-error={tf_path}")
        res = run_gcloud(args, check=False, timeout=20)
        return res.returncode == 0
    except Exception:
        return False
    finally:
        if tf_path:
            try:
                Path(tf_path).unlink(missing_ok=True)
            except OSError:
                pass


def follow_live_logs(vm: dict, lines: int = 400, log_path: str = "") -> int:
    """Tail a build log in realtime until the user presses Ctrl+C."""
    if not is_vm_running(vm):
        print(">>> VM is offline.")
        return 1
    path = log_path or get_active_log_path(vm)
    if not path:
        print(">>> No build log found.")
        return 1
    print(f">>> Following {path} in realtime (last {lines} lines, Ctrl+C to stop)\n")
    cmd = f"tail -n {int(lines)} -F -- {shlex.quote(path)}"
    return vm_exec_tty(vm, cmd)


def view_log_snapshot(vm: dict, log_path: str = "") -> int:
    """Open a static build log in a scrollable pager."""
    if not is_vm_running(vm):
        print(">>> VM is offline.")
        return 1
    path = log_path or get_active_log_path(vm)
    if not path:
        print(">>> No build log found.")
        return 1
    quoted = shlex.quote(path)
    cmd = (
        f"if [ -f {quoted} ]; then tr '\\r' '\\n' < {quoted} | "
        "sed -E 's/\\x1b\\[[0-9;?]*[A-Za-z]//g' | less -R --mouse +G; "
        "else echo 'No log file found.'; fi"
    )
    return vm_exec_tty(vm, cmd)


def scan_log_for_errors(vm: dict, log_path: str = "") -> str:
    """Print a root-cause summary for the newest build log."""
    if not is_vm_running(vm):
        print(">>> VM is offline.")
        return ""
    path = log_path or get_active_log_path(vm)
    if not path:
        print(">>> No build log found.")
        return ""
    quoted = shlex.quote(path)
    cmd = f"""
LOG={quoted}
[ -f "$LOG" ] || {{ echo "Error: log file not found: $LOG"; exit 1; }}
CLEAN=$(mktemp)
trap 'rm -f "$CLEAN"' EXIT
tr '\\r' '\\n' < "$LOG" | sed -E 's/\\x1b\\[[0-9;?]*[A-Za-z]//g' > "$CLEAN"
FAILED_N=$(grep -acE 'FAILED:' "$CLEAN" || true)
ERROR_N=$(grep -acE '(^|: )error:|^ERROR|^ninja: build stopped' "$CLEAN" || true)
echo ">>> 1. TARGET FAILURES (FAILED: with 12 lines of context) [$FAILED_N matches]"
echo '-----------------------------------------------------------------'
if [ "${{FAILED_N:-0}}" -gt 0 ]; then
    grep -anE -B 1 -A 12 'FAILED:' "$CLEAN" | tail -n 140
else
    echo 'No FAILED targets found in this log.'
    echo 'If the build is still running, a failure may not have occurred yet.'
fi
echo ''
echo ">>> 2. COMPILER / NINJA ROOT CAUSES [$ERROR_N matches]"
echo '-----------------------------------------------------------------'
if [ "${{ERROR_N:-0}}" -gt 0 ]; then
    grep -anE -B 1 -A 6 '(^|: )error:|^ERROR|^ninja: build stopped' "$CLEAN" | tail -n 100
else
    echo 'No explicit compiler errors found.'
fi
echo '-----------------------------------------------------------------'
"""
    res = vm_exec(vm, cmd, check=False, timeout=90)
    output = (res.stdout or "").strip()
    if output:
        print(output)
    return output


def stop_active_build(vm: dict) -> bool:
    """Terminate the tmux session and every compiler process on the VM."""
    if not is_vm_running(vm):
        print(">>> VM is offline; nothing to stop.")
        return True
    cmd = (
        "tmux kill-session -t rom-build 2>/dev/null || true; "
        "pkill -SIGINT -f '([n]inja|[s]oong|[r]epo|[p]ython3.*upload)' 2>/dev/null || true; "
        "sleep 1; "
        "pkill -9 -f '([n]inja|[s]oong|[r]epo)' 2>/dev/null || true; "
        "for f in /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status; do "
        "[ -f \"$f\" ] && echo \"BUILD_STATUS: CANCELLED by user - $(date)\" > \"$f\"; done; "
        "rm -f /run/gcp-build-active 2>/dev/null || true; true"
    )
    res = vm_exec(vm, cmd, check=False, timeout=30)
    return res.returncode == 0


def get_running_build_processes(vm: dict) -> str:
    """Return a short ``ps`` listing of build-related processes."""
    if not is_vm_running(vm):
        return ""
    res = vm_exec(
        vm,
        "ps aux | grep -E '([n]inja|[s]oong|[r]epo|[p]ython3.*upload|build/envsetup|make)' "
        "| grep -v grep || echo 'None (No active compiler processes).'",
        check=False,
        timeout=20,
    )
    return (res.stdout or "").strip()
