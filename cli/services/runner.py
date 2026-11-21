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
# GCP-NET-SPEED-V5
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

read -r mem_total mem_avail swap_total swap_free < <(awk '
  /^MemTotal:/ { t = $2 }
  /^MemAvailable:/ { a = $2 }
  /^SwapTotal:/ { st = $2 }
  /^SwapFree:/ { sf = $2 }
  END { printf "%.0f %.0f %.0f %.0f\n", t, a, st, sf }
' /proc/meminfo)

[ -r "$STATE_FILE" ] && read -r prev_t prev_rx prev_tx prev_cpu_total prev_cpu_idle < "$STATE_FILE" 2>/dev/null || true
printf '%s %s %s %s %s\n' "$now_t" "$rx" "$tx" "$cpu_total" "$cpu_idle" > "$STATE_FILE" 2>/dev/null || true

awk -v mode="$MODE" -v now_t="$now_t" -v rx="$rx" -v tx="$tx" \
    -v prev_t="${prev_t:-0}" -v prev_rx="${prev_rx:-0}" -v prev_tx="${prev_tx:-0}" \
    -v cpu_tot="$cpu_total" -v cpu_idl="$cpu_idle" \
    -v prev_cpu_tot="${prev_cpu_total:-0}" -v prev_cpu_idl="${prev_cpu_idle:-0}" \
    -v m_tot="$mem_total" -v m_avail="$mem_avail" \
    -v s_tot="$swap_total" -v s_free="$swap_free" '
  function fmt(b) {
    if (b >= 1048576) return sprintf("%.1f MB/s", b / 1048576);
    if (b >= 1024)    return sprintf("%.0f KB/s", b / 1024);
    return "0 KB/s";
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

    if (s_tot > 0) {
      swap_pct = int(100 * (s_tot - s_free) / s_tot);
      if (swap_pct < 0) swap_pct = 0;
      if (swap_pct > 100) swap_pct = 100;
    } else {
      swap_pct = 0;
    }

    dt = now_t - prev_t;
    if (prev_t <= 0 || dt <= 0 || dt > 60 || rx < prev_rx || tx < prev_tx) {
      dl_str = "DL 0 KB/s";
      ul_str = "UL 0 KB/s";
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

      if (swap_pct >= 80) c_swap = "#[fg=colour196,bold]";
      else if (swap_pct >= 40) c_swap = "#[fg=colour220,bold]";
      else if (s_tot > 0) c_swap = "#[fg=colour141,bold]";
      else c_swap = "#[fg=colour245]";

      printf "%sCPU %d%% #[fg=colour242]| %sRAM %d%% #[fg=colour242]| %sSWAP %d%% #[fg=colour242]| #[fg=colour82,bold]%s #[fg=colour242]| #[fg=colour214,bold]%s\n",
             c_cpu, cpu_pct, c_ram, ram_pct, c_swap, swap_pct, dl_str, ul_str;
    } else {
      printf "CPU %d%% | RAM %d%% | SWAP %d%% | %s | %s\n", cpu_pct, ram_pct, swap_pct, dl_str, ul_str;
    }
  }
'
"""

TMUX_CONF = r"""
# GCP-TMUX-STYLE-V12: high-contrast dark footer, 1s refresh, CPU/RAM/Swap/Net monitor, dynamic build pills & shortcuts
set -g mouse on
set -g set-clipboard on
bind-key m set-option -g mouse
bind-key -T copy-mode-vi MouseDown1Pane select-pane \; send-keys -X cancel
bind-key -T copy-mode MouseDown1Pane select-pane \; send-keys -X cancel
bind-key -T copy-mode-vi MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel
bind-key -T copy-mode MouseDragEnd1Pane send-keys -X copy-pipe-and-cancel
set -g history-limit 50000
set -g status-interval 1
set -g status-style "bg=colour235,fg=colour252"
set -g status-left-length 60
set -g status-left "#[bg=colour39,fg=colour232,bold] #{?@build_icon,#{@build_icon},⚙} #{?@build_label,#{@build_label},BUILD} #[bg=colour238,fg=colour255,bold] #{?@build_detail,#{@build_detail},#W} #[bg=colour235] "
set -g window-status-current-format ""
set -g window-status-format ""
set -g status-right-length 180
set -g status-right "#[fg=colour245]Detach #[fg=colour82,bold]Ctrl+b d #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b "
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
    return f"""if ! grep -q 'GCP-NET-SPEED-V5' /usr/local/bin/vm_net_speed.sh 2>/dev/null; then
  printf '%s' '{net_b64}' | base64 -d | sudo tee /usr/local/bin/vm_net_speed.sh >/dev/null && sudo chmod +x /usr/local/bin/vm_net_speed.sh
fi
if ! grep -q 'GCP-TMUX-STYLE-V12' /etc/tmux.conf 2>/dev/null; then
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
tmux set-option -g status-interval 1 2>/dev/null || true
tmux set-option -g status-style 'bg=colour235,fg=colour252' 2>/dev/null || true
tmux set-option -g status-left-length 60 2>/dev/null || true
tmux set-option -g status-left '#[bg=colour39,fg=colour232,bold] #{{?@build_icon,#{{@build_icon}},⚙}} #{{?@build_label,#{{@build_label}},BUILD}} #[bg=colour238,fg=colour255,bold] #{{?@build_detail,#{{@build_detail}},#W}} #[bg=colour235] ' 2>/dev/null || true
tmux set-window-option -g window-status-current-format '' 2>/dev/null || true
tmux set-window-option -g window-status-format '' 2>/dev/null || true
tmux set-option -g status-right-length 180 2>/dev/null || true
tmux set-option -g status-right '#[fg=colour245]Detach #[fg=colour82,bold]Ctrl+b d #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b ' 2>/dev/null || true
tmux set-option -u -t rom-build mouse 2>/dev/null || true
tmux set-option -t rom-build history-limit 50000 2>/dev/null || true
tmux set-option -t rom-build status-interval 1 2>/dev/null || true
tmux set-option -t rom-build status-style 'bg=colour235,fg=colour252' 2>/dev/null || true
tmux set-option -t rom-build status-left-length 60 2>/dev/null || true
tmux set-window-option -t rom-build window-status-current-format '' 2>/dev/null || true
tmux set-window-option -t rom-build window-status-format '' 2>/dev/null || true
if ! tmux show-option -t rom-build status-left 2>/dev/null | grep -q 'bg=colour'; then
  tmux set-option -t rom-build status-left '#[bg=colour39,fg=colour232,bold] #{{?@build_icon,#{{@build_icon}},⚙}} #{{?@build_label,#{{@build_label}},BUILD}} #[bg=colour238,fg=colour255,bold] #{{?@build_detail,#{{@build_detail}},#W}} #[bg=colour235] ' 2>/dev/null || true
fi
tmux set-option -t rom-build status-right-length 180 2>/dev/null || true
tmux set-option -t rom-build status-right '#[fg=colour245]Detach #[fg=colour82,bold]Ctrl+b d #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b ' 2>/dev/null || true
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
    for f in /mnt/rom/build.status /mnt/recovery/build.status; do
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
    """Sync scripts/, configs/, and patches/ into ~/.gcp_farm and refresh ~/upload.py."""
    print(">>> Syncing build tools and configs to VM...", end="", flush=True)
    vm_exec(vm, f"mkdir -p {REMOTE_FARM} && rm -rf {REMOTE_FARM}/scripts {REMOTE_FARM}/configs {REMOTE_FARM}/patches",
            check=False, timeout=30)

    ok = True
    for folder in ("scripts", "configs", "patches"):
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


def ensure_and_report_swap(vm: dict, mount_point: str) -> None:
    """Activate existing swapfile on mount or root if inactive, and report active swap."""
    print(">>> Checking and activating swap memory...", end="", flush=True)
    q_mp = shlex.quote(mount_point)
    cmd = (
        f"for sw in {q_mp}/swapfile /swapfile; do "
        "if [ -f \"$sw\" ] && ! grep -q \"$sw\" /proc/swaps 2>/dev/null; then "
        "sudo chmod 600 \"$sw\" 2>/dev/null || true; "
        "sudo swapon \"$sw\" 2>/dev/null || { sudo mkswap \"$sw\" 2>/dev/null && sudo swapon \"$sw\" 2>/dev/null; } || true; "
        "fi; done; "
        "swapon --show=NAME,SIZE --noheadings 2>/dev/null"
    )
    res = vm_exec(vm, cmd, check=False, timeout=20)
    print(" Done.", flush=True)
    lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
    if lines:
        summary = ", ".join(f"{parts[1]} ({parts[0]})" if len(parts := ln.split()) >= 2 else ln for ln in lines)
        print(f">>> Swap Memory successfully active: {BOLD}{summary}{NC}")
    else:
        print(f">>> Swap Memory: No swapfile active (pure RAM mode).")


def _runner_script(vm: dict, command: str, branch: str, mount_point: str, variant: str,
                   sf_upload: bool, gofile_upload: bool, auto_shutdown: bool,
                   build_label: str, win_label: str, device: str,
                   target_label: str = "", detail_label: str = "",
                   target_id: str = "") -> str:
    net_setup = net_speed_setup_cmd()
    q_mp = shlex.quote(mount_point)
    q_status = shlex.quote(f"{mount_point}/build.status")
    q_log = shlex.quote(f"{mount_point}/build.log")
    q_target = shlex.quote(f"{mount_point}/.gcp_build_target")
    marker = f"{target_id}|{target_label}" if target_id else (target_label or branch)

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
echo {q_mp} > {q_mp}/.last_mount 2>/dev/null || true
echo {shlex.quote(marker)} > {q_target} 2>/dev/null || true
if [ {shlex.quote(branch)} = 'recovery' ] && [ {q_mp} != '/mnt/recovery' ]; then
    sudo mkdir -p {q_mp}/recovery && sudo ln -sfn {q_mp}/recovery /mnt/recovery
fi
echo "BUILD_STATUS: RUNNING ($BUILD_VARIANT) - $(date)" > {q_status} 2>/dev/null || true

# Auto-activate existing swapfile on build disk or root disk if inactive
for sw in {q_mp}/swapfile /swapfile; do
    if [ -f "$sw" ] && ! grep -q "$sw" /proc/swaps 2>/dev/null; then
        echo ">>> [SWAP] Activating existing swapfile at $sw in background..."
        sudo chmod 600 "$sw" 2>/dev/null || true
        sudo swapon "$sw" 2>/dev/null || {{ sudo mkswap "$sw" 2>/dev/null && sudo swapon "$sw" 2>/dev/null; }} || true
    fi
done

stty -ixon -ixoff 2>/dev/null || true
stty stop undef 2>/dev/null || true
stty start undef 2>/dev/null || true

BUILD_SCRIPT_TMP="/tmp/gcp_build_script.py"
if [ -s ~/.gcp_farm/scripts/build.py ]; then
    cp ~/.gcp_farm/scripts/build.py "$BUILD_SCRIPT_TMP"
    mkdir -p /tmp/configs /tmp/scripts /tmp/patches
    cp -r ~/.gcp_farm/configs/* /tmp/configs/ 2>/dev/null || true
    cp -r ~/.gcp_farm/scripts/* /tmp/scripts/ 2>/dev/null || true
    cp -r ~/.gcp_farm/patches/* /tmp/patches/ 2>/dev/null || true
    BUILD_EXIT=0
else
    echo ">>> [ERROR] Remote build engine ~/.gcp_farm/scripts/build.py is missing" | tee {q_log}
    BUILD_EXIT=1
fi

if [ "$BUILD_EXIT" -eq 0 ]; then
    if [ -f {q_log} ]; then
        mkdir -p {q_mp}/logs 2>/dev/null || true
        cp -f {q_log} {q_mp}/build.log.prev 2>/dev/null || true
        cp -f {q_log} "{q_mp}/logs/build_$(date +%Y%m%d_%H%M%S).log" 2>/dev/null || true
    fi
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
    [ -f "$HOME/.upload_links.log" ] && sudo cp -f "$HOME/.upload_links.log" "{mount_point}/.upload_links.log" 2>/dev/null || true
    python3 -c '
import os, importlib.util
p = os.path.expanduser("~/.gcp_farm/scripts/upload.py")
if not os.path.exists(p): p = os.path.expanduser("~/upload.py")
if os.path.exists(p):
    spec = importlib.util.spec_from_file_location("u", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    log = os.path.expanduser("~/.upload_links.log")
    if os.path.exists(log) and hasattr(mod, "_update_gcp_metadata_links"):
        lines = [l.strip() for l in open(log).readlines() if l.strip()]
        mod._update_gcp_metadata_links(lines)
    if hasattr(mod, "_update_gcp_metadata_build_status"):
        mod._update_gcp_metadata_build_status("SUCCESS ('"$BUILD_VARIANT"') - $(date)")
' 2>/dev/null || true
    echo -e '\\n\\033[1;32m============================================================\\033[0m'
    echo -e '\\033[1;32m [SUCCESS] Build and upload completed successfully!\\033[0m'
    echo -e '\\033[1;32m============================================================\\033[0m'
else
    tmux set-option -t rom-build @build_icon "✗" 2>/dev/null || true
    tmux set-option -t rom-build status-left "#[bg=colour196,fg=colour255,bold] ✗ {build_label} #[bg=colour238,fg=colour255,bold] {detail_label} #[bg=colour235] " 2>/dev/null || true
    echo "BUILD_STATUS: FAILED (Exit Code $BUILD_EXIT) - $(date)" > {q_status} 2>/dev/null || true
    mkdir -p {q_mp}/logs 2>/dev/null || true
    FAIL_LOG="{q_mp}/logs/build_failed_$(date +%Y%m%d_%H%M%S).log"
    cp -f {q_log} "{q_mp}/build.log.failed" 2>/dev/null || true
    cp -f {q_log} "$FAIL_LOG" 2>/dev/null || true
    echo "FAILED_LOG: {mount_point}/build.log.failed" >> {q_status} 2>/dev/null || true
    echo "FAILED_ARCHIVE: $FAIL_LOG" >> {q_status} 2>/dev/null || true
    python3 -c '
import os, importlib.util
p = os.path.expanduser("~/.gcp_farm/scripts/upload.py")
if not os.path.exists(p): p = os.path.expanduser("~/upload.py")
if os.path.exists(p):
    spec = importlib.util.spec_from_file_location("u", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    log_p = "{mount_point}/build.log"
    root_err = ""
    if os.path.exists(log_p):
        try:
            lines = open(log_p, errors="replace").readlines()[-150:]
            errs = [l.strip() for l in lines if any(k in l.lower() for k in ("failed:", "ninja: build stopped", "error:"))]
            root_err = "\n".join(errs[-8:])
        except Exception:
            pass
    if hasattr(mod, "_update_gcp_metadata_build_status"):
        mod._update_gcp_metadata_build_status("FAILED (Exit Code '"$BUILD_EXIT"') - $(date)", root_err)
' 2>/dev/null || true
    echo -e '\\n\\033[1;31m============================================================\\033[0m'
    echo -e "\\033[1;31m [ERROR] Build stopped or failed with exit code $BUILD_EXIT!\\033[0m"
    echo -e '\\033[1;31m============================================================\\033[0m'
    echo "Full build output is saved at: {mount_point}/build.log"
    echo "A permanent failure copy is saved at: {mount_point}/build.log.failed"
    echo "Historical failure archive is saved at: $FAIL_LOG"
fi

sudo rm -f /run/gcp-build-active 2>/dev/null || true

if [ "$WRAPPER_SHUTDOWN" = 'true' ] && [ "$BUILD_EXIT" -eq 0 ]; then
    sudo rm -f /run/gcp-build-shutdown-pending 2>/dev/null || true
    echo ""
    echo -e '\\033[1;33m>>> Build succeeded! Auto-shutdown requested: Powering off VM in 30 seconds...\\033[0m'
    echo -e '\\033[1;33m>>> Press any key to cancel shutdown and keep session open...\\033[0m'
    if read -t 30 -n 1 -s -r; then
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


def _target_work_folder(target_type: str, name: str, version: str = "") -> str:
    """Return the expected workspace directory basename (e.g. 'evox', 'los', 'ofrp')."""
    target_type = (target_type or "rom").lower()
    name = (name or "").lower()
    ver = str(version or "").lower()
    if target_type == "recovery":
        try:
            cfg_path = REPO_ROOT / "configs" / "recovery.json"
            if cfg_path.exists():
                rec_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if name in rec_cfg and "work_dir" in rec_cfg[name]:
                    return Path(rec_cfg[name]["work_dir"]).name
                for rname, rinfo in rec_cfg.items():
                    if name in rinfo.get("aliases", []):
                        if "work_dir" in rinfo:
                            return Path(rinfo["work_dir"]).name
        except Exception:
            pass
        if "fox" in name or "orange" in name or name == "ofrp":
            return "ofrp"
        if "twrp" in name or "teamwin" in name:
            return "twrp"
        return name
    elif target_type == "kernel":
        try:
            cfg_path = REPO_ROOT / "configs" / "kernel.json"
            if cfg_path.exists():
                kern_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if name in kern_cfg:
                    ver_cfg = kern_cfg[name].get("versions", {}).get(ver, {})
                    if "work_dir" in ver_cfg:
                        return Path(ver_cfg["work_dir"]).name
                for kname, kinfo in kern_cfg.items():
                    if name in kinfo.get("aliases", []):
                        ver_cfg = kinfo.get("versions", {}).get(ver, {})
                        if "work_dir" in ver_cfg:
                            return Path(ver_cfg["work_dir"]).name
        except Exception:
            pass
        return "evox"
    else:  # rom
        try:
            cfg_path = REPO_ROOT / "configs" / "rom.json"
            if cfg_path.exists():
                rom_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if name in rom_cfg:
                    ver_cfg = rom_cfg[name].get("versions", {}).get(ver, {})
                    if "work_dir" in ver_cfg:
                        return Path(ver_cfg["work_dir"]).name
                for rname, rinfo in rom_cfg.items():
                    if name in rinfo.get("aliases", []):
                        ver_cfg = rinfo.get("versions", {}).get(ver, {})
                        if "work_dir" in ver_cfg:
                            return Path(ver_cfg["work_dir"]).name
        except Exception:
            pass
        if "evo" in name:
            return "evox"
        if "lineage" in name or name == "los":
            return "los"
        return name


def _is_same_build_source(
    prev_id: str,
    prev_clean: str,
    target_type: str,
    target_name: str,
    version: str,
    target_work_folder: str,
    dir_list: list[str],
    mount_point: str = "",
    disk_mode: str = "general",
) -> bool:
    """Return True if the current build uses the same source/workspace as on disk."""
    t_type = (target_type or "").lower()
    t_name = (target_name or "").lower()
    t_ver = str(version or "").lower()
    p_clean = (prev_clean or "").lower()
    p_id = (prev_id or "").lower()

    # 1. If target workspace directory already exists on disk, the source is already there!
    if target_work_folder and target_work_folder in dir_list:
        return True

    # 2. Exact target ID match
    tid = f"{t_type}:{t_name}:{t_ver}"
    if p_id and (p_id == tid or tid in p_clean):
        return True

    # Parse previous target components if in standard "type:name:version" format
    p_type, p_name, p_ver = "", "", ""
    if ":" in p_id:
        parts = p_id.split(":")
        if len(parts) >= 3:
            p_type, p_name, p_ver = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            p_type, p_name = parts[0], parts[1]

    # 3. Check if previous target and current target share the same workspace folder
    if p_type and p_name:
        prev_work_folder = _target_work_folder(p_type, p_name, p_ver)
        if prev_work_folder and target_work_folder and prev_work_folder == target_work_folder:
            return True

    # 4. Kernel <-> Evolution X ROM cross-compatibility (both share evox workspace)
    is_current_evo_kernel = (t_type == "kernel") or (t_type == "rom" and "evo" in t_name)
    is_prev_evo_kernel = any(k in p_clean for k in ("kernel", "atoll", "evolution", "evox", "evo"))
    if is_current_evo_kernel and is_prev_evo_kernel:
        return True

    # 5. Recovery builds on /mnt/recovery (OrangeFox and TWRP coexist in separate subfolders)
    if t_type == "recovery" or mount_point == "/mnt/recovery":
        if p_type == "recovery" or any(r in p_clean for r in ("recovery", "ofrp", "fox", "twrp")):
            return True

    # 6. Same target name and version
    if t_name and t_name in p_clean:
        if not t_ver or t_ver in p_clean or t_type == "recovery":
            return True

    # 7. Dedicated disk
    if disk_mode not in ("general", "shared-recovery"):
        if not p_id or any(k in p_clean for k in (t_type, t_name, f"aosp-{t_ver}", f"rom a{t_ver}")):
            return True

    return False


def _general_disk_data_prompt(
    vm: dict,
    target_disk: str,
    mount_point: str,
    branch: str,
    target_label: str = "",
    target_type: str = "rom",
    target_name: str = "",
    version: str = "",
    disk_mode: str = "general",
    target_id: str = "",
) -> None:
    if not is_interactive():
        return
    py_code = (
        f"import os, json, shutil\n"
        f"mp = {repr(mount_point)}\n"
        f"prev = ''\n"
        f"tf = os.path.join(mp, '.gcp_build_target')\n"
        f"if os.path.isfile(tf):\n"
        f"    try:\n"
        f"        with open(tf, 'r', encoding='utf-8', errors='ignore') as f: prev = f.read().strip()\n"
        f"    except Exception: pass\n"
        f"used = ''\n"
        f"try:\n"
        f"    u = shutil.disk_usage(mp)\n"
        f"    used = f'{{u.used / (1024**3):.1f}}G / {{u.total / (1024**3):.1f}}G (Free: {{u.free / (1024**3):.1f}}G)'\n"
        f"except Exception: pass\n"
        f"dirs = []\n"
        f"try:\n"
        f"    ign = {{'lost+found', '.gcp_build_target', 'swapfile', 'build.log', 'build.status'}}\n"
        f"    for e in sorted(os.listdir(mp)):\n"
        f"        if e not in ign: dirs.append(e)\n"
        f"except Exception: pass\n"
        f"print(json.dumps({{'prev': prev, 'used': used, 'dirs': dirs}}))\n"
    )
    b64 = base64.b64encode(py_code.encode()).decode()
    res = vm_exec(vm, f"python3 -c \"$(printf '%s' '{b64}' | base64 -d)\"", check=False, timeout=30)
    out = (res.stdout or "").strip()
    data: dict = {}
    if out:
        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            pass

    dir_list: list[str] = data.get("dirs", [])
    if not dir_list:
        return

    prev = str(data.get("prev", ""))
    used = str(data.get("used", ""))

    prev_clean = prev.strip()
    prev_id = prev_clean.split("|")[0].strip().lower() if "|" in prev_clean else prev_clean.lower()
    target_work_folder = _target_work_folder(target_type, target_name, version)

    if _is_same_build_source(
        prev_id=prev_id,
        prev_clean=prev_clean,
        target_type=target_type,
        target_name=target_name,
        version=version,
        target_work_folder=target_work_folder,
        dir_list=dir_list,
        mount_point=mount_point,
        disk_mode=disk_mode,
    ):
        return

    display = target_label or branch
    prev_display = prev_clean.split("|")[1].strip() if "|" in prev_clean else prev_clean
    dirs_display = " ".join(dir_list[:6])
    print(f"\n{YELLOW}>>> Notice: Storage disk ({target_disk}) at {mount_point} contains existing data from a different target!{NC}")
    if prev_display:
        print(f"    Previous Target : {BOLD}{prev_display}{NC} (New: {BOLD}{display}{NC})")
    if used:
        print(f"    Disk Usage      : {used}")
    print(f"    Top Entries     : {dirs_display}")
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
        rom_display = _format_display_name(name)
        target_label = f"{rom_display} Recovery"
        build_label = "RECOVERY"
        win_label = f"{name}_{device}".lower()
        detail_label = f"{name.upper()} • {device.strip().upper()}"
        target_id = f"recovery:{name.lower()}:recovery"
    elif target_type == "kernel":
        resolved = resolve_build_disk(vm, version)
        # Kernel builds share the ROM workspace, so track the same target.
        branch = f"aosp-{version}"
        rom_display = _format_display_name(name)
        target_label = f"{rom_display} A{version} Kernel"
        build_label = f"KERNEL A{version}"
        win_label = f"kernel-{name}-{version}".lower()
        detail_label = f"{name} • {device.strip().upper()}"
        target_id = f"kernel:{name.lower()}:{version}"
    else:
        resolved = resolve_build_disk(vm, version)
        branch = f"aosp-{version}"
        rom_display = _format_display_name(name)
        target_label = f"{rom_display} ROM A{version}"
        build_label = f"ROM A{version}"
        win_label = f"{name}_{device}".lower()
        detail_label = f"{rom_display} • {device.strip().upper()}"
        target_id = f"rom:{name.lower()}:{version}"

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
        print(f"{CYAN}Recovery Workspace{NC}   {mount_point}/recovery")
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

    # 5. Refresh the watchdog and ensure swap memory is active and reported.
    deploy_watchdog(vm)
    ensure_and_report_swap(vm, mount_point)
    _general_disk_data_prompt(
        vm=vm,
        target_disk=target_disk,
        mount_point=mount_point,
        branch=branch,
        target_label=target_label,
        target_type=target_type,
        target_name=name,
        version=str(version),
        disk_mode=disk_mode,
        target_id=target_id,
    )
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
    else:
        args.append("--no-sf")
    if gofile_upload:
        args.append("--gofile")
    else:
        args.append("--no-gofile")
    command = " ".join(shlex.quote(a) for a in args)

    runner = _runner_script(
        vm=vm, command=command, branch=branch, mount_point=mount_point,
        variant=variant, sf_upload=sf_upload, gofile_upload=gofile_upload,
        auto_shutdown=auto_shutdown, build_label=build_label,
        win_label=win_label, device=device, target_label=target_label,
        detail_label=detail_label, target_id=target_id,
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
        import threading
        stop_watcher = threading.Event()

        def _link_sync_watcher():
            from cli.services.link import sync_links_from_vm
            while not stop_watcher.is_set():
                stop_watcher.wait(10)
                if stop_watcher.is_set():
                    break
                try:
                    sync_links_from_vm(vm)
                except Exception:
                    pass

        watcher_thread = threading.Thread(target=_link_sync_watcher, daemon=True)
        watcher_thread.start()

        print("\nConnecting to live build session in tmux... (Detach: Ctrl+B then D)\n")
        attach_tmux_session(vm)
        stop_watcher.set()
        try:
            from cli.services.link import sync_links_from_vm
            sync_links_from_vm(vm)
        except Exception:
            pass
        try:
            from cli.services.monitor import write_metadata_build_status, collect_build_status
            b_data = collect_build_status(vm)
            b_status = b_data.get("status", "")
            if b_status and b_status != "NO_BUILD_RECORDED":
                err_text = "\n".join(b_data.get("errors", []))
                write_metadata_build_status(vm, b_status, err_text)
        except Exception:
            pass
        try:
            from cli.services.download import auto_download_failed_log
            auto_download_failed_log(vm, mount_point)
        except Exception:
            pass
    return True
