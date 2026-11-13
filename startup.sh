#!/bin/bash
# ==============================================================================
# VM Startup Script: Auto-mounts secondary ROM disks to dedicated paths upon boot
# and ensures the 30-minute Auto-Idle Shutdown Watchdog is installed & running.
# Runs automatically by google-startup-scripts.service at every instance boot.
# ==============================================================================

# Disks to dedicated mountpoints mapping (boot-time mounting is ONLY for
# ROM/recovery-specific disks; general shared disks mount on demand at build time)
declare -A DISK_MAP=(
    ["google-rom-a16"]="/mnt/a16"
    ["google-rom-a17"]="/mnt/a17"
    ["google-rom-recovery"]="/mnt/recovery"
)

# 1. Mount recognized ROM disks to dedicated paths
for id in "${!DISK_MAP[@]}"; do
    dev="/dev/disk/by-id/$id"
    if [ -b "$dev" ]; then
        mp="${DISK_MAP[$id]}"
        mkdir -p "$mp"
        if ! mountpoint -q "$mp"; then
            mount -o discard,defaults,noatime,nofail "$dev" "$mp" 2>/dev/null || true
        fi
        resize2fs "$dev" 2>/dev/null || true
    fi
done

# 2. Configure SSH keepalive to terminate dead sessions
mkdir -p /etc/ssh/sshd_config.d
cat << 'EOF_SSH' > /etc/ssh/sshd_config.d/99-watchdog-keepalive.conf
ClientAliveInterval 60
ClientAliveCountMax 3
EOF_SSH
systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true

# 3. Ensure Auto-Idle Shutdown Watchdog script is installed
cat << 'EOF_WATCHDOG' > /usr/local/bin/idle_watchdog.sh
#!/usr/bin/env bash
IDLE_THRESHOLD_MINUTES=30
CHECK_INTERVAL_SECONDS=60
STATUS_FILE="/run/idle_watchdog.status"
LOG_TAG="idle-watchdog"

idle_count=0

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

is_busy() {
    if [ -e /run/gcp-build-active ]; then
        echo "BUILD_RUNNER_ACTIVE"
        return 0
    fi

    local build_procs
    build_procs=$(pgrep -f '[s]oong_ui|[s]oong_build|[n]inja|[c]kati|repo/main\.py|repo/repo|/usr/local/bin/repo|[u]pload\.py|[s]ignapk|[z]ipalign|[g]it-remote-https|[a]ria2c|[r]clone|[c]lang|[r]ustc|[j]avac|/d8([[:space:]]|$)|/bin/mka' 2>/dev/null | wc -l)
    if [ "$build_procs" -gt 0 ]; then
        echo "BUILD_ACTIVE ($build_procs build processes running)"
        return 0
    fi

    local active_ssh_sockets
    active_ssh_sockets=$(ss -t state established '( sport = :22 )' 2>/dev/null | grep -v 'Local Address' | grep -v '127.0.0.1' | grep -v '::1' | wc -l)

    if [ "$active_ssh_sockets" -gt 0 ]; then
        local now
        now=$(date +%s)
        local min_idle_sec=999999
        local found_pts=0

        for pts in /dev/pts/[0-9]*; do
            if [ -c "$pts" ]; then
                found_pts=1
                local pts_atime
                pts_atime=$(stat -c %X "$pts" 2>/dev/null || stat -c %Y "$pts" 2>/dev/null || echo 0)
                local pts_idle=$((now - pts_atime))
                if [ "$pts_idle" -lt "$min_idle_sec" ]; then
                    min_idle_sec="$pts_idle"
                fi
            fi
        done

        if [ "$found_pts" -eq 1 ] && [ "$min_idle_sec" -lt $((IDLE_THRESHOLD_MINUTES * 60)) ]; then
            local idle_min=$((min_idle_sec / 60))
            echo "SSH_ACTIVE ($active_ssh_sockets socket(s), active ${idle_min}m ago)"
            return 0
        fi
    fi

    return 1
}

log_msg "Starting GCP Build Farm Idle Shutdown Watchdog (Threshold: ${IDLE_THRESHOLD_MINUTES}m)..."
echo "INIT: Watchdog started (threshold: ${IDLE_THRESHOLD_MINUTES}m)" > "$STATUS_FILE" 2>/dev/null || true

