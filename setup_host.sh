#!/usr/bin/env bash
# ==============================================================================
# setup_host.sh: Host Environment Provisioning on GCP 30GB Boot Disk
# Installs all OS dependencies, repo, ccache, and git on the persistent boot disk.
# Secondary ROM disk provisioning is completely decoupled and optional.
# ==============================================================================
set -e

export DEBIAN_FRONTEND=noninteractive

echo "============================================================"
echo " [1/5] Installing Android Build Packages on 30GB Boot Disk..."
echo "============================================================"
sudo apt-get update -y
sudo apt-get install -y \
    bc \
    bison \
    build-essential \
    ccache \
    curl \
    flex \
    g++-multilib \
    gcc-multilib \
    git \
    git-lfs \
    gnupg \
    gperf \
    htop \
    imagemagick \
    jq \
    lib32ncurses5-dev \
    lib32readline-dev \
    lib32z1-dev \
    libc6-dev-i386 \
    libelf-dev \
    libgl1-mesa-dev \
    libncurses5 \
    libncurses5-dev \
    libsdl1.2-dev \
    libssl-dev \
    libxml2 \
    libxml2-utils \
    lzop \
    ninja-build \
    openjdk-17-jdk \
    pngcrush \
    python3 \
    python3-pip \
    python-is-python3 \
    rsync \
    schedtool \
    squashfs-tools \
    tmux \
    tree \
    unzip \
    xsltproc \
    zip \
    zlib1g-dev \
    aria2

echo "============================================================"
echo " [2/5] Installing Google repo Tool on 30GB Boot Disk..."
echo "============================================================"
sudo curl -s https://storage.googleapis.com/git-repo-downloads/repo > /tmp/repo
sudo chmod a+x /tmp/repo
sudo mv /tmp/repo /usr/local/bin/repo

echo "============================================================"
echo " [3/5] Configuring Git & CCache on Boot Disk..."
echo "============================================================"
if [ -z "$(git config --global user.name 2>/dev/null)" ]; then
    git config --global user.name "Android Builder"
fi
if [ -z "$(git config --global user.email 2>/dev/null)" ]; then
    git config --global user.email "android-builder@noreply.github.com"
fi
git config --global color.ui auto

# Persistent ccache on the boot disk (enforced 15 GB cap, system-wide)
mkdir -p "$HOME/.ccache"
ccache -M 15G
ccache -o max_size=15G
ccache -o compression=true

# Enforce in ~/.ccache/ccache.conf
cat << 'EOF' > "$HOME/.ccache/ccache.conf"
max_size = 15.0G
compression = true
EOF

# Enforce OS-level default in /etc/ccache.conf
sudo mkdir -p /etc
sudo tee /etc/ccache.conf > /dev/null << 'EOF'
max_size = 15.0G
compression = true
EOF

# Enforce system-wide in /etc/profile.d/ccache.sh (no override allowed)
sudo tee /etc/profile.d/ccache.sh > /dev/null << 'EOF'
export CCACHE_DIR="${HOME}/.ccache"
export CCACHE_MAXSIZE="15G"
readonly CCACHE_MAXSIZE
export USE_CCACHE=1
export CCACHE_EXEC="$(which ccache 2>/dev/null || echo /usr/bin/ccache)"
export PATH="/usr/lib/ccache:${PATH}"
EOF
sudo chmod 644 /etc/profile.d/ccache.sh

# Update environment variables in ~/.bashrc (no override allowed)
sed -i '/# Android Build Environment (CCache on Boot Disk)/,/ANDROID_JACK_VM_ARGS/d' "$HOME/.bashrc" 2>/dev/null || true
sed -i '/USE_CCACHE/d' "$HOME/.bashrc" 2>/dev/null || true
sed -i '/CCACHE_MAXSIZE/d' "$HOME/.bashrc" 2>/dev/null || true

cat << 'EOF' >> "$HOME/.bashrc"

