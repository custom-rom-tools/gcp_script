#!/usr/bin/env bash
# ==============================================================================
# idle_watchdog.sh - Automatic VM Idle Shutdown Watchdog
# Prevents runaway GCP compute billing (~$0.726/hr) by automatically powering off
# the instance after 30 minutes of complete inactivity.
#
# Activity checks:
# 1. ROM/Recovery Compilation, Sync & Upload:
#    soong_ui, soong_build, ninja, siso, ckati, m, rom_build.sh, repo, upload.py,
#    aria2c, git-remote-https, rclone, clang, rustc, javac, java, kotlinc,
#    metalava, aapt2, d8/r8, mka
# 2. Interactive User SSH Sessions:
#    Active established SSH connection with terminal activity within last 30 min.
#    (Idle/abandoned terminals with no typing for 30m do NOT block shutdown).
# ==============================================================================

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
    # 1. Check for active ROM/recovery build, sync, packaging, or upload processes
    local build_procs
    build_procs=$(pgrep -f '[s]oong_ui|[s]oong_build|[n]inja|[c]kati|[s]iso|/mnt/a17/evox/build/soong/bin/m |[r]om_build\.sh|[g]cp_build_runner|[g]cp_build_script|repo/main\.py|repo/repo|/usr/local/bin/repo|[u]pload\.py|[s]ignapk|[z]ipalign|[g]it-remote-https|[a]ria2c|[r]clone|[c]lang|[r]ustc|[j]avac|[j]ava|[k]otlinc|[m]etalava|[a]apt2|[s]oong_zip|/d8([[:space:]]|$)|/r8([[:space:]]|$)|/bin/mka' 2>/dev/null | wc -l)
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
