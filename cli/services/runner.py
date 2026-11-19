"""
Build Dispatcher Backend Service (cli/services/runner.py)
Synchronises guest build tools and launches ROM, recovery and kernel builds in
a resilient remote tmux session.  The runner is base64-encoded and executed via
``script -qefc`` so repo/ninja retain live terminal progress while everything is
also written to ``build.log``.  Behaviour mirrors the original ``main`` menu.
"""

from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path

from cli.base import (
    CYAN, GREEN, YELLOW, RED, BOLD, NC, clear_screen, print_header, print_divider,
    is_interactive, read_input,
)
from cli.services.vm import (
    vm_exec, vm_exec_tty, vm_push, get_vm_state, is_vm_running,
    ensure_vm_online, invalidate_vm_cache,
)
from cli.services.disk import (
    resolve_build_disk, format_disk_action, unmount_disk_on_vm,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REMOTE_FARM = "~/.gcp_farm"

NET_SPEED_SCRIPT = r"""#!/usr/bin/env bash
# GCP-NET-SPEED-V3
MODE="$1"
STATE_FILE="/tmp/vm_net_speed_$(id -u).state"

read -r now_t _ < /proc/uptime || exit 0
read -r rx tx < <(awk '
  /:/ { gsub(/:/, " "); if ($1 != "lo" && $1 !~ /^(veth|docker|br-|virbr)/) { rx += $2; tx += $10; } }
  END { printf "%.0f %.0f\n", rx, tx }
' /proc/net/dev)

read -r _ u n s i w irq sirq st _ < /proc/stat
cpu_total=$((u + n + s + i + w + irq + sirq + st))
cpu_idle=$((i + w))

read -r mem_total mem_avail < <(awk '
  /^MemTotal:/ { t = $2 }
  /^MemAvailable:/ { a = $2 }
  END { printf "%.0f %.0f\n", t, a }
' /proc/meminfo)

[ -r "$STATE_FILE" ] && read -r prev_t prev_rx prev_tx prev_cpu_total prev_cpu_idle < "$STATE_FILE" 2>/dev/null || true
printf '%s %s %s %s %s\n' "$now_t" "$rx" "$tx" "$cpu_total" "$cpu_idle" > "$STATE_FILE" 2>/dev/null || true

awk -v mode="$MODE" -v now_t="$now_t" -v rx="$rx" -v tx="$tx" \
    -v prev_t="${prev_t:-0}" -v prev_rx="${prev_rx:-0}" -v prev_tx="${prev_tx:-0}" \
    -v cpu_tot="$cpu_total" -v cpu_idl="$cpu_idle" \
    -v prev_cpu_tot="${prev_cpu_total:-0}" -v prev_cpu_idl="${prev_cpu_idle:-0}" \
    -v m_tot="$mem_total" -v m_avail="$mem_avail" '
  function fmt(b) {
    if (b >= 1048576) return sprintf("%.1f MB/s", b / 1048576);
    if (b >= 1024)    return sprintf("%.0f KB/s", b / 1024);
    return sprintf("%.0f B/s", b);
  }
  BEGIN {
    d_tot = cpu_tot - prev_cpu_tot;
    d_idl = cpu_idl - prev_cpu_idl;
    if (d_tot > 0 && d_idl >= 0) {
      cpu_pct = int(100 * (d_tot - d_idl) / d_tot);
      if (cpu_pct < 0) cpu_pct = 0;
      if (cpu_pct > 100) cpu_pct = 100;
    } else {
      cpu_pct = 0;
    }

    if (m_tot > 0) {
      ram_pct = int(100 * (m_tot - m_avail) / m_tot);
    } else {
      ram_pct = 0;
    }

    dt = now_t - prev_t;
    if (prev_t <= 0 || dt <= 0 || dt > 60 || rx < prev_rx || tx < prev_tx) {
      dl_str = "DL --";
      ul_str = "UL --";
    } else {
      dl_str = sprintf("DL %s", fmt((rx - prev_rx) / dt));
      ul_str = sprintf("UL %s", fmt((tx - prev_tx) / dt));
    }

    if (mode == "--tmux") {
      if (cpu_pct >= 85) c_cpu = "#[fg=colour196,bold]";
      else if (cpu_pct >= 50) c_cpu = "#[fg=colour220,bold]";
      else c_cpu = "#[fg=colour82,bold]";

      if (ram_pct >= 85) c_ram = "#[fg=colour196,bold]";
      else if (ram_pct >= 70) c_ram = "#[fg=colour220,bold]";
      else c_ram = "#[fg=colour51,bold]";

      printf "%sCPU %d%% #[fg=colour242]| %sRAM %d%% #[fg=colour242]| #[fg=colour82,bold]%s #[fg=colour242]| #[fg=colour214,bold]%s\n",
             c_cpu, cpu_pct, c_ram, ram_pct, dl_str, ul_str;
    } else {
      printf "CPU %d%% | RAM %d%% | %s | %s\n", cpu_pct, ram_pct, dl_str, ul_str;
    }
  }
'
"""

TMUX_CONF = r"""
# GCP-TMUX-STYLE-V9: high-contrast dark footer, CPU/RAM/Net monitor, dynamic build pills & shortcuts
set -g mouse on
set -g set-clipboard on
bind-key m set-option -g mouse
bind-key -T copy-mode-vi MouseDown1Pane select-pane \; send-keys -X cancel
bind-key -T copy-mode MouseDown1Pane select-pane \; send-keys -X cancel
bind-key -T copy-mode-vi MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel
bind-key -T copy-mode MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel
set -g history-limit 50000
set -g status-interval 2
set -g status-style "bg=colour235,fg=colour252"
set -g status-left-length 60
set -g status-left "#[bg=colour39,fg=colour232,bold] #{?@build_icon,#{@build_icon},⚙} #{?@build_label,#{@build_label},BUILD} #[bg=colour238,fg=colour255,bold] #{?@build_detail,#{@build_detail},#W} #[bg=colour235] "
set -g window-status-current-format ""
set -g window-status-format ""
set -g status-right-length 160
set -g status-right "#[fg=colour245][#[fg=colour82,bold]Ctrl+b d#[fg=colour245] Detach | #[fg=colour196,bold]Ctrl+c#[fg=colour245] Stop] #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b "
"""


def _format_display_name(name: str) -> str:
    n = name.strip()
    low = n.lower()
    if low in ("evolution", "evolutionx", "evox"):
        return "Evolution X"
    if low in ("lineage", "lineageos", "los"):
        return "LineageOS"
    if low in ("crdroid", "cr"):
        return "crDroid"
    if low in ("pixel", "pixelos"):
        return "PixelOS"
    if low in ("aosp", "arrow", "derp", "paranoid"):
        return n.title()
    return n.title() if n.islower() else n


def load_kernel_config() -> dict:
    try:
        return json.loads((REPO_ROOT / "configs" / "kernel.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def net_speed_setup_cmd() -> str:
    """Shell snippet installing the net-speed helper and tmux styling."""
    net_b64 = base64.b64encode(NET_SPEED_SCRIPT.encode()).decode()
    tmux_b64 = base64.b64encode(TMUX_CONF.encode()).decode()
    return f"""if ! grep -q 'GCP-NET-SPEED-V3' /usr/local/bin/vm_net_speed.sh 2>/dev/null; then
  printf '%s' '{net_b64}' | base64 -d | sudo tee /usr/local/bin/vm_net_speed.sh >/dev/null && sudo chmod +x /usr/local/bin/vm_net_speed.sh
fi
if ! grep -q 'GCP-TMUX-STYLE-V9' /etc/tmux.conf 2>/dev/null; then
  sudo touch /etc/tmux.conf 2>/dev/null || true
  sudo sed -i '/# GCP Build Farm:/,$d;/# GCP-TMUX-STYLE-V/,$d' /etc/tmux.conf 2>/dev/null || true
  printf '%s' '{tmux_b64}' | base64 -d | sudo tee -a /etc/tmux.conf >/dev/null 2>&1 || true
fi
tmux set-option -g mouse on 2>/dev/null || true
tmux set-option -g set-clipboard on 2>/dev/null || true
tmux bind-key m set-option -g mouse 2>/dev/null || true
tmux bind-key -T copy-mode-vi MouseDown1Pane select-pane \\; send-keys -X cancel 2>/dev/null || true
tmux bind-key -T copy-mode MouseDown1Pane select-pane \\; send-keys -X cancel 2>/dev/null || true
tmux bind-key -T copy-mode-vi MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel 2>/dev/null || true
tmux bind-key -T copy-mode MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel 2>/dev/null || true
tmux set-option -g history-limit 50000 2>/dev/null || true
tmux set-option -g status-interval 2 2>/dev/null || true
tmux set-option -g status-style 'bg=colour235,fg=colour252' 2>/dev/null || true
tmux set-option -g status-left-length 60 2>/dev/null || true
tmux set-option -g status-left '#[bg=colour39,fg=colour232,bold] #{{?@build_icon,#{{@build_icon}},⚙}} #{{?@build_label,#{{@build_label}},BUILD}} #[bg=colour238,fg=colour255,bold] #{{?@build_detail,#{{@build_detail}},#W}} #[bg=colour235] ' 2>/dev/null || true
tmux set-window-option -g window-status-current-format '' 2>/dev/null || true
tmux set-window-option -g window-status-format '' 2>/dev/null || true
tmux set-option -g status-right-length 160 2>/dev/null || true
tmux set-option -g status-right '#[fg=colour245][#[fg=colour82,bold]Ctrl+b d#[fg=colour245] Detach | #[fg=colour196,bold]Ctrl+c#[fg=colour245] Stop] #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b ' 2>/dev/null || true
tmux set-option -u -t rom-build mouse 2>/dev/null || true
tmux set-option -t rom-build history-limit 50000 2>/dev/null || true
tmux set-option -t rom-build status-interval 2 2>/dev/null || true
tmux set-option -t rom-build status-style 'bg=colour235,fg=colour252' 2>/dev/null || true
tmux set-option -t rom-build status-left-length 60 2>/dev/null || true
tmux set-window-option -t rom-build window-status-current-format '' 2>/dev/null || true
tmux set-window-option -t rom-build window-status-format '' 2>/dev/null || true
if ! tmux show-option -t rom-build status-left 2>/dev/null | grep -q 'bg=colour'; then
  tmux set-option -t rom-build status-left '#[bg=colour39,fg=colour232,bold] #{{?@build_icon,#{{@build_icon}},⚙}} #{{?@build_label,#{{@build_label}},BUILD}} #[bg=colour238,fg=colour255,bold] #{{?@build_detail,#{{@build_detail}},#W}} #[bg=colour235] ' 2>/dev/null || true
fi
tmux set-option -t rom-build status-right-length 160 2>/dev/null || true
tmux set-option -t rom-build status-right '#[fg=colour245][#[fg=colour82,bold]Ctrl+b d#[fg=colour245] Detach | #[fg=colour196,bold]Ctrl+c#[fg=colour245] Stop] #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b ' 2>/dev/null || true
"""


def attach_tmux_session(vm: dict) -> int:
    """Attach to the live build session, installing tmux styling first."""
    return vm_exec_tty(vm, net_speed_setup_cmd() + "\ntmux attach-session -t rom-build || tmux ls")


def check_build_slot(vm: dict) -> bool:
    """Refuse to replace an already running remote build."""
    state, _ = get_vm_state(vm)
    if state in ("TERMINATED", "STOPPED", "SUSPENDED"):
        return True
    if state != "RUNNING":
        print(f"{RED}[ERROR] Could not verify the GCP VM state ({state}); refusing to start another build.{NC}")
        return False

    cmd = r"""
active=$(ps -eo pid=,etime=,args= | grep -E "[g]cp_build_script|[s]oong_ui .*--build-mode|[/]build/soong/bin/m .*(recoveryimage|otapackage|bacon|droid|bootimage)|[r]epo sync|[n]inja|[c]kati" || true)
if [ -e /run/gcp-build-active ]; then
    active="$active
Build activity marker: /run/gcp-build-active"
fi
if [ -n "$active" ]; then
    echo BUILD_ALREADY_RUNNING
    echo "$active"
    for f in /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status; do
        [ -f "$f" ] && echo "$f: $(cat "$f")"
    done
else
    echo BUILD_SLOT_FREE
fi
"""
    res = vm_exec(vm, cmd, check=False, timeout=45)
    output = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0 or "BUILD_SLOT_FREE" not in output:
        if "BUILD_ALREADY_RUNNING" in output:
            print(f"{RED}[ERROR] A build is already running on the GCP VM. The new build was not started, "
                  f"and the running session was left untouched.{NC}")
            for line in output.splitlines():
                if line.strip() and line.strip() != "BUILD_ALREADY_RUNNING":
                    print(f"  {line}")
            print("       View it with: Monitoring -> Build Status & Logs.")
        else:
            print(f"{RED}[ERROR] Could not check whether a build is already running. "
                  f"Refusing to replace the VM build session.{NC}")
            if output.strip():
                print(output.strip())
        return False
    return True


def sync_build_tools_to_vm(vm: dict) -> bool:
    """Sync scripts/ and configs/ into ~/.gcp_farm and refresh ~/upload.py."""
    print(">>> Syncing build tools and configs to VM...", end="", flush=True)
    vm_exec(vm, f"mkdir -p {REMOTE_FARM} && rm -rf {REMOTE_FARM}/scripts {REMOTE_FARM}/configs",
            check=False, timeout=30)

    ok = True
    for folder in ("scripts", "configs"):
        local = REPO_ROOT / folder
        if local.is_dir():
            res = vm_push(vm, [local], f"{REMOTE_FARM}/", recursive=True)
            if res.returncode != 0:
                ok = False
                print(f"\n>>> Warning: could not sync {folder}: {(res.stderr or res.stdout).strip()}")

    uploader = REPO_ROOT / "upload.py"
    if uploader.is_file():
        vm_push(vm, [uploader], "~/upload.py")
    uploader = REPO_ROOT / "clean.py"
    if uploader.is_file():
        vm_push(vm, [uploader], "~/clean.py")

    print(" Done." if ok else " Partial.", flush=True)
    return ok


def deploy_watchdog(vm: dict) -> bool:
    """Upload idle_watchdog.sh and install/restart its systemd unit."""
    if not ensure_vm_online(vm):
        return False
    script = REPO_ROOT / "idle_watchdog.sh"
    if script.is_file():
        print(">>> Updating idle-shutdown watchdog...", end="", flush=True)
        res = vm_push(vm, [script], "/tmp/idle_watchdog.sh")
        if res.returncode == 0:
            vm_exec(vm, "sudo mv /tmp/idle_watchdog.sh /usr/local/bin/idle_watchdog.sh "
                        "&& sudo chmod +x /usr/local/bin/idle_watchdog.sh", check=False, timeout=30)
        print(" Done.", flush=True)

    unit = """[Unit]
Description=GCP Build Farm Auto-Idle Shutdown Watchdog (30 min)
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/idle_watchdog.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    unit_b64 = base64.b64encode(unit.encode()).decode()
    res = vm_exec(
        vm,
        f"printf '%s' '{unit_b64}' | base64 -d | sudo tee /etc/systemd/system/idle-shutdown.service >/dev/null "
        "&& sudo systemctl daemon-reload && sudo systemctl enable --now idle-shutdown.service "
        "&& sudo systemctl restart idle-shutdown.service",
        check=False, timeout=60,
    )
    if res.returncode == 0:
        print(">>> Auto-Idle Shutdown Watchdog successfully active (30 min threshold).")
        return True
    print(f">>> Warning: could not enable watchdog: {(res.stderr or res.stdout).strip()}")
    return False


def _runner_script(vm: dict, command: str, branch: str, mount_point: str, variant: str,
                   sf_upload: bool, gofile_upload: bool, auto_shutdown: bool,
                   build_label: str, win_label: str, device: str,
                   target_label: str = "", detail_label: str = "") -> str:
    net_setup = net_speed_setup_cmd()
    q_mp = shlex.quote(mount_point)
    q_status = shlex.quote(f"{mount_point}/build.status")
    q_log = shlex.quote(f"{mount_point}/build.log")
    q_target = shlex.quote(f"{mount_point}/.gcp_build_target")
    marker = target_label or branch

    return f"""#!/usr/bin/env bash
export BUILD_VARIANT={shlex.quote(variant)}
export SF_UPLOAD={'true' if sf_upload else 'false'}
export SKIP_GOFILE={'false' if gofile_upload else 'true'}
export AUTO_SHUTDOWN=false
export WRAPPER_SHUTDOWN={'true' if auto_shutdown else 'false'}
export DEVICE={shlex.quote(device)}
{net_setup}
export CCACHE_MAXSIZE=15G
readonly CCACHE_MAXSIZE 2>/dev/null || true
ccache -M 15G >/dev/null 2>&1 || true
ccache -o max_size=15G >/dev/null 2>&1 || true
BOOT_AVAIL_MB=$(df -Pm / | awk 'NR==2{{print $4}}')
if [ -n "$BOOT_AVAIL_MB" ] && [ "$BOOT_AVAIL_MB" -lt 3072 ] 2>/dev/null; then
    echo ">>> Boot disk only ${{BOOT_AVAIL_MB}}MB free: clearing ccache before build..."
    ccache -C >/dev/null 2>&1 || rm -rf "$HOME/.ccache"
fi
tmux set-option -t rom-build @build_label {shlex.quote(build_label)} 2>/dev/null || true
tmux set-option -t rom-build @build_detail {shlex.quote(detail_label)} 2>/dev/null || true
tmux set-option -t rom-build @build_icon "⚙" 2>/dev/null || true
tmux set-option -t rom-build status-left-length 60 2>/dev/null || true
tmux set-window-option -t rom-build window-status-current-format "" 2>/dev/null || true
tmux set-window-option -t rom-build window-status-format "" 2>/dev/null || true
tmux set-option -t rom-build status-left "#[bg=colour39,fg=colour232,bold] ⚙ {build_label} #[bg=colour238,fg=colour255,bold] {detail_label} #[bg=colour235] " 2>/dev/null || true
tmux rename-window {shlex.quote(win_label)} 2>/dev/null || true
sudo touch /run/gcp-build-active 2>/dev/null || true
sudo rm -f /run/gcp-build-shutdown-pending 2>/dev/null || true
sudo mkdir -p {q_mp}
echo {shlex.quote(marker)} > {q_target} 2>/dev/null || true
if [ {shlex.quote(branch)} = 'recovery' ] && [ {q_mp} != '/mnt/recovery' ]; then
    sudo mkdir -p {q_mp}/recovery && sudo ln -sfn {q_mp}/recovery /mnt/recovery
fi
echo "BUILD_STATUS: RUNNING ($BUILD_VARIANT) - $(date)" > {q_status} 2>/dev/null || true

stty -ixon -ixoff 2>/dev/null || true
stty stop undef 2>/dev/null || true
stty start undef 2>/dev/null || true

BUILD_SCRIPT_TMP="/tmp/gcp_build_script.py"
if [ -s ~/.gcp_farm/scripts/build.py ]; then
    cp ~/.gcp_farm/scripts/build.py "$BUILD_SCRIPT_TMP"
    mkdir -p /tmp/configs /tmp/scripts
    cp -r ~/.gcp_farm/configs/* /tmp/configs/ 2>/dev/null || true
    cp -r ~/.gcp_farm/scripts/* /tmp/scripts/ 2>/dev/null || true
    BUILD_EXIT=0
else
    echo ">>> [ERROR] Remote build engine ~/.gcp_farm/scripts/build.py is missing" | tee {q_log}
    BUILD_EXIT=1
fi

if [ "$BUILD_EXIT" -eq 0 ]; then
    if command -v script >/dev/null 2>&1; then
        script -qefc "stdbuf -oL -eL python3 $BUILD_SCRIPT_TMP {command}" {q_log}
        BUILD_EXIT=$?
    else
        set -o pipefail
        stdbuf -oL -eL python3 "$BUILD_SCRIPT_TMP" {command} 2>&1 | tee {q_log}
        BUILD_EXIT=$?
    fi
fi

if [ "$BUILD_EXIT" -eq 0 ]; then
    tmux set-option -t rom-build @build_icon "✓" 2>/dev/null || true
    tmux set-option -t rom-build status-left "#[bg=colour82,fg=colour232,bold] ✓ {build_label} #[bg=colour238,fg=colour255,bold] {detail_label} #[bg=colour235] " 2>/dev/null || true
    echo "BUILD_STATUS: SUCCESS ($BUILD_VARIANT) - $(date)" > {q_status} 2>/dev/null || true
    echo -e '\\n\\033[1;32m============================================================\\033[0m'
    echo -e '\\033[1;32m [SUCCESS] Build and upload completed successfully!\\033[0m'
    echo -e '\\033[1;32m============================================================\\033[0m'
else
    tmux set-option -t rom-build @build_icon "✗" 2>/dev/null || true
    tmux set-option -t rom-build status-left "#[bg=colour196,fg=colour255,bold] ✗ {build_label} #[bg=colour238,fg=colour255,bold] {detail_label} #[bg=colour235] " 2>/dev/null || true
    echo "BUILD_STATUS: FAILED (Exit Code $BUILD_EXIT) - $(date)" > {q_status} 2>/dev/null || true
    echo -e '\\n\\033[1;31m============================================================\\033[0m'
    echo -e "\\033[1;31m [ERROR] Build stopped or failed with exit code $BUILD_EXIT!\\033[0m"
    echo -e '\\033[1;31m============================================================\\033[0m'
    echo "Full build output is saved at: {mount_point}/build.log"
fi

sudo rm -f /run/gcp-build-active 2>/dev/null || true

if [ "$WRAPPER_SHUTDOWN" = 'true' ] && [ "$BUILD_EXIT" -eq 0 ]; then
    sudo rm -f /run/gcp-build-shutdown-pending 2>/dev/null || true
    echo ""
    echo -e '\\033[1;33m>>> Build succeeded! Auto-shutdown requested: Powering off VM in 15 seconds...\\033[0m'
    echo -e '\\033[1;33m>>> Press any key to cancel shutdown and keep session open...\\033[0m'
    if read -t 15 -n 1 -s -r; then
        echo -e '\\n\\033[1;32m>>> Auto-shutdown cancelled by user. Session will remain open.\\033[0m'
        date +%s | sudo tee /run/gcp-build-shutdown-pending >/dev/null 2>&1 || true
    else
        echo -e '\\n>>> Powering off now.'
        sudo poweroff || true
        exit 0
    fi
else
    date +%s | sudo tee /run/gcp-build-shutdown-pending >/dev/null 2>&1 || true
    echo ""
    if [ "$WRAPPER_SHUTDOWN" = 'true' ] && [ "$BUILD_EXIT" -ne 0 ]; then
        echo -e '\\033[1;33m>>> [NOTICE] Immediate power-off skipped because the build failed.\\033[0m'
        echo -e '\\033[1;36m>>> VM will remain alive for debugging and automatically power off after 30 minutes of idle.\\033[0m'
    else
        echo -e '\\033[1;36m>>> VM will remain alive and automatically power off after 30 minutes of idle.\\033[0m'
    fi
fi

echo ""
echo -e '\\033[1;36m============================================================\\033[0m'
echo -e "\\033[1;36m Build session finished (Exit code: $BUILD_EXIT). Session remains open.\\033[0m"
echo -e '\\033[1;36m============================================================\\033[0m'
echo -e '\\nPress [Enter] to enter interactive bash prompt (or Ctrl+B then D to detach)...'
read -t 30 -r || true
exec bash
"""


def _general_disk_data_prompt(vm: dict, target_disk: str, mount_point: str, branch: str,
                              label: str = "") -> None:
    if not is_interactive():
        return
    info = vm_exec(
        vm,
        f"PREV=''; [ -f {shlex.quote(mount_point)}/.gcp_build_target ] && "
        f"PREV=$(cat {shlex.quote(mount_point)}/.gcp_build_target 2>/dev/null | tr -d '\\r'); "
        f"USED=$(df -h {shlex.quote(mount_point)} | awk 'NR==2{{print $3\" / \"$2\" (Free: \"$4\")\"}}'); "
        f"DIRS=$(ls -1A {shlex.quote(mount_point)} 2>/dev/null | grep -vx 'lost+found' | "
        f"grep -vx '.gcp_build_target' | head -n 6 | tr '\\n' ' '); "
        'echo "PREV=$PREV|USED=$USED|DIRS=$DIRS"',
        check=False, timeout=30,
    )
    raw = (info.stdout or "").strip()
    prev = raw.split("PREV=", 1)[1].split("|", 1)[0] if "PREV=" in raw else ""
    used = raw.split("USED=", 1)[1].split("|", 1)[0] if "USED=" in raw else ""
    dirs = raw.split("DIRS=", 1)[1].strip() if "DIRS=" in raw else ""
    if not dirs or prev == branch:
        return
    display = label or branch
    print(f"\n{YELLOW}>>> Notice: General disk ({target_disk}) at {mount_point} contains existing data!{NC}")
    if prev:
        print(f"    Previous Target : {BOLD}{prev}{NC} (New: {BOLD}{display}{NC})")
    if used:
        print(f"    Disk Usage      : {used}")
    print(f"    Top Entries     : {dirs}")
    answer = read_input(f" Format '{target_disk}' clean (ext4) before starting '{display}' build? [y/N, default: N]: ")
    if answer.strip().lower() in ("y", "yes"):
        if format_disk_action(vm, target_disk, auto_yes=True):
            from cli.services.disk import mount_disk_on_vm
            mount_disk_on_vm(vm, target_disk, mount_point)


def dispatch_build(
    vm: dict,
    target_type: str,            # "rom", "recovery", or "kernel"
    name: str,                   # "evolution", "lineage", "ofrp", "atoll", ...
    version: str = "16",
    device: str = "all",
    variant: str = "userdebug",
    sf_upload: bool = False,
    gofile_upload: bool = True,
    auto_shutdown: bool = True,
    format_disk: bool = False,
    attach_live: bool = True,
) -> bool:
    """Deploy and launch a build pipeline inside the remote tmux session."""
    target_type = (target_type or "rom").lower()

    # 1. Never disturb an active build.
    if not check_build_slot(vm):
        return False

    # 2. Resolve storage disk.
    if target_type == "recovery":
        resolved = resolve_build_disk(vm, "recovery")
        branch = "recovery"
        target_label = "Recovery"
        build_label = "RECOVERY"
        win_label = f"{name}_{device}".lower()
        detail_label = f"{name.upper()} • {device.strip().upper()}"
    elif target_type == "kernel":
        resolved = resolve_build_disk(vm, version)
        # Kernel builds share the ROM workspace, so track the same target.
        branch = f"aosp-{version}"
        target_label = f"A{version} Kernel"
        build_label = f"KERNEL A{version}"
        win_label = f"kernel-{name}-{version}".lower()
        detail_label = f"{name} • {device.strip().upper()}"
    else:
        resolved = resolve_build_disk(vm, version)
        branch = f"aosp-{version}"
        target_label = f"ROM A{version}"
        build_label = f"ROM A{version}"
        win_label = f"{name}_{device}".lower()
        rom_display = _format_display_name(name)
        detail_label = f"{rom_display} • {device.strip().upper()}"

    target_disk = resolved["disk"]
    mount_point = resolved["mount_point"]
    disk_mode = resolved["mode"]

    clear_screen()
    print_header(f"{name.upper()} {target_type.upper()} BUILD")
    print(f"{CYAN}Target Device{NC}       {device}")
    print(f"{CYAN}Build Type / Variant{NC} {variant}")
    mode_label = "(General Shared Fallback)" if disk_mode == "general" else "(Specific)"
    print(f"{CYAN}Target Storage Disk{NC}  {target_disk} {YELLOW}{mode_label}{NC}")
    print(f"{CYAN}Mount Point Path{NC}     {mount_point}")
    if target_type == "recovery" and mount_point != "/mnt/recovery":
        print(f"{CYAN}Recovery Workspace{NC}   {mount_point}/recovery (symlinked to /mnt/recovery)")
    print(f"{CYAN}SourceForge Upload{NC}   {sf_upload}")
    print(f"{CYAN}Gofile.io Upload{NC}     {gofile_upload}")
    shutdown_text = ("Power off immediately on success (30m idle fallback on error)"
                     if auto_shutdown else "After 30m without build activity")
    print(f"{CYAN}Shutdown Behavior{NC}    {shutdown_text}")
    if format_disk:
        print(f"{CYAN}Format Disk First{NC}    {RED}true{NC}")
    print_divider()

    # 3. Optional clean format (before any VM/disk changes).
    if format_disk:
        if not format_disk_action(vm, target_disk, auto_yes=True):
            return False

    # 4. Start VM, attach + mount the build disk.
    print(f">>> Initializing VM and storage ({target_disk} -> {mount_point})...")
    if not ensure_vm_online(vm):
        print(">>> Error: Could not power on the VM.")
        return False
    from cli.services.vm import ensure_running
    if not ensure_running(vm, target_disk, mount_point):
        print(">>> Error: Could not prepare the build disk.")
        return False

    # 5. Refresh the watchdog and push guest tools before launching.
    deploy_watchdog(vm)
    _general_disk_data_prompt(vm, target_disk, mount_point, branch, target_label)
    sync_build_tools_to_vm(vm)

    # 6. Build runner command.
    if target_type == "kernel":
        args = ["--kernel", name, "--ver", str(version), "--dev", device,
                "--variant", variant]
    elif target_type == "recovery":
        args = ["--recovery", name, "--dev", device, "--variant", variant]
    else:
        args = ["--rom", name, "--ver", str(version), "--dev", device,
                "--variant", variant]
    if sf_upload:
        args.append("--sf")
    if not gofile_upload:
        args.append("--no-gofile")
    command = " ".join(shlex.quote(a) for a in args)

    runner = _runner_script(
        vm=vm, command=command, branch=branch, mount_point=mount_point,
        variant=variant, sf_upload=sf_upload, gofile_upload=gofile_upload,
        auto_shutdown=auto_shutdown, build_label=build_label,
        win_label=win_label, device=device, target_label=target_label,
        detail_label=detail_label,
    )
    runner_b64 = base64.b64encode(runner.encode()).decode()

    print(f"\n{GREEN}>>> Starting {build_label} ({name}) in tmux session 'rom-build' on VM...{NC}")
    print(f"{YELLOW}Tip: You can safely detach (Ctrl+B then D) or close this terminal.{NC}")
    print(f"{YELLOW}Re-attach later from Monitoring -> Live Build Progress.{NC}\n")

    launch_cmd = (
        "if [ -e /run/gcp-build-active ] || pgrep -x soong_ui >/dev/null 2>&1 || "
        "pgrep -x ninja >/dev/null 2>&1 || pgrep -x ckati >/dev/null 2>&1 || "
        "pgrep -f '^bash /tmp/gcp_build_script[.]sh$' >/dev/null 2>&1; then "
        "echo 'An Android build became active before launch; refusing to replace it.' >&2; exit 1; fi; "
        f"printf '%s' '{runner_b64}' | base64 -d > /tmp/gcp_build_runner.sh && "
        "chmod +x /tmp/gcp_build_runner.sh && "
        "{ tmux kill-session -t rom-build 2>/dev/null || true; } && "
        "tmux new-session -d -s rom-build 'bash /tmp/gcp_build_runner.sh'"
    )
    res = vm_exec(vm, launch_cmd, check=False, timeout=90)
    if res.returncode != 0:
        print(f"{RED}[ERROR] Could not create the remote tmux build session. "
              f"The build was not started; check the error below.{NC}")
        print((res.stderr or res.stdout or "").strip())
        return False

    clear_screen()
    print_header("BUILD STARTED")
    print(f"{GREEN}Build{NC}      {name.upper()} {target_type.upper()} ({build_label})")
    print(f"{GREEN}Device{NC}     {device}")
    print(f"{GREEN}Variant{NC}    {variant}")
    print(f"{GREEN}Session{NC}    rom-build")
    print(f"{GREEN}VM{NC}         {vm['instance']}")
    print(f"{GREEN}Mount{NC}      {mount_point}")
    print_divider()

    if attach_live:
        print("\nConnecting to live build session in tmux... (Detach: Ctrl+B then D)\n")
        attach_tmux_session(vm)
    return True