# Android Build Environment (CCache on Boot Disk - 15GB cap enforced)
export USE_CCACHE=1
export CCACHE_DIR="$HOME/.ccache"
export CCACHE_EXEC="$(which ccache 2>/dev/null || echo /usr/bin/ccache)"
export CCACHE_MAXSIZE="15G"
readonly CCACHE_MAXSIZE
export PATH="/usr/lib/ccache:$PATH"
export ANDROID_JACK_VM_ARGS="-Dfile.encoding=UTF-8 -XX:+TieredCompilation -Xmx4G"
EOF

# Ensure user SSH directory exists with safe permissions
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

echo "============================================================"
echo " [4/5] Checking Secondary ROM Storage Disk (Optional)..."
echo "============================================================"
# Boot/setup mounts ONLY ROM/recovery-specific disks.
# A shared general disk (google-rom-general) stays unmounted until a build starts.
SPECIFIC_DISKS=(
    "/dev/disk/by-id/google-rom-a16:/mnt/a16"
    "/dev/disk/by-id/google-rom-a17:/mnt/a17"
    "/dev/disk/by-id/google-rom-recovery:/mnt/recovery"
)
FOUND_SECONDARY=0

for entry in "${SPECIFIC_DISKS[@]}"; do
    ROM_DISK="${entry%%:*}"
    TARGET_MP="${entry#*:}"
    if [ -b "$ROM_DISK" ]; then
        FOUND_SECONDARY=1
        echo "Detected specific ROM disk: $ROM_DISK -> $TARGET_MP"
        if ! blkid "$ROM_DISK" 2>/dev/null | grep -q 'TYPE="ext4"'; then
            echo "Formatting $ROM_DISK with ext4..."
            sudo mkfs.ext4 -m 1 -F "$ROM_DISK"
        fi
        sudo mkdir -p "$TARGET_MP"
        if ! mountpoint -q "$TARGET_MP"; then
            sudo mount -o discard,defaults,noatime,nofail "$ROM_DISK" "$TARGET_MP"
        fi
        sudo resize2fs "$ROM_DISK" 2>/dev/null || true
        sudo chown "$USER:$USER" "$TARGET_MP"
        echo "Storage mounted successfully at $TARGET_MP (Pure 64GB RAM mode, swap disabled)."
    fi
done

if [ -b "/dev/disk/by-id/google-rom-general" ]; then
    FOUND_SECONDARY=1
    echo "Detected general shared disk: /dev/disk/by-id/google-rom-general"
    if ! blkid /dev/disk/by-id/google-rom-general 2>/dev/null | grep -q 'TYPE="ext4"'; then
        echo "Formatting /dev/disk/by-id/google-rom-general with ext4..."
        sudo mkfs.ext4 -m 1 -F /dev/disk/by-id/google-rom-general
    fi
    echo "General disk left unmounted (it will mount to /mnt/a16, /mnt/a17, or /mnt/recovery when a build starts)."
fi

if [ "$FOUND_SECONDARY" -eq 0 ]; then
    echo "No secondary ROM disk attached at the moment."
    echo "Boot disk packages, ccache, repo and SSH keys are 100% ready."
fi

echo "============================================================"
echo " [5/5] Configuring 30-Minute Auto-Idle Shutdown Watchdog..."
echo "============================================================"
sudo tee /usr/local/bin/idle_watchdog.sh > /dev/null << 'EOF_WATCHDOG'
#!/usr/bin/env bash
# ==============================================================================
# idle_watchdog.sh - Automatic VM Idle Shutdown Watchdog
# Prevents runaway GCP compute billing (~$0.726/hr) by automatically powering off
# the instance after 30 minutes of complete inactivity.
# ==============================================================================

IDLE_THRESHOLD_MINUTES=30
CHECK_INTERVAL_SECONDS=60
STATUS_FILE="/run/idle_watchdog.status"
LOG_TAG="idle-watchdog"

idle_count=0
last_pending_id=""

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