while true; do
    sleep "$CHECK_INTERVAL_SECONDS"

    busy_reason=$(is_busy)
    if [ -n "$busy_reason" ]; then
        if [ "$idle_count" -gt 0 ]; then
            log_msg "Activity detected ($busy_reason). Resetting idle timer."
        fi
        idle_count=0
        echo "BUSY: $busy_reason" > "$STATUS_FILE" 2>/dev/null || true
    else
        idle_count=$((idle_count + 1))
        echo "IDLE: ${idle_count}/${IDLE_THRESHOLD_MINUTES} minutes" > "$STATUS_FILE" 2>/dev/null || true

        if [ $((idle_count % 5)) -eq 0 ] || [ "$idle_count" -ge $((IDLE_THRESHOLD_MINUTES - 2)) ]; then
            log_msg "VM idle for ${idle_count}/${IDLE_THRESHOLD_MINUTES} minutes (no builds, no active SSH sessions)."
        fi

        if [ "$idle_count" -ge "$IDLE_THRESHOLD_MINUTES" ]; then
            log_msg "Idle threshold reached (${IDLE_THRESHOLD_MINUTES}m). Powering off VM to pause compute billing..."
            echo "SHUTTING_DOWN: Idle for ${IDLE_THRESHOLD_MINUTES} minutes" > "$STATUS_FILE" 2>/dev/null || true
            wall ">>> GCP Auto-Idle Watchdog: Instance idle for ${IDLE_THRESHOLD_MINUTES} minutes. Powering down to pause compute billing..." 2>/dev/null || true
            sync
            systemctl poweroff --force --force 2>/dev/null || /sbin/poweroff -f 2>/dev/null || shutdown -h now 2>/dev/null || true
            exit 0
        fi
    fi
done
EOF_WATCHDOG
chmod +x /usr/local/bin/idle_watchdog.sh

# 4. Install / Update systemd idle-shutdown unit
cat << 'EOF_UNIT' > /etc/systemd/system/idle-shutdown.service
[Unit]
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
EOF_UNIT

# 5. Enable and start Watchdog immediately
systemctl daemon-reload
systemctl enable --now idle-shutdown.service 2>/dev/null || true

# 6. Install live network speed helper & configure tmux status bar
cat << 'EOF_NETSPEED' > /usr/local/bin/vm_net_speed.sh
#!/usr/bin/env bash
# GCP-NET-SPEED-V2
# Zero-sleep live VM download/upload speed reporter for tmux status-right.
MODE="$1"
STATE_FILE="/tmp/vm_net_speed_$(id -u).state"

read -r now_t _ < /proc/uptime || exit 0
read -r rx tx < <(awk '
  /:/ {
    gsub(/:/, " ");
    iface = $1;
    if (iface != "lo" && iface !~ /^(veth|docker|br-|virbr)/) {
      rx += $2; tx += $10;
    }
  }
  END { printf "%.0f %.0f\n", rx, tx }
' /proc/net/dev)

if [ -r "$STATE_FILE" ]; then
  read -r prev_t prev_rx prev_tx < "$STATE_FILE" 2>/dev/null || true
fi
printf '%s %s %s\n' "$now_t" "$rx" "$tx" > "$STATE_FILE" 2>/dev/null || true

awk -v mode="$MODE" -v now_t="$now_t" -v rx="$rx" -v tx="$tx" \
    -v prev_t="${prev_t:-0}" -v prev_rx="${prev_rx:-0}" -v prev_tx="${prev_tx:-0}" '
  function fmt(b) {
    if (b >= 1048576) return sprintf("%.1f MB/s", b / 1048576);
    if (b >= 1024)    return sprintf("%.0f KB/s", b / 1024);
    return sprintf("%.0f B/s", b);
  }
  BEGIN {
    dt = now_t - prev_t;
    if (prev_t <= 0 || dt <= 0 || dt > 60 || rx < prev_rx || tx < prev_tx) {
      if (mode == "--tmux") print "#[fg=colour245]DL -- #[fg=colour242]| #[fg=colour245]UL --";
      else print "DL -- | UL --";
    } else {
      dl = fmt((rx - prev_rx) / dt);
      ul = fmt((tx - prev_tx) / dt);
      if (mode == "--tmux") printf "#[fg=colour82,bold]DL %s #[fg=colour242]| #[fg=colour214,bold]UL %s\n", dl, ul;
      else printf "DL %s | UL %s\n", dl, ul;
    }
  }
'
EOF_NETSPEED
chmod +x /usr/local/bin/vm_net_speed.sh

if ! grep -q "GCP-TMUX-STYLE-V4" /etc/tmux.conf 2>/dev/null; then
  sed -i '/# GCP Build Farm:/,$d;/# GCP-TMUX-STYLE-V/,$d' /etc/tmux.conf 2>/dev/null || true
  cat << 'EOF_TMUX' >> /etc/tmux.conf

# GCP-TMUX-STYLE-V4: high-contrast dark footer, mouse scrolling, 50k scrollback, build label & DL/UL speed
set -g mouse on
set -g history-limit 50000
set -g status-interval 2
set -g status-style "bg=colour235,fg=colour252"
set -g status-left-length 30
set -g status-left "#[bg=colour39,fg=colour232,bold] #{?@build_label,#{@build_label},BUILD} #[bg=colour235,fg=colour39] "
set -g window-status-style "bg=colour235,fg=colour245"
set -g window-status-current-style "bg=colour238,fg=colour255,bold"
set -g status-right-length 85
set -g status-right "#(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b "
EOF_TMUX
fi
systemctl restart idle-shutdown.service 2>/dev/null || true