is_busy() {
    if [ -e /run/gcp-build-active ]; then
        echo "BUILD_RUNNER_ACTIVE"
        return 0
    fi

    # 1. Check for active ROM/recovery build, sync, packaging, or upload processes
    local build_procs
    build_procs=$(pgrep -f '[s]oong_ui|[s]oong_build|[n]inja|[c]kati|repo/main\.py|repo/repo|/usr/local/bin/repo|[u]pload\.py|[s]ignapk|[z]ipalign|[g]it-remote-https|[a]ria2c|[r]clone|[c]lang|[r]ustc|[j]avac|/d8([[:space:]]|$)|/bin/mka' 2>/dev/null | wc -l)
    if [ "$build_procs" -gt 0 ]; then
        echo "BUILD_ACTIVE ($build_procs build processes running)"
        return 0
    fi

    # 2. Check for active interactive user SSH sessions
    # Check if any established TCP connections exist on port 22 (SSH)
    local active_ssh_sockets
    active_ssh_sockets=$(ss -t state established '( sport = :22 )' 2>/dev/null | grep -v 'Local Address' | grep -v '127.0.0.1' | grep -v '::1' | wc -l)

    if [ "$active_ssh_sockets" -gt 0 ]; then
        # An SSH connection exists. Check if user is actively interacting (not idle for > 30 min)
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

        # If user touched a terminal in the last 1800 seconds (30 minutes), consider active
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

    pending_id=$(cat /run/gcp-build-shutdown-pending 2>/dev/null || true)
    if [ -n "$pending_id" ] && [ "$pending_id" != "$last_pending_id" ]; then
        idle_count=0
        log_msg "Delayed shutdown requested; starting a fresh 30-minute build-idle timer."
    fi
    last_pending_id="$pending_id"

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

        # Log to journal/syslog every 5 minutes or when nearing threshold
        if [ $((idle_count % 5)) -eq 0 ] || [ "$idle_count" -ge $((IDLE_THRESHOLD_MINUTES - 2)) ]; then
            log_msg "VM idle for ${idle_count}/${IDLE_THRESHOLD_MINUTES} minutes (no builds, no SSH sessions)."
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

sudo chmod +x /usr/local/bin/idle_watchdog.sh

sudo tee /etc/systemd/system/idle-shutdown.service > /dev/null << 'EOF_UNIT'
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

sudo systemctl daemon-reload
sudo systemctl enable --now idle-shutdown.service
sudo systemctl restart idle-shutdown.service
echo "Auto-Idle Shutdown Watchdog active (30 min threshold)."

# Install live network speed reporter for tmux & dashboards
sudo tee /usr/local/bin/vm_net_speed.sh > /dev/null << 'EOF_NETSPEED'
#!/usr/bin/env bash
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
EOF_NETSPEED
sudo chmod +x /usr/local/bin/vm_net_speed.sh

if ! grep -q "GCP-TMUX-STYLE-V8" /etc/tmux.conf 2>/dev/null; then
  sudo sed -i '/# GCP Build Farm:/,$d;/# GCP-TMUX-STYLE-V/,$d' /etc/tmux.conf 2>/dev/null || true
  sudo tee -a /etc/tmux.conf > /dev/null << 'EOF_TMUX'

# GCP-TMUX-STYLE-V8: high-contrast dark footer, CPU/RAM/Net monitor, mouse scrolling, 50k scrollback, shortcuts
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
set -g status-left-length 30
set -g status-left "#[bg=colour39,fg=colour232,bold] #{?@build_label,#{@build_label},BUILD} #[bg=colour235,fg=colour39] "
set -g window-status-style "bg=colour235,fg=colour245"
set -g window-status-current-style "bg=colour238,fg=colour255,bold"
set -g status-right-length 160
set -g status-right "#[fg=colour245][#[fg=colour82,bold]Ctrl+b d#[fg=colour245] Detach | #[fg=colour196,bold]Ctrl+c#[fg=colour245] Stop] #[fg=colour242]| #(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b "
EOF_TMUX
fi

echo "============================================================"
echo " Verification:"
echo "============================================================"
df -h /
df -h /mnt/a16 /mnt/a17 2>/dev/null || true
swapon --show
ccache -s

echo "=== Host Setup Complete! ==="
