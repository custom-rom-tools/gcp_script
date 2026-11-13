#!/usr/bin/env bash
# ==============================================================================
# gcp_menu.sh: GCP Cloud Build, Orchestration & Download Manager
# Repository: custom-rom-tools/gcp_script
# Usage:
#   ~/gcp_menu.sh
#   or: bash /Volumes/Storage/rom/gcp_script/gcp_menu.sh
# ==============================================================================

INSTANCE="rom-build-farm"
ZONE="us-central1-a"
PROJECT="project-53211141-f92a-48c1-b88"
# Resolve paths relative to this script, even when setup_environment() was
# skipped or the menu was launched from another working directory.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_OUT_DIR="$HOME/Downloads"
LOCAL_LINKS_FILE="$HOME/.gcp_upload_links.log"
GENERAL_DISK="gcp-general-disk"
GENERAL_DEV_NAME="rom-general"

# Compute-Optimized 16-Core 64GB machine types for Custom ROM (AOSP/Clang) Building:
# 1. c2d-standard-16 : Compute-Optimized AMD Milan, max L3 cache & 3.7GHz boost (Fastest AOSP build)
# 2. t2d-standard-16 : Tau AMD Milan, 16 dedicated physical cores without SMT contention (Cheapest)
# 3. c2-standard-16  : Compute-Optimized Intel Cascade Lake, 3.8GHz all-core turbo (Intel fallback)
MACHINE_TYPES=(
    "c2d-standard-16"
    "t2d-standard-16"
    "c2-standard-16"
)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ==============================================================================
# Unified Step Runner with Animated Spinner and Progress Reporting
# ==============================================================================
function run_step() {
    local label="$1"
    shift

    if [ -t 1 ]; then
        echo -ne " ${CYAN}⏳${NC} ${label}... "
        local tmp_out
        tmp_out=$(mktemp /tmp/gcp_step.XXXXXX)

        "$@" > "$tmp_out" 2>&1 &
        local pid=$!

        local spin_chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
        local i=0
        while kill -0 "$pid" 2>/dev/null; do
            local char="${spin_chars:i++%${#spin_chars}:1}"
            echo -ne "\r\033[K ${CYAN}${char}${NC} ${label}... "
            sleep 0.1
        done

        wait "$pid"
        local exit_code=$?

        if [ $exit_code -eq 0 ]; then
            echo -e "\r\033[K ${GREEN}✔${NC} ${label} [${GREEN}OK${NC}]"
            [ -s "$tmp_out" ] && cat "$tmp_out" > /tmp/gcp_last_step.log
            rm -f "$tmp_out"
            return 0
        else
            if [[ "$label" == *"VM status"* || "$label" == *"VM state"* ]]; then
                echo -e "\r\033[K ${YELLOW}●${NC} ${label} [${YELLOW}OFF / TERMINATED${NC}]"
            else
                echo -e "\r\033[K ${RED}✖${NC} ${label} [${RED}FAILED (exit $exit_code)${NC}]"
                if [ -s "$tmp_out" ]; then
                    echo -e "${RED}--- Details: ---${NC}"
                    tail -n 10 "$tmp_out"
                    echo -e "${RED}----------------${NC}"
                fi
            fi
            [ -s "$tmp_out" ] && cat "$tmp_out" > /tmp/gcp_last_step.log
            rm -f "$tmp_out"
            return $exit_code
        fi
    else
        echo -n ">>> ${label}... "
        local tmp_out
        tmp_out=$(mktemp /tmp/gcp_step.XXXXXX)
        if "$@" > "$tmp_out" 2>&1; then
            echo "OK"
            rm -f "$tmp_out"
            return 0
        else
            if [[ "$label" == *"VM status"* || "$label" == *"VM state"* ]]; then
                echo "OFF / TERMINATED"
            else
                echo "FAILED"
                tail -n 10 "$tmp_out"
            fi
            rm -f "$tmp_out"
            return 1
        fi
    fi
}

# ==============================================================================
# Ultra-Fast Low-Latency Direct SSH & Connection Multiplexing Layer
# ==============================================================================
VM_IP_CACHE="/tmp/gcp_rom_vm_ip"
VM_STATE_CACHE="/tmp/gcp_rom_vm_state"
SSH_SOCK="/tmp/gcp_rom_ssh_%h_%p_%r"

function check_ssh_port() {
    local target_ip="$1"
    [ -z "$target_ip" ] && return 1
    nc -z -G 1 -w 1 "$target_ip" 22 2>/dev/null
}

function get_vm_state() {
    local now
    now=$(date +%s)
    if [ -f "$VM_STATE_CACHE" ]; then
        local c_st c_ip c_ts
        IFS=',' read -r c_st c_ip c_ts < "$VM_STATE_CACHE" 2>/dev/null || true
        if [ -n "$c_ts" ] && [ $((now - c_ts)) -lt 4 ]; then
            echo "$c_st,$c_ip"
            return 0
        fi
    fi

    if [ -f "$VM_IP_CACHE" ]; then
        local cached_ip
        cached_ip=$(cat "$VM_IP_CACHE" 2>/dev/null)
        if [ -n "$cached_ip" ] && check_ssh_port "$cached_ip"; then
            echo "RUNNING,$cached_ip,$now" > "$VM_STATE_CACHE" 2>/dev/null || true
            echo "RUNNING,$cached_ip"
            return 0
        fi
    fi

    local res
    res=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --format="csv[no-heading](status,networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null || echo "UNKNOWN,")
    local st="${res%%,*}"
    local ip="${res##*,}"

    if [ "$st" = "RUNNING" ] && [ -n "$ip" ]; then
        echo "$ip" > "$VM_IP_CACHE"
    else
        rm -f "$VM_IP_CACHE" 2>/dev/null || true
    fi
    echo "$st,$ip,$now" > "$VM_STATE_CACHE" 2>/dev/null || true
    echo "$st,$ip"
}

function is_vm_running() {
    local state
    state=$(get_vm_state)
    local st="${state%%,*}"
    [ "$st" = "RUNNING" ]
}

function check_vm_online() {
    is_vm_running
}

function require_vm_running() {
    local context="${1:-perform this action}"
    run_step "Checking GCP VM status" check_vm_online
    if [ $? -ne 0 ]; then
        echo ""
        echo -e "${YELLOW}=================================================================${NC}"
        echo -e " ${BOLD}>>> [VM STATUS: TERMINATED / OFF]${NC}"
        echo -e "     Instance '$INSTANCE' is currently powered OFF."
        echo -e "     Cannot $context while VM is powered down."
        echo -e "     Compute billing is paused (~$0.726/hr saved)."
        echo ""
        echo -e "     Tip: Select Option 22 (Start VM) or start a build to power it on."
        echo -e "${YELLOW}=================================================================${NC}"
        echo ""
        read -rp "Press Enter to return to main menu..."
        return 1
    fi
    return 0
}

function get_vm_ip() {
    if [ -f "$VM_IP_CACHE" ]; then
        local cached_ip
        cached_ip=$(cat "$VM_IP_CACHE" 2>/dev/null)
        if [ -n "$cached_ip" ] && check_ssh_port "$cached_ip"; then
            echo "$cached_ip"
            return 0
        fi
    fi

    local state
    state=$(get_vm_state)
    local ip="${state##*,}"
    if [ -n "$ip" ]; then
        echo "$ip"
        return 0
    fi
    return 1
}

function vm_exec() {
    local ip
    ip=$(get_vm_ip 2>/dev/null)
    if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
        ssh -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o ControlPersist=15m \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=3 \
            -i "$HOME/.ssh/google_compute_engine" "$USER@$ip" "$@"
    else
        gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" -- "$@"
    fi
}

# Disable every common xterm mouse-reporting mode. If a previous tmux/htop
# session was killed mid-run the host terminal can stay in mouse mode, making
# click-drag send escape codes instead of selecting text.
function disable_mouse_reporting() {
    printf '\033[?9l\033[?1000l\033[?1001l\033[?1002l\033[?1003l\033[?1005l\033[?1006l\033[?1015l\033[?1016l' 2>/dev/null || true
}

# Restore the local terminal after an interactive SSH/tmux session. If tmux or
# htop was killed mid-session it can leave xterm mouse reporting enabled, which
# makes click-drag paste mouse escape codes instead of selecting text.
function restore_local_terminal() {
    stty sane 2>/dev/null || true
    disable_mouse_reporting
    printf '\033[?1049l\033[0m' 2>/dev/null || true
}

# Clear the visible screen and scrollback. Falls back to raw ANSI when tput or
# terminfo is unavailable (otherwise a full-screen log can leave the menu
# header scrolled out of view).
function clear_screen() {
    if [ -n "${TERM:-}" ] && command -v tput >/dev/null 2>&1; then
        tput clear 2>/dev/null || true
    fi
    printf '\033[H\033[2J\033[3J' 2>/dev/null || true
}

function vm_exec_tty() {
    local ip rc
    ip=$(get_vm_ip 2>/dev/null)
    if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
        ssh -t -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o ControlPersist=15m \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=3 \
            -i "$HOME/.ssh/google_compute_engine" "$USER@$ip" "$@"
        rc=$?
    else
        gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" -- -t "$@"
        rc=$?
    fi
    restore_local_terminal
    return $rc
}

function vm_scp() {
    local ip
    ip=$(get_vm_ip 2>/dev/null)
    if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
        scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o ControlPersist=15m \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=3 \
            -i "$HOME/.ssh/google_compute_engine" -r "$@"
    else
        gcloud compute scp --recurse "$@" --zone="$ZONE" --project="$PROJECT"
    fi
}

# ==============================================================================
# Disk Metadata, Resolution, Mounting & Formatting Helpers
# ==============================================================================
function normalize_disk_name() {
    case "$1" in
        a16) echo "gcp-rom-a16-disk" ;;
        a17) echo "gcp-rom-a17-disk" ;;
        recovery|rec) echo "gcp-recovery-disk" ;;
        general|shared) echo "$GENERAL_DISK" ;;
        *) echo "$1" ;;
    esac
}

function get_disk_meta() {
    local target_disk="$1"
    META_DEV_NAME="rom-storage"
    META_DEFAULT_MP=""
    META_SIZE_GB=500
    META_DESC="Secondary storage for Android ROM builds"
    META_IS_GENERAL=1

    if [ "$target_disk" = "gcp-rom-a16-disk" ]; then
        META_DEV_NAME="rom-a16"
        META_DEFAULT_MP="/mnt/a16"
        META_SIZE_GB=400
        META_DESC="Dedicated 400GB SSD storage for Android 16 ROM builds"
        META_IS_GENERAL=0
    elif [ "$target_disk" = "gcp-rom-a17-disk" ]; then
        META_DEV_NAME="rom-a17"
        META_DEFAULT_MP="/mnt/a17"
        META_SIZE_GB=400
        META_DESC="Dedicated 400GB SSD storage for Android 17 ROM builds"
        META_IS_GENERAL=0
    elif [ "$target_disk" = "gcp-recovery-disk" ]; then
        META_DEV_NAME="rom-recovery"
        META_DEFAULT_MP="/mnt/recovery"
        META_SIZE_GB=400
        META_DESC="Dedicated 400GB storage for Recovery builds (OFRP + TWRP)"
        META_IS_GENERAL=0
    elif [ "$target_disk" = "$GENERAL_DISK" ]; then
        META_DEV_NAME="$GENERAL_DEV_NAME"
        META_DEFAULT_MP=""
        META_SIZE_GB=500
        META_DESC="Shared 500GB general SSD storage for ROM and Recovery builds"
        META_IS_GENERAL=1
    fi
}

function get_disk_role_label() {
    case "$1" in
        gcp-rom-a16-disk)  echo "A16 Specific -> /mnt/a16" ;;
        gcp-rom-a17-disk)  echo "A17 Specific -> /mnt/a17" ;;
        gcp-recovery-disk) echo "Recovery Specific -> /mnt/recovery" ;;
        "$GENERAL_DISK")   echo "General Shared -> mounts per build" ;;
        *)                 echo "Custom Secondary" ;;
    esac
}

function get_attached_device_name() {
    local target_disk="$1"
    local fallback_dev="$2"
    local actual
    actual=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --format="csv[no-heading](disks[].source.basename(),disks[].deviceName)" 2>/dev/null \
        | awk -F',' -v d="$target_disk" '$1 == d {print $2; exit}' | tr -d '\r')
    echo "${actual:-$fallback_dev}"
}

function build_vm_resolve_dev_cmd() {
    local target_disk="$1"
    local fallback_dev="$2"
    local actual_dev="$3"
    echo "DEV=''; \
for c in '/dev/disk/by-id/google-$actual_dev' '/dev/disk/by-id/google-$fallback_dev' '/dev/disk/by-id/google-$target_disk'; do \
    if [ -b \"\$c\" ]; then DEV=\"\$c\"; break; fi; \
done; \
if [ -z \"\$DEV\" ]; then \
    echo 'Error: Block device for $target_disk not detected on VM!'; \
    exit 1; \
fi; \
REAL_DEV=\$(readlink -f \"\$DEV\")"
}

function resolve_build_disk() {
    local branch="$1"
    local specific_disk="gcp-rom-a16-disk"
    local target_mp="/mnt/a16"
    if [ "$branch" = "aosp-17" ]; then
        specific_disk="gcp-rom-a17-disk"
        target_mp="/mnt/a17"
    elif [ "$branch" = "recovery" ]; then
        specific_disk="gcp-recovery-disk"
        target_mp="/mnt/recovery"
    fi

    local project_disks
    project_disks=$(gcloud compute disks list --project="$PROJECT" --zones="$ZONE" --format="value(name)" 2>/dev/null || echo "")

    # Order: ROM/recovery-specific disk first -> general shared disk fallback
    if echo "$project_disks" | grep -qx "$specific_disk"; then
        RESOLVED_DISK="$specific_disk"
        RESOLVED_MP="$target_mp"
        RESOLVED_MODE="specific"
        return 0
    fi

    if echo "$project_disks" | grep -qx "$GENERAL_DISK"; then
        RESOLVED_DISK="$GENERAL_DISK"
        RESOLVED_MP="$target_mp"
        RESOLVED_MODE="general"
        return 0
    fi

    # Backward-compatible recovery fallback if an existing specific ROM disk is attached
    if [ "$branch" = "recovery" ]; then
        local attached_disks
        attached_disks=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
            --format="value(disks[].source.basename())" 2>/dev/null || echo "")
        if [[ "$attached_disks" == *"gcp-rom-a17-disk"* ]]; then
            RESOLVED_DISK="gcp-rom-a17-disk"
            RESOLVED_MP="/mnt/a17"
            RESOLVED_MODE="shared-recovery"
            return 0
        elif [[ "$attached_disks" == *"gcp-rom-a16-disk"* ]]; then
            RESOLVED_DISK="gcp-rom-a16-disk"
            RESOLVED_MP="/mnt/a16"
            RESOLVED_MODE="shared-recovery"
            return 0
        fi
    fi

    # Neither specific nor general disk exists yet -> default to general shared disk
    RESOLVED_DISK="$GENERAL_DISK"
    RESOLVED_MP="$target_mp"
    RESOLVED_MODE="general"
}

function mount_disk_on_vm() {
    local target_disk="$1"
    local mp="$2"
    get_disk_meta "$target_disk"
    local actual_dev
    actual_dev=$(get_attached_device_name "$target_disk" "$META_DEV_NAME")
    local resolve_cmd
    resolve_cmd=$(build_vm_resolve_dev_cmd "$target_disk" "$META_DEV_NAME" "$actual_dev")

    vm_exec "$resolve_cmd; \
if ! sudo blkid \"\$REAL_DEV\" 2>/dev/null | grep -q 'TYPE=\"ext4\"'; then \
    sudo mkfs.ext4 -F -m 1 -E lazy_itable_init=0,lazy_journal_init=0 \"\$REAL_DEV\"; \
fi; \
while IFS= read -r omp; do \
    if [ -n \"\$omp\" ] && [ \"\$omp\" != '$mp' ]; then \
        sudo swapoff \"\${omp}/swapfile\" 2>/dev/null || true; \
        sudo umount \"\$omp\" 2>/dev/null || sudo umount -l \"\$omp\" 2>/dev/null || true; \
    fi; \
done < <(findmnt -rn -S \"\$REAL_DEV\" -o TARGET 2>/dev/null || true); \
if [ -L '$mp' ]; then sudo rm -f '$mp'; fi; \
sudo mkdir -p '$mp'; \
if mountpoint -q '$mp'; then \
    CUR_SRC=\$(findmnt -rn -T '$mp' -o SOURCE 2>/dev/null || true); \
    CUR_REAL=\$(readlink -f \"\$CUR_SRC\" 2>/dev/null || echo \"\$CUR_SRC\"); \
    if [ \"\$CUR_REAL\" != \"\$REAL_DEV\" ]; then \
        sudo swapoff '$mp/swapfile' 2>/dev/null || true; \
        sudo umount '$mp' 2>/dev/null || sudo umount -l '$mp' 2>/dev/null || true; \
    fi; \
fi; \
if ! mountpoint -q '$mp'; then \
    sudo mount -o discard,defaults,noatime,nofail \"\$REAL_DEV\" '$mp'; \
fi; \
sudo resize2fs \"\$REAL_DEV\" 2>/dev/null || true; \
sudo chown \$USER:\$USER '$mp' 2>/dev/null || true; \
[ ! -f '$mp/swapfile' ] || sudo swapon '$mp/swapfile' 2>/dev/null || true; \
[ -L '$mp/source' ] && rm -f '$mp/source' 2>/dev/null || true"
}

function unmount_disk_on_vm() {
    local target_disk="$1"
    get_disk_meta "$target_disk"
    local actual_dev
    actual_dev=$(get_attached_device_name "$target_disk" "$META_DEV_NAME")
    local fallback_mp="${META_DEFAULT_MP:-/mnt/a16}"

    vm_exec "DEV=''; \
for c in '/dev/disk/by-id/google-$actual_dev' '/dev/disk/by-id/google-$META_DEV_NAME' '/dev/disk/by-id/google-$target_disk'; do \
    if [ -b \"\$c\" ]; then DEV=\"\$c\"; break; fi; \
done; \
if [ -n \"\$DEV\" ]; then \
    REAL_DEV=\$(readlink -f \"\$DEV\"); \
    while IFS= read -r omp; do \
        [ -z \"\$omp\" ] && continue; \
        sudo swapoff \"\${omp}/swapfile\" 2>/dev/null || true; \
        sudo umount \"\$omp\" 2>/dev/null || sudo umount -l \"\$omp\" 2>/dev/null || true; \
        sudo sed -i \"\\\\|\$omp|d\" /etc/fstab 2>/dev/null || true; \
    done < <(findmnt -rn -S \"\$REAL_DEV\" -o TARGET 2>/dev/null || true); \
elif mountpoint -q '$fallback_mp' 2>/dev/null; then \
    sudo swapoff '$fallback_mp/swapfile' 2>/dev/null || true; \
    sudo umount -l '$fallback_mp' 2>/dev/null || true; \
fi"
}

function create_and_attach_disk() {
    local target_disk
    target_disk=$(normalize_disk_name "${1:-$GENERAL_DISK}")
    get_disk_meta "$target_disk"
    local size_gb="${2:-$META_SIZE_GB}"
    local description="${3:-$META_DESC}"
    local dev_name="$META_DEV_NAME"
    local is_general="$META_IS_GENERAL"
    local mp="$META_DEFAULT_MP"

    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD} Creating & Auto-Linking ROM Disk: $target_disk (${size_gb}GB)${NC}"
    echo -e "${CYAN}============================================================${NC}"

    # 1. Create disk if not exists, otherwise grow it when the requested size is larger.
    if gcloud compute disks describe "$target_disk" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
        local current_size
        current_size=$(gcloud compute disks describe "$target_disk" --zone="$ZONE" --project="$PROJECT" \
            --format="value(sizeGb)" 2>/dev/null | tr -d '\r')
        echo -e " ${GREEN}✔${NC} Disk '$target_disk' already exists in GCP (${current_size}GB)."
        if [[ "$current_size" =~ ^[0-9]+$ ]] && [ "$current_size" -lt "$size_gb" ]; then
            echo -e " ${CYAN}>>> Growing '$target_disk' from ${current_size}GB to ${size_gb}GB...${NC}"
            if run_step "Resizing $target_disk from ${current_size}GB to ${size_gb}GB" \
                gcloud compute disks resize "$target_disk" \
                    --project="$PROJECT" \
                    --zone="$ZONE" \
                    --size="${size_gb}GB" --quiet; then
                echo -e " ${GREEN}✔${NC} Disk resized. The filesystem is grown automatically when it is mounted."
            else
                echo -e "${RED}>>> Resize failed (regional SSD/DISK quota?). Keeping ${current_size}GB.${NC}"
                size_gb="$current_size"
            fi
        fi
    else
        local disk_type="pd-balanced"
        if ! run_step "Creating ${size_gb}GB $disk_type SSD disk '$target_disk' in $ZONE" \
            gcloud compute disks create "$target_disk" \
                --project="$PROJECT" \
                --zone="$ZONE" \
                --size="${size_gb}GB" \
                --type="$disk_type" \
                --description="$description" --quiet; then

            echo -e "\n${YELLOW}>>> Notice: Regional SSD quota ('SSD_TOTAL_GB' = 500GB) may be exceeded if another SSD exists.${NC}"
            echo -e "    Free-tier accounts only support up to 500GB total SSD across all disks."
            echo -e "    Delete unused SSD disks via Disk Manager or create '$target_disk' as standard disk (pd-standard HDD)."
            read -rp "Create as pd-standard HDD? (y/N): " use_std
            if [[ ! "$use_std" =~ ^[Yy]$ ]]; then
                echo -e "${RED}Aborting disk creation.${NC}"
                return 1
            fi
            disk_type="pd-standard"
            if ! run_step "Creating ${size_gb}GB $disk_type disk '$target_disk' in $ZONE" \
                gcloud compute disks create "$target_disk" \
                    --project="$PROJECT" \
                    --zone="$ZONE" \
                    --size="${size_gb}GB" \
                    --type="$disk_type" \
                    --description="$description" --quiet; then
                echo -e "${RED}Failed to create disk $target_disk.${NC}"
                return 1
            fi
        fi
    fi

    # 2. Attach disk to VM instance if not already attached
    local attached_disks
    attached_disks=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --format="value(disks[].source.basename())" 2>/dev/null || echo "")
    if ! echo "$attached_disks" | grep -qx "$target_disk"; then
        if ! run_step "Attaching $target_disk to $INSTANCE (device-name: $dev_name)" \
            gcloud compute instances attach-disk "$INSTANCE" \
                --disk="$target_disk" \
                --device-name="$dev_name" \
                --zone="$ZONE" \
                --project="$PROJECT" \
                --mode=rw --quiet; then
            echo -e "${RED}Failed to attach disk $target_disk.${NC}"
            return 1
        fi
    else
        echo -e " ${GREEN}✔${NC} Disk '$target_disk' is already attached to $INSTANCE."
    fi

    # 3. Ensure VM is running to format (and mount if ROM/recovery-specific)
    local status
    status=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format="value(status)" 2>/dev/null || echo "UNKNOWN")
    if [ "$status" != "RUNNING" ]; then
        run_step "Starting VM to initialize disk filesystem" start_vm_with_failover
    fi

    if [ "$is_general" -eq 1 ]; then
        local actual_dev
        actual_dev=$(get_attached_device_name "$target_disk" "$dev_name")
        local resolve_cmd
        resolve_cmd=$(build_vm_resolve_dev_cmd "$target_disk" "$dev_name" "$actual_dev")
        run_step "Formatting general disk (ext4) if needed (kept unmounted until build)" \
            vm_exec "$resolve_cmd; \
if ! sudo blkid \"\$REAL_DEV\" 2>/dev/null | grep -q 'TYPE=\"ext4\"'; then \
    sudo mkfs.ext4 -F -m 1 -E lazy_itable_init=0,lazy_journal_init=0 \"\$REAL_DEV\"; \
    TMP_MP=\$(mktemp -d /tmp/gcp_init.XXXXXX); \
    sudo mount \"\$REAL_DEV\" \"\$TMP_MP\" && sudo chown \$USER:\$USER \"\$TMP_MP\" && sudo umount \"\$TMP_MP\"; \
    rmdir \"\$TMP_MP\" 2>/dev/null || true; \
fi"
        echo -e "${GREEN}>>> General disk '$target_disk' is initialized and ready!${NC}"
        echo -e "${CYAN}    It stays unmounted at boot and will auto-mount to /mnt/a16, /mnt/a17, or /mnt/recovery when a build starts.${NC}"
    else
        run_step "Initializing filesystem and mounting at $mp" \
            mount_disk_on_vm "$target_disk" "$mp"
        echo -e "${GREEN}>>> Disk $target_disk is formatted and ready at $mp!${NC}"
    fi
}

function detach_and_delete_disk() {
    local target_disk
    target_disk=$(normalize_disk_name "$1")

    if [ -z "$target_disk" ]; then
        echo -e "${CYAN}============================================================${NC}"
        echo -e "${BOLD}       SELECT ROM DISK TO DELETE (STOP STORAGE BILLING)     ${NC}"
        echo -e "${CYAN}============================================================${NC}"

        local disks=()
        while IFS= read -r line; do
            [ -n "$line" ] && disks+=("$line")
        done < <(gcloud compute disks list --project="$PROJECT" --zones="$ZONE" \
            --filter="name != '$INSTANCE' AND name != 'gcp-boot-ccache-disk'" \
            --format="value(name)")

        if [ ${#disks[@]} -eq 0 ]; then
            echo -e "${YELLOW}>>> No secondary ROM disks found in project $PROJECT ($ZONE).${NC}"
            echo -e "${GREEN}>>> You only have the Free-Tier boot disk ($INSTANCE). Zero extra storage billing!${NC}"
            return 0
        fi

        echo -e "${BOLD}Select a ROM Disk to Delete:${NC}"
        local idx=1
        for d in "${disks[@]}"; do
            local size
            size=$(gcloud compute disks describe "$d" --zone="$ZONE" --project="$PROJECT" --format="value(sizeGb)" 2>/dev/null || echo "400")
            local attached
            attached=$(gcloud compute disks describe "$d" --zone="$ZONE" --project="$PROJECT" --format="value(users[].basename().list())" 2>/dev/null || echo "")
            local status_label=""
            if [ -n "$attached" ]; then
                status_label=" ${YELLOW}[Attached to $attached]${NC}"
            else
                status_label=" ${GREEN}[Unattached]${NC}"
            fi
            echo -e " ${BOLD}$idx)${NC} ${RED}$d${NC} (${size} GB - $(get_disk_role_label "$d"))$status_label"
            ((idx++))
        done
        echo -e " ${BOLD}0)${NC} Cancel (Keep all disks)"
        echo ""
        read -rp "Select disk number to delete [1-${#disks[@]} or 0]: " sel_idx

        if [ "$sel_idx" = "0" ] || [ -z "$sel_idx" ]; then
            echo -e "${GREEN}>>> Aborted. No disks deleted.${NC}"
            return 0
        elif [[ "$sel_idx" =~ ^[0-9]+$ ]] && [ "$sel_idx" -le "${#disks[@]}" ] && [ "$sel_idx" -gt 0 ]; then
            target_disk="${disks[$((sel_idx-1))]}"
        else
            echo -e "${RED}>>> Invalid selection.${NC}"
            return 1
        fi
    fi

    if [ "$target_disk" = "$INSTANCE" ] || [ "$target_disk" = "gcp-boot-ccache-disk" ]; then
        echo -e "${RED}ERROR: '$target_disk' is your VM boot disk! It cannot be deleted.${NC}"
        return 1
    fi

    if ! gcloud compute disks describe "$target_disk" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
        echo -e "${RED}ERROR: Disk '$target_disk' does not exist in GCP.${NC}"
        return 1
    fi

    echo ""
    echo -e "${RED}============================================================${NC}"
    echo -e "${BOLD} ⚠️  CONFIRM PERMANENT DELETION: $target_disk${NC}"
    echo -e "${RED}============================================================${NC}"
    echo -e "${YELLOW}This action will:${NC}"
    echo -e " 1. Unmount $target_disk and turn off swap on the VM."
    echo -e " 2. Detach $target_disk from $INSTANCE."
    echo -e " 3. ${RED}PERMANENTLY DELETE $target_disk from GCP.${NC}"
    echo -e " 4. ${GREEN}Completely eliminate all storage billing for this disk.${NC}"
    echo ""
    echo -e "${BOLD}Note:${NC} Your 50GB boot disk (OS, packages, ccache, SF keys) is completely preserved."
    echo -e "You can recreate and auto-link a fresh ROM disk anytime with 1 click."
    echo ""
    read -rp "Type 'DELETE' to confirm deletion of $target_disk: " confirm_del
    if [ "$confirm_del" != "DELETE" ]; then
        echo -e "${GREEN}>>> Aborted. No changes were made.${NC}"
        return 0
    fi

    if is_vm_running; then
        run_step "Unmounting $target_disk on VM" unmount_disk_on_vm "$target_disk"
    fi

    local attached_users
    attached_users=$(gcloud compute disks describe "$target_disk" --zone="$ZONE" --project="$PROJECT" --format="value(users[].basename().list())" 2>/dev/null || echo "")
    if [ -n "$attached_users" ]; then
        run_step "Detaching $target_disk from $INSTANCE" \
            gcloud compute instances detach-disk "$INSTANCE" --disk="$target_disk" --zone="$ZONE" --project="$PROJECT" --quiet
    fi

    run_step "Permanently deleting $target_disk from GCP (Stop storage billing)" \
        gcloud compute disks delete "$target_disk" --zone="$ZONE" --project="$PROJECT" --quiet

    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN} ✅ Disk $target_disk has been permanently deleted!${NC}"
    echo -e "${GREEN}    Storage billing for this disk has stopped immediately.${NC}"
    echo -e "${GREEN}============================================================${NC}"
}

function format_disk_action() {
    local target_disk=""
    local auto_yes="false"

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --yes|-y|true) auto_yes="true"; shift ;;
            *) target_disk=$(normalize_disk_name "$1"); shift ;;
        esac
    done

    if [ -z "$target_disk" ]; then
        echo -e "${CYAN}============================================================${NC}"
        echo -e "${BOLD}         SELECT ROM / GENERAL DISK TO FORMAT (EXT4)         ${NC}"
        echo -e "${CYAN}============================================================${NC}"

        local disks=()
        while IFS= read -r line; do
            [ -n "$line" ] && disks+=("$line")
        done < <(gcloud compute disks list --project="$PROJECT" --zones="$ZONE" \
            --filter="name != '$INSTANCE' AND name != 'gcp-boot-ccache-disk'" \
            --format="value(name)")

        if [ ${#disks[@]} -eq 0 ]; then
            echo -e "${YELLOW}>>> No secondary disks found in project $PROJECT ($ZONE).${NC}"
            return 0
        fi

        echo -e "${BOLD}Available Disks to Format:${NC}"
        local idx=1
        for d in "${disks[@]}"; do
            local size
            size=$(gcloud compute disks describe "$d" --zone="$ZONE" --project="$PROJECT" --format="value(sizeGb)" 2>/dev/null || echo "")
            local attached
            attached=$(gcloud compute disks describe "$d" --zone="$ZONE" --project="$PROJECT" --format="value(users[].basename().list())" 2>/dev/null || echo "")
            local status_label=""
            if [ -n "$attached" ]; then
                status_label=" ${YELLOW}[Attached to $attached]${NC}"
            else
                status_label=" ${GREEN}[Unattached]${NC}"
            fi
            echo -e " ${BOLD}$idx)${NC} ${RED}$d${NC} (${size} GB - $(get_disk_role_label "$d"))$status_label"
            ((idx++))
        done
        echo -e " ${BOLD}0)${NC} Cancel"
        echo ""
        read -rp "Select disk number to format [1-${#disks[@]} or 0]: " sel_idx

        if [ "$sel_idx" = "0" ] || [ -z "$sel_idx" ]; then
            echo -e "${GREEN}>>> Aborted. No disks formatted.${NC}"
            return 0
        elif [[ "$sel_idx" =~ ^[0-9]+$ ]] && [ "$sel_idx" -le "${#disks[@]}" ] && [ "$sel_idx" -gt 0 ]; then
            target_disk="${disks[$((sel_idx-1))]}"
        else
            echo -e "${RED}>>> Invalid selection.${NC}"
            return 1
        fi
    fi

    if [ "$target_disk" = "$INSTANCE" ] || [ "$target_disk" = "gcp-boot-ccache-disk" ]; then
        echo -e "${RED}ERROR: '$target_disk' is your VM boot disk! It cannot be formatted.${NC}"
        return 1
    fi

    if ! gcloud compute disks describe "$target_disk" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
        echo -e "${RED}ERROR: Disk '$target_disk' does not exist in GCP.${NC}"
        return 1
    fi

    if [ "$auto_yes" != "true" ]; then
        echo ""
        echo -e "${RED}============================================================${NC}"
        echo -e "${BOLD} ⚠️  CONFIRM DISK FORMAT: $target_disk${NC}"
        echo -e "${RED}============================================================${NC}"
        echo -e "${YELLOW}This action will:${NC}"
        echo -e " 1. Unmount $target_disk from any active ROM/Recovery mount point and disable swap on it."
        echo -e " 2. ${RED}PERMANENTLY ERASE ALL source trees, out/ artifacts, and files on $target_disk.${NC}"
        echo -e " 3. Create a fresh clean ext4 filesystem ready for your next ROM or Recovery build."
        echo ""
        echo -e "${BOLD}Note:${NC} Your 50GB boot disk (OS, packages, ccache, SF keys) is NOT affected."
        echo ""
        read -rp "Type 'FORMAT' to confirm formatting $target_disk: " confirm_fmt
        if [ "$confirm_fmt" != "FORMAT" ]; then
            echo -e "${GREEN}>>> Aborted. No changes were made.${NC}"
            return 0
        fi
    fi

    get_disk_meta "$target_disk"
    run_step "Ensuring $target_disk is attached to $INSTANCE" ensure_disk_attached "$target_disk"
    if ! is_vm_running; then
        run_step "Powering on VM instance to format $target_disk" start_vm_with_failover
    fi

    local actual_dev
    actual_dev=$(get_attached_device_name "$target_disk" "$META_DEV_NAME")
    local resolve_cmd
    resolve_cmd=$(build_vm_resolve_dev_cmd "$target_disk" "$META_DEV_NAME" "$actual_dev")

    run_step "Unmounting and formatting $target_disk as clean ext4" \
        vm_exec "$resolve_cmd; \
while IFS= read -r omp; do \
    [ -z \"\$omp\" ] && continue; \
    sudo swapoff \"\${omp}/swapfile\" 2>/dev/null || true; \
    sudo umount \"\$omp\" 2>/dev/null || sudo umount -l \"\$omp\" 2>/dev/null || true; \
done < <(findmnt -rn -S \"\$REAL_DEV\" -o TARGET 2>/dev/null || true); \
sudo mkfs.ext4 -F -m 1 -E lazy_itable_init=0,lazy_journal_init=0 \"\$REAL_DEV\"; \
TMP_MP=\$(mktemp -d /tmp/gcp_fmt.XXXXXX); \
sudo mount \"\$REAL_DEV\" \"\$TMP_MP\" && sudo chown \$USER:\$USER \"\$TMP_MP\" && sudo umount \"\$TMP_MP\"; \
rmdir \"\$TMP_MP\" 2>/dev/null || true"

    if [ "$META_IS_GENERAL" -eq 0 ] && [ -n "$META_DEFAULT_MP" ]; then
        run_step "Remounting formatted specific disk at $META_DEFAULT_MP" \
            mount_disk_on_vm "$target_disk" "$META_DEFAULT_MP"
    fi

    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN} ✅ Disk $target_disk has been formatted cleanly (ext4)!${NC}"
    if [ "$META_IS_GENERAL" -eq 1 ]; then
        echo -e "${CYAN}    Kept unmounted until a build starts (will mount at target /mnt/a16, /mnt/a17, or /mnt/recovery).${NC}"
    else
        echo -e "${CYAN}    Mounted and ready at $META_DEFAULT_MP.${NC}"
    fi
    echo -e "${GREEN}============================================================${NC}"
}

function ensure_disk_attached() {
    local target_disk
    target_disk=$(normalize_disk_name "${1:-$GENERAL_DISK}")
    get_disk_meta "$target_disk"
    local dev_name="$META_DEV_NAME"
    local size_gb="$META_SIZE_GB"
    local desc="$META_DESC"

    # Fast check: If VM is running and disk block device is present, skip GCP API
    if is_vm_running; then
        if vm_exec "test -b '/dev/disk/by-id/google-$dev_name' || test -b '/dev/disk/by-id/google-$target_disk'" 2>/dev/null; then
            echo -e " ${GREEN}✔${NC} Disk '$target_disk' is attached and recognized."
            return 0
        fi
    fi

    # Check if disk exists in GCP project
    if ! gcloud compute disks describe "$target_disk" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
        echo -e "${YELLOW}>>> Target disk '$target_disk' does not exist in GCP.${NC}"
        echo -e "${CYAN}>>> Auto-creating, linking, and initializing ${size_gb}GB disk '$target_disk'...${NC}"
        create_and_attach_disk "$target_disk" "$size_gb" "$desc"
        return $?
    fi

    local attached_disks
    attached_disks=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --format="value(disks[].source.basename())" 2>/dev/null || echo "")

    if ! echo "$attached_disks" | grep -qx "$target_disk"; then
        run_step "Attaching $target_disk to $INSTANCE" \
            gcloud compute instances attach-disk "$INSTANCE" \
                --disk="$target_disk" \
                --device-name="$dev_name" \
                --zone="$ZONE" \
                --project="$PROJECT" \
                --mode=rw --quiet
    else
        echo -e " ${GREEN}✔${NC} Disk '$target_disk' is attached to $INSTANCE."
    fi
}

function diagnose_ssh_failure() {
    local elapsed="$1"
    local state st ip
    state=$(get_vm_state)
    st="${state%%,*}"
    ip="${state##*,}"

    echo ""
    echo -e "${RED}=================================================================${NC}"
    echo -e "${RED}>>> SSH did not become ready after ${elapsed}s${NC}"
    echo -e "    Instance state : ${BOLD}${st:-UNKNOWN}${NC}"
    echo -e "    Instance IP    : ${BOLD}${ip:-(none)}${NC}"

    if [ "$st" != "RUNNING" ]; then
        echo -e "${YELLOW}    The VM is not RUNNING, so sshd cannot accept connections.${NC}"
        echo -e "${RED}=================================================================${NC}"
        return 1
    fi

    if [ -z "$ip" ]; then
        echo -e "${YELLOW}    The VM has no external IP yet. Wait a few seconds and retry.${NC}"
        echo -e "${RED}=================================================================${NC}"
        return 1
    fi

    if ! check_ssh_port "$ip"; then
        echo -e "    TCP port 22    : ${RED}closed${NC}"
        echo -e "${YELLOW}    sshd is not listening yet (still booting) or port 22 is blocked.${NC}"
        echo "    Inspect the boot log:"
        echo "      gcloud compute instances get-serial-port-output $INSTANCE --zone=$ZONE --project=$PROJECT | tail -40"
        echo -e "${RED}=================================================================${NC}"
        return 1
    fi

    local ssh_err ssh_rc
    ssh_err=$(ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
        -i "$HOME/.ssh/google_compute_engine" "$USER@$ip" "true" 2>&1)
    ssh_rc=$?
    echo -e "    TCP port 22    : ${GREEN}open${NC}"
    echo -e "    SSH result     : ${ssh_err:-unknown} (exit $ssh_rc)"

    if [[ "$ssh_err" == *"Permission denied"* ]]; then
        echo -e "${RED}    The guest rejected the SSH key.${NC}"
        echo -e "${YELLOW}    Most common cause: the boot disk is 100% full, so cloud-init cannot${NC}"
        echo -e "${YELLOW}    write ~/.ssh/authorized_keys (the metadata script fails too).${NC}"
        if command -v timeout >/dev/null 2>&1; then
            local serial
            serial=$(timeout 20 gcloud compute instances get-serial-port-output "$INSTANCE" \
                --zone="$ZONE" --project="$PROJECT" 2>/dev/null | tail -n 150 || true)
            if grep -qi "No space left on device" <<<"$serial"; then
                echo -e "${RED}    CONFIRMED from the serial console: 'No space left on device'.${NC}"
                echo -e "${YELLOW}    Free boot-disk space (ccache/logs) or grow the boot disk, then retry.${NC}"
            fi
        fi
    fi
    echo -e "${RED}=================================================================${NC}"
    return 1
}

function wait_for_ssh() {
    local timeout="${1:-180}"
    local start now elapsed state st ip
    echo -e "${YELLOW}>>> Waiting for SSH service to become ready (up to ${timeout}s)...${NC}"
    # A stopped VM may receive a different ephemeral NAT IP at the next boot.
    # Drop both caches so the first SSH probe resolves the current address
    # from the Compute API instead of reusing the pre-reboot state.
    rm -f "$VM_IP_CACHE" "$VM_STATE_CACHE" 2>/dev/null || true
    # A cold boot on this image needs ~20-60s before sshd accepts connections,
    # but a full boot disk or missing key makes it fail forever.  Bound the
    # wait and explain the failure instead of hanging the menu.
    start=$(date +%s)
    while :; do
        now=$(date +%s)
        elapsed=$((now - start))
        state=$(get_vm_state)
        st="${state%%,*}"
        ip="${state##*,}"

        if [ "$st" = "RUNNING" ] && [ -n "$ip" ] && vm_exec "true" >/dev/null 2>&1; then
            echo -e "${GREEN}>>> SSH connection established (${elapsed}s).${NC}"
            # Ensure Auto-Idle Watchdog is active on boot
            vm_exec "sudo systemctl is-active --quiet idle-shutdown.service || (sudo systemctl daemon-reload && sudo systemctl enable --now idle-shutdown.service && sudo systemctl restart idle-shutdown.service) 2>/dev/null || true" >/dev/null 2>&1 &

            # The boot disk holds the OS, apt cache and ccache.  If it fills up,
            # the next boot cannot inject SSH keys.  Warn while SSH still works.
            local avail_mb
            avail_mb=$(vm_exec "df -Pm / | awk 'NR==2{print \$4}'" 2>/dev/null | tr -dc '0-9')
            if [ -n "$avail_mb" ] && [ "$avail_mb" -lt 4096 ] 2>/dev/null; then
                echo -e "${RED}>>> WARNING: only ${avail_mb}MB free on the VM boot disk.${NC}"
                echo -e "${YELLOW}    Run 'ccache -C' or grow the boot disk before building.${NC}"
            fi
            return 0
        fi

        if [ "$elapsed" -ge "$timeout" ]; then
            break
        fi

        local port_state="waiting"
        [ -n "$ip" ] && check_ssh_port "$ip" && port_state="open"
        printf "\r\033[K    ... %3ss  VM:%-10s  SSH:%s" "$elapsed" "${st:-UNKNOWN}" "$port_state"
        sleep 2
    done
    printf "\r\033[K"
    diagnose_ssh_failure "$timeout"
    return 1
}

function start_vm_with_failover() {
    if is_vm_running; then
        return 0
    fi

    local status
    status=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format="value(status)" 2>/dev/null || echo "UNKNOWN")
    if [ "$status" = "RUNNING" ]; then
        echo -e "${GREEN}>>> VM $INSTANCE is already RUNNING in $ZONE.${NC}"
        return 0
    fi

    local current_mt
    current_mt=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format="value(machineType.basename())" 2>/dev/null || echo "c2d-standard-16")

    echo -e "${YELLOW}>>> VM $INSTANCE is currently $status ($current_mt). Powering on...${NC}"
    local start_err
    start_err=$(gcloud compute instances start "$INSTANCE" --zone="$ZONE" --project="$PROJECT" 2>&1)
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}>>> VM $INSTANCE started successfully with $current_mt!${NC}"
        wait_for_ssh || return 1
        return 0
    fi

    echo -e "${RED}>>> Start failed with $current_mt:${NC}\n$start_err\n"

    # If capacity is exhausted, cascade through machine types in order of lowest price
    if [[ "$start_err" == *"RESOURCE_POOL_EXHAUSTED"* || "$start_err" == *"ZONE_RESOURCE_POOL_EXHAUSTED"* || "$start_err" == *"not have enough resources"* || "$start_err" == *"does not have enough resources"* ]]; then
        echo -e "${YELLOW}>>> Capacity exhausted for $current_mt in $ZONE.${NC}"
        echo -e "${CYAN}>>> Cascading through 16-core machine types (c2d -> t2d -> c2) until resources are acquired...${NC}"

        for mt in "${MACHINE_TYPES[@]}"; do
            if [ "$mt" != "$current_mt" ]; then
                echo -e "\n${YELLOW}>>> Switching in-place to candidate: $mt...${NC}"
                gcloud compute instances set-machine-type "$INSTANCE" --machine-type="$mt" --zone="$ZONE" --project="$PROJECT" --quiet 2>/dev/null || true
                echo -e "${YELLOW}>>> Attempting to power on with $mt...${NC}"
                local retry_err
                retry_err=$(gcloud compute instances start "$INSTANCE" --zone="$ZONE" --project="$PROJECT" 2>&1)
                if [ $? -eq 0 ]; then
                    echo -e "${GREEN}>>> Resources acquired! VM $INSTANCE is RUNNING with $mt!${NC}"
                    wait_for_ssh || return 1
                    return 0
                else
                    echo -e "${RED}>>> $mt also out of capacity in $ZONE.${NC}"
                fi
            fi
        done

        echo -e "${RED}>>> All 16-vCPU 64GB machine types are temporarily exhausted in $ZONE. Please retry in a few moments.${NC}"
        return 1
    else
        echo -e "${RED}>>> Retrying start once...${NC}"
        gcloud compute instances start "$INSTANCE" --zone="$ZONE" --project="$PROJECT" 2>/dev/null || true
        wait_for_ssh || return 1
    fi
}

function ensure_vm_online() {
    if ! is_vm_running; then
        run_step "Powering on VM instance with capacity failover" start_vm_with_failover
    fi
}

function ensure_running() {
    local target_disk
    target_disk=$(normalize_disk_name "${1:-$GENERAL_DISK}")
    get_disk_meta "$target_disk"
    local mp="${2:-${META_DEFAULT_MP:-/mnt/a16}}"
    local dev_name="$META_DEV_NAME"

    # Fast-path: VM is already running and block device is attached
    if is_vm_running; then
        local actual_dev
        actual_dev=$(get_attached_device_name "$target_disk" "$dev_name")
        local check_status
        check_status=$(vm_exec "
DEV=''
for c in '/dev/disk/by-id/google-$actual_dev' '/dev/disk/by-id/google-$dev_name' '/dev/disk/by-id/google-$target_disk'; do
    if [ -b \"\$c\" ]; then DEV=\"\$c\"; break; fi
done
[ -z \"\$DEV\" ] && echo 'NO_DEV' && exit 0
REAL_DEV=\$(readlink -f \"\$DEV\")
CUR_MPS=\$(findmnt -rn -S \"\$REAL_DEV\" -o TARGET 2>/dev/null || true)
if [ \"\$CUR_MPS\" = '$mp' ]; then
    echo 'MOUNTED_EXACT'
else
    echo 'NEEDS_REMOUNT'
fi" 2>/dev/null || echo "NEED_FULL")

        if [ "$check_status" = "MOUNTED_EXACT" ]; then
            return 0
        elif [ "$check_status" = "NEEDS_REMOUNT" ]; then
            run_step "Mounting $target_disk at $mp (unmounting previous mount if any)" \
                mount_disk_on_vm "$target_disk" "$mp"
            return $?
        fi
    fi

    echo -e "${YELLOW}>>> Initializing VM and storage for build...${NC}"
    # Slow-path: Cold start or missing/unattached disk
    run_step "Verifying $target_disk is attached to VM" ensure_disk_attached "$target_disk" || return 1
    run_step "Powering on VM instance with capacity failover" start_vm_with_failover || return 1
    run_step "Mounting storage disk at $mp on VM" mount_disk_on_vm "$target_disk" "$mp"
}

function start_vm() {
    clear 2>/dev/null || true
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD}             GCP INSTANCE & DISK STARTUP                    ${NC}"
    echo -e "${CYAN}============================================================${NC}"

    echo -e ">>> Step 1/3: Checking GCP Instance State..."
    run_step "Checking GCP VM status" check_vm_online
    local is_online=$?
    local current_ip
    current_ip=$(get_vm_ip 2>/dev/null)

    if [ $is_online -eq 0 ]; then
        echo -e " ${GREEN}✔${NC} VM '$INSTANCE' is already powered ON."
        [ -n "$current_ip" ] && echo -e "    External IP: ${BOLD}${current_ip}${NC}"
    else
        echo -e "${YELLOW}>>> VM '$INSTANCE' is currently powered down.${NC}"
        if ! run_step "Powering on VM instance with capacity failover (c2d -> t2d -> c2)" start_vm_with_failover; then
            echo -e "${RED} ✖ Could not power on VM '$INSTANCE' (no capacity in $ZONE).${NC}"
            read -rp "Press Enter to return to menu..."
            return 1
        fi
    fi

    echo -e "\n>>> Step 2/3: Inspecting Attached Secondary Disks..."
    local attached_disks
    attached_disks=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --format="value(disks[].source.basename())" 2>/dev/null || echo "")
    local sec_list=""
    for d in $attached_disks; do
        if [ "$d" != "$INSTANCE" ] && [ "$d" != "gcp-boot-ccache-disk" ]; then
            sec_list="${sec_list}${sec_list:+, }$d"
        fi
    done
    if [ -n "$sec_list" ]; then
        echo -e " ${GREEN}✔${NC} Attached secondary disk(s): ${BOLD}$sec_list${NC}"
        echo -e "    (Specific disks auto-mount at boot; general disk mounts at build time.)"
    else
        echo -e " ${YELLOW}ℹ${NC} No secondary ROM disk currently attached (use Disk Manager or start a build)."
    fi

    echo -e "\n>>> Step 3/3: Verifying SSH access, boot mounts & RAM on VM..."
    if ! wait_for_ssh; then
        current_ip=$(get_vm_ip 2>/dev/null)
        echo ""
        echo -e "${RED}============================================================${NC}"
        echo -e "${RED} ✖ VM '$INSTANCE' is RUNNING but SSH is NOT ready!${NC}"
        echo -e "    External IP: ${BOLD}${current_ip:-unknown}${NC} | Status: RUNNING | SSH: FAILED"
        echo -e "    sshd may still be booting, or the firewall/SSH key is not ready."
        echo -e "    Wait a minute and retry, or run:"
        echo -e "    ${BOLD}gcloud compute ssh $INSTANCE --zone=$ZONE${NC}"
        echo -e "${RED}============================================================${NC}"
        echo ""
        read -rp "Press Enter to return to menu..."
        return 1
    fi

    # Refresh the IP after boot; ephemeral external IPs change across restarts.
    current_ip=$(get_vm_ip 2>/dev/null)
    local mount_list
    mount_list=$(vm_exec 'for d in /mnt/a16 /mnt/a17 /mnt/recovery; do mountpoint -q "$d" && echo "$d"; done' 2>/dev/null || true)
    echo "RUNNING,$current_ip,$(date +%s)" > "$VM_STATE_CACHE" 2>/dev/null || true
    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN} ✅ VM '$INSTANCE' is ONLINE and READY!${NC}"
    echo -e "    External IP: ${BOLD}${current_ip}${NC} | Status: RUNNING | SSH: OK"
    if [ -n "$mount_list" ]; then
        echo -e "    Mounted build storage: ${BOLD}$(echo "$mount_list" | tr '\n' ' ')${NC}"
    else
        echo -e "    ${YELLOW}ℹ No build disk mounted yet.${NC}"
        echo -e "    It is mounted automatically when a build starts (Disk Manager / build menu)."
    fi
    echo -e "${GREEN}============================================================${NC}"
    echo ""
    vm_exec "df -h / /mnt/a16 /mnt/a17 /mnt/recovery 2>/dev/null; free -h" || true
    echo ""
    read -rp "Press Enter to return to menu..."
}

function stop_vm() {
    clear 2>/dev/null || true
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD}                 STOPPING GCP INSTANCE                      ${NC}"
    echo -e "${CYAN}============================================================${NC}"

    run_step "Checking GCP VM status" check_vm_online
    if [ $? -ne 0 ]; then
        echo -e " ${GREEN}✔${NC} VM '$INSTANCE' is already STOPPED (TERMINATED)."
        echo -e "    Compute billing is paused. No action needed."
        echo ""
        read -rp "Press Enter to return to menu..."
        return 0
    fi

    run_step "Stopping VM '$INSTANCE' to pause compute charges (~\$0.726/hr)" \
        gcloud compute instances stop "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet

    rm -f "$VM_IP_CACHE" 2>/dev/null || true
    echo "TERMINATED,,$(date +%s)" > "$VM_STATE_CACHE" 2>/dev/null || true
    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN} ✅ VM '$INSTANCE' has been STOPPED successfully!${NC}"
    echo -e "${GREEN}    Compute billing has been PAUSED immediately.${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""
    read -rp "Press Enter to return to menu..."
}

function attach_disk_action() {
    local target_disk
    target_disk=$(normalize_disk_name "$1")

    if [ -z "$target_disk" ]; then
        echo -e "${CYAN}============================================================${NC}"
        echo -e "${BOLD}              SELECT UNATTACHED DISK TO ATTACH              ${NC}"
        echo -e "${CYAN}============================================================${NC}"

        local attached_disks
        attached_disks=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
            --format="value(disks[].source.basename())" 2>/dev/null || echo "")

        local all_disks=()
        while IFS= read -r line; do
            [ -n "$line" ] && all_disks+=("$line")
        done < <(gcloud compute disks list --project="$PROJECT" --zones="$ZONE" \
            --filter="name != '$INSTANCE' AND name != 'gcp-boot-ccache-disk'" \
            --format="value(name)")

        local available=()
        for d in "${all_disks[@]}"; do
            if ! echo "$attached_disks" | grep -qx "$d"; then
                available+=("$d")
            fi
        done

        if [ ${#available[@]} -eq 0 ]; then
            echo -e "${YELLOW}>>> No unattached secondary disks found in project.${NC}"
            echo -e "${CYAN}>>> Use Options 1-4 in Disk Manager to create one.${NC}"
            return 0
        fi

        echo -e "${BOLD}Available Unattached Disks:${NC}"
        local idx=1
        for d in "${available[@]}"; do
            local size
            size=$(gcloud compute disks describe "$d" --zone="$ZONE" --project="$PROJECT" --format="value(sizeGb)" 2>/dev/null || echo "")
            echo -e " ${BOLD}$idx)${NC} ${GREEN}$d${NC} (${size} GB - $(get_disk_role_label "$d"))"
            ((idx++))
        done
        echo -e " ${BOLD}0)${NC} Cancel"
        echo ""
        read -rp "Select disk number to attach [1-${#available[@]} or 0]: " sel_idx
        if [ "$sel_idx" = "0" ] || [ -z "$sel_idx" ]; then return 0; fi
        if [[ "$sel_idx" =~ ^[0-9]+$ ]] && [ "$sel_idx" -le "${#available[@]}" ] && [ "$sel_idx" -gt 0 ]; then
            target_disk="${available[$((sel_idx-1))]}"
        else
            echo -e "${RED}Invalid selection.${NC}"; return 1
        fi
    fi

    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD} Attaching ROM Disk ($target_disk) to $INSTANCE...${NC}"
    echo -e "${CYAN}============================================================${NC}"
    run_step "Attaching $target_disk to $INSTANCE" ensure_disk_attached "$target_disk"

    get_disk_meta "$target_disk"

    if [ "$META_IS_GENERAL" -eq 1 ]; then
        echo -e "${GREEN}>>> General disk '$target_disk' attached to $INSTANCE.${NC}"
        echo -e "${CYAN}    Kept unmounted until a build starts (mounts automatically at /mnt/a16, /mnt/a17, or /mnt/recovery).${NC}"
    elif is_vm_running; then
        run_step "Mounting specific disk at $META_DEFAULT_MP on VM" \
            mount_disk_on_vm "$target_disk" "$META_DEFAULT_MP"
    else
        echo -e "${YELLOW}>>> Note: VM is currently stopped.${NC}"
        echo -e "${GREEN}>>> Disk is attached at GCP level and will auto-mount to $META_DEFAULT_MP at boot.${NC}"
    fi
}

function detach_disk_action() {
    local target_disk
    target_disk=$(normalize_disk_name "$1")

    if [ -z "$target_disk" ]; then
        echo -e "${CYAN}============================================================${NC}"
        echo -e "${BOLD}              SELECT ATTACHED DISK TO DETACH                ${NC}"
        echo -e "${CYAN}============================================================${NC}"

        local attached_str
        attached_str=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
            --format="value(disks[].source.basename())" 2>/dev/null || echo "")

        local attached_disks=()
        for d in $attached_str; do
            if [ "$d" != "$INSTANCE" ] && [ "$d" != "gcp-boot-ccache-disk" ]; then
                attached_disks+=("$d")
            fi
        done

        if [ ${#attached_disks[@]} -eq 0 ]; then
            echo -e "${YELLOW}>>> No secondary ROM disks are currently attached to $INSTANCE.${NC}"
            return 0
        fi

        echo -e "${BOLD}Attached Secondary Disks:${NC}"
        local idx=1
        for d in "${attached_disks[@]}"; do
            echo -e " ${BOLD}$idx)${NC} ${YELLOW}$d${NC} ($(get_disk_role_label "$d"))"
            ((idx++))
        done
        echo -e " ${BOLD}0)${NC} Cancel"
        echo ""
        read -rp "Select disk number to detach [1-${#attached_disks[@]} or 0]: " sel_idx
        if [ "$sel_idx" = "0" ] || [ -z "$sel_idx" ]; then return 0; fi
        if [[ "$sel_idx" =~ ^[0-9]+$ ]] && [ "$sel_idx" -le "${#attached_disks[@]}" ] && [ "$sel_idx" -gt 0 ]; then
            target_disk="${attached_disks[$((sel_idx-1))]}"
        else
            echo -e "${RED}Invalid selection.${NC}"; return 1
        fi
    fi

    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD} Detaching ROM Disk ($target_disk) from $INSTANCE...${NC}"
    echo -e "${CYAN}============================================================${NC}"

    if is_vm_running; then
        run_step "Unmounting $target_disk on VM" unmount_disk_on_vm "$target_disk"
    fi

    run_step "Detaching $target_disk from $INSTANCE" \
        gcloud compute instances detach-disk "$INSTANCE" --disk="$target_disk" --zone="$ZONE" --project="$PROJECT" --quiet
    echo -e "${GREEN}>>> Disk $target_disk detached successfully!${NC}"
}

function prompt_options() {
    local build_cat="${1:-rom}"
    local default_type="userdebug"
    [ "$build_cat" = "recovery" ] && default_type="eng"

    if [ -z "$BUILD_TYPE_OPT" ]; then
        if [ -t 0 ]; then
            echo ""
            echo -e "${BOLD}Select Build Variant:${NC}"
            if [ "$build_cat" = "recovery" ]; then
                echo -e "  ${GREEN}1)${NC} eng        (Recommended)"
                echo -e "  ${CYAN}2)${NC} userdebug  (Debug)"
                echo -e "  ${YELLOW}3)${NC} user       (Release)"
                read -rp " Select variant [1-3, default: 1 (eng)]: " b_type_in
                case "$b_type_in" in
                    2|userdebug) BUILD_TYPE_OPT="userdebug" ;;
                    3|user)      BUILD_TYPE_OPT="user" ;;
                    1|eng|*)     BUILD_TYPE_OPT="eng" ;;
                esac
            else
                echo -e "  ${GREEN}1)${NC} userdebug  (Recommended)"
                echo -e "  ${CYAN}2)${NC} user       (Official Release)"
                echo -e "  ${YELLOW}3)${NC} eng        (Debug)"
                read -rp " Select variant [1-3, default: 1 (userdebug)]: " b_type_in
                case "$b_type_in" in
                    2|user)      BUILD_TYPE_OPT="user" ;;
                    3|eng)       BUILD_TYPE_OPT="eng" ;;
                    1|userdebug|*) BUILD_TYPE_OPT="userdebug" ;;
                esac
            fi
        else
            BUILD_TYPE_OPT="$default_type"
        fi
    fi

    if [ -z "$SF_OPT" ]; then
        if [ -t 0 ]; then
            read -rp " Upload to SourceForge too? (y/N): " sf_in
            if [[ "$sf_in" =~ ^[Yy]$ ]]; then
                SF_OPT="true"
            else
                SF_OPT="false"
            fi
        else
            SF_OPT="false"
        fi
    fi

    if [ -z "$GOFILE_OPT" ]; then
        if [ -t 0 ]; then
            read -rp " Upload to Gofile.io too? (Y/n): " gf_in
            if [[ "$gf_in" =~ ^[Nn]$ ]]; then
                GOFILE_OPT="false"
            else
                GOFILE_OPT="true"
            fi
        else
            GOFILE_OPT="true"
        fi
    fi

    if [ -z "$SHUT_OPT" ]; then
        if [ -t 0 ]; then
            read -rp " Power off immediately when build ends? (Y/n; n = 30m idle timeout): " auto_in
            if [[ "$auto_in" =~ ^[Nn]$ ]]; then
                SHUT_OPT="false"
            else
                SHUT_OPT="true"
            fi
        else
            SHUT_OPT="true"
        fi
    fi
}

function check_build_slot() {
    local state vm_state remote_result
    state=$(get_vm_state 2>/dev/null)
    vm_state="${state%%,*}"

    case "$vm_state" in
        RUNNING) ;;
        TERMINATED|STOPPED|SUSPENDED) return 0 ;;
        *)
            echo -e "${RED}[ERROR] Could not verify the GCP VM state ('$vm_state'); refusing to start another build.${NC}"
            echo -e "       Check the VM with: ./gcp_menu.sh status"
            return 1
            ;;
    esac

    remote_result=$(vm_exec '
active=$(ps -eo pid=,etime=,args= | grep -E "[g]cp_build_runner[.]sh|[g]cp_build_script[.]sh|[s]oong_ui .*--build-mode|[/]build/soong/bin/m .*(recoveryimage|otapackage|bacon|droid)|[r]epo sync" || true)
if [ -n "$active" ]; then
    echo BUILD_ALREADY_RUNNING
    echo "$active"
    for f in /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status; do
        [ -f "$f" ] && echo "$f: $(cat "$f")"
    done
else
    echo BUILD_SLOT_FREE
fi
' 2>&1)
    if [ $? -ne 0 ]; then
        echo -e "${RED}[ERROR] Could not check whether a build is already running. Refusing to replace the VM build session.${NC}"
        [ -n "$remote_result" ] && echo "$remote_result"
        return 1
    fi

    if [[ "$remote_result" == *BUILD_ALREADY_RUNNING* ]]; then
        echo -e "${RED}[ERROR] A build is already running on the GCP VM. The new build was not started, and the running session was left untouched.${NC}"
        echo "$remote_result" | sed '/^BUILD_ALREADY_RUNNING$/d'
        echo -e "       View it with: ./gcp_menu.sh logs"
        return 1
    fi

    if [[ "$remote_result" != *BUILD_SLOT_FREE* ]]; then
        echo -e "${RED}[ERROR] The VM did not confirm that the build slot is free. Refusing to start.${NC}"
        [ -n "$remote_result" ] && echo "$remote_result"
        return 1
    fi
    return 0
}

function run_build_with_feedback() {
    run_build "$@"
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo -e "${RED}[ERROR] Build request was not started. Review the message above, then check VM status or build logs before retrying.${NC}"
        if [ -t 0 ]; then
            read -rp "Press Enter to return to the menu..."
        fi
    fi
    return "$rc"
}

function run_build() {
    local branch="$1"
    local script_name="$2"
    local title="$3"

    # Check before disk formatting, VM setup, or replacing the shared tmux
    # session so a second request cannot disturb an active build.
    check_build_slot || return 1

    local build_cat="rom"
    [ "$branch" = "recovery" ] && build_cat="recovery"

    prompt_options "$build_cat"

    resolve_build_disk "$branch"
    local target_disk="$RESOLVED_DISK"
    local mp="$RESOLVED_MP"
    local disk_mode="$RESOLVED_MODE"

    local script_url="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/${branch}/${script_name}"

    echo -e "\n${CYAN}============================================================${NC}"
    echo -e "${BOLD} $title${NC}"
    [ -n "${DEVICE_OPT:-}" ] && echo -e " Target Device       : ${BOLD}${CYAN}${DEVICE_OPT}${NC}"
    echo -e " Build Type / Variant: ${BOLD}${GREEN}${BUILD_TYPE_OPT}${NC}"
    if [ "$disk_mode" = "general" ]; then
        echo -e " Target Storage Disk : $target_disk ${YELLOW}(General Shared Fallback)${NC}"
    else
        echo -e " Target Storage Disk : $target_disk ${GREEN}(Specific)${NC}"
    fi
    echo -e " Mount Point Path    : $mp"
    if [ "$branch" = "recovery" ]; then
        if [ "$mp" = "/mnt/recovery" ]; then
            if [ "$disk_mode" = "general" ]; then
                echo -e " Recovery Workspace  : /mnt/recovery (General Disk mounted at /mnt/recovery)"
            else
                echo -e " Recovery Workspace  : /mnt/recovery (Dedicated Recovery SSD)"
            fi
        else
            echo -e " Recovery Workspace  : $mp/recovery (symlinked to /mnt/recovery)"
        fi
    fi
    echo -e " Script URL          : $script_url"
    echo -e " SourceForge Upload  : $SF_OPT"
    echo -e " Gofile.io Upload    : $GOFILE_OPT"
    if [ "$SHUT_OPT" = "true" ]; then
        echo -e " Shutdown Behavior   : Immediate after build"
    else
        echo -e " Shutdown Behavior   : After 30m without build activity"
    fi
    [ "$FORMAT_DISK_OPT" = "true" ] && echo -e " Format Disk First   : ${RED}true (--format)${NC}"
    echo -e "${CYAN}============================================================${NC}"

    if [ "$FORMAT_DISK_OPT" = "true" ]; then
        format_disk_action "$target_disk" "--yes" || return 1
    fi

    ensure_running "$target_disk" "$mp" || return 1
    # Refresh the guest watchdog from this checkout before a build starts.
    # This keeps its process detection and build-runner guard in sync with the
    # wrapper that is about to launch.
    run_step "Updating idle-shutdown watchdog" deploy_watchdog || return 1

    # On general shared disk, check if it holds data from another target and offer clean format interactively
    if [ "$FORMAT_DISK_OPT" != "true" ] && [ -t 0 ] && [ "$disk_mode" = "general" ]; then
        local disk_info
        disk_info=$(vm_exec "
PREV=''
[ -f '$mp/.gcp_build_target' ] && PREV=\$(cat '$mp/.gcp_build_target' 2>/dev/null | tr -d '\r')
USED=\$(df -h '$mp' | awk 'NR==2{print \$3\" / \"\$2\" (Free: \"\$4\")\"}')
DIRS=\$(ls -1A '$mp' 2>/dev/null | grep -vx 'lost+found' | grep -vx '.gcp_build_target' | head -n 6 | tr '\n' ' ')
echo \"PREV=\$PREV|USED=\$USED|DIRS=\$DIRS\"
" 2>/dev/null || true)
        local prev_target used_info existing_dirs
        prev_target=$(echo "$disk_info" | sed -n 's/.*PREV=\([^|]*\).*/\1/p')
        used_info=$(echo "$disk_info" | sed -n 's/.*USED=\([^|]*\).*/\1/p')
        existing_dirs=$(echo "$disk_info" | sed -n 's/.*DIRS=\(.*\)/\1/p' | xargs)

        if [ -n "$existing_dirs" ] && [ "$prev_target" != "$branch" ]; then
            echo ""
            echo -e "${YELLOW}>>> Notice: General disk ($target_disk) at $mp contains existing data!${NC}"
            [ -n "$prev_target" ] && echo -e "    Previous Target : ${BOLD}$prev_target${NC} (New: ${BOLD}$branch${NC})"
            [ -n "$used_info" ]   && echo -e "    Disk Usage      : $used_info"
            echo -e "    Top Entries     : $existing_dirs"
            read -rp " Format '$target_disk' clean (ext4) before starting '$branch' build? (y/N): " fmt_ans
            if [[ "$fmt_ans" =~ ^[Yy]$ ]]; then
                format_disk_action "$target_disk" "--yes" || return 1
                run_step "Mounting freshly formatted disk at $mp" mount_disk_on_vm "$target_disk" "$mp" || return 1
            fi
        fi
    fi

    local build_label="BUILD"
    case "$branch" in
        recovery) build_label="RECOVERY" ;;
        aosp-16)  build_label="ROM A16" ;;
        aosp-17)  build_label="ROM A17" ;;
    esac
    local win_label="${script_name%.sh}"
    win_label="${win_label%_sm7125}"
    win_label="${win_label%_all}"

    echo -e "\n${GREEN}>>> Starting ${build_label} (${win_label}) in tmux session 'rom-build' on VM...${NC}"
    echo -e "${YELLOW}Tip: You can safely detach (Ctrl+B then D) or close terminal.${NC}"
    echo -e "${YELLOW}To re-attach at any time, select Option 17 in this menu.${NC}\n"

    local net_speed_setup
    net_speed_setup=$(build_vm_net_speed_setup_cmd)

    # Build a clean runner script (uploaded base64-encoded to avoid SSH/tmux nested quoting bugs)
    # Runs inside a real PTY via 'script' so repo sync / ninja display live progress while logging.
    local runner_script
    runner_script=$(cat <<EOF_RUNNER
#!/usr/bin/env bash
export BUILD_VARIANT='$BUILD_TYPE_OPT'
export SOONG_INCREMENTAL_ANALYSIS=true
export SF_UPLOAD='$SF_OPT'
export SKIP_GOFILE='$([ "$GOFILE_OPT" = "false" ] && echo "true" || echo "false")'
# The device build scripts also honor AUTO_SHUTDOWN.  Keep them from
# powering off mid-run: the wrapper must write build.status, print the
# result and only then shut the VM down.
export AUTO_SHUTDOWN=false
export WRAPPER_SHUTDOWN='$SHUT_OPT'
$( [ -n "${DEVICE_OPT:-}" ] && echo "export DEVICE='$DEVICE_OPT'" )
$net_speed_setup
# Keep ccache from filling the boot disk again: a full / breaks cloud-init
# and SSH key injection on the next boot.  Clear it if space is tight.
ccache -M 5G >/dev/null 2>&1 || true
BOOT_AVAIL_MB=\$(df -Pm / | awk 'NR==2{print \$4}')
if [ -n "\$BOOT_AVAIL_MB" ] && [ "\$BOOT_AVAIL_MB" -lt 3072 ] 2>/dev/null; then
    echo ">>> Boot disk only \${BOOT_AVAIL_MB}MB free: clearing ccache before build..."
    ccache -C >/dev/null 2>&1 || rm -rf "\$HOME/.ccache"
fi
tmux set-option -t rom-build @build_label '$build_label' 2>/dev/null || true
tmux rename-window '$win_label' 2>/dev/null || true
sudo touch /run/gcp-build-active 2>/dev/null || true
sudo rm -f /run/gcp-build-shutdown-pending 2>/dev/null || true
sudo mkdir -p '$mp'
echo '$branch' > '$mp/.gcp_build_target' 2>/dev/null || true
if [ '$branch' = 'recovery' ] && [ '$mp' != '/mnt/recovery' ]; then
    sudo mkdir -p '$mp/recovery' && sudo ln -sfn '$mp/recovery' /mnt/recovery
fi
echo "BUILD_STATUS: RUNNING (\$BUILD_VARIANT) - \$(date)" > '$mp/build.status' 2>/dev/null || true

BUILD_SCRIPT_TMP="/tmp/gcp_build_script.sh"
CACHE_BUST="\$(date +%s)"
if curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' "${script_url}?t=\${CACHE_BUST}" | sed -E '/repo[[:space:]]+sync/ { s/[[:space:]]+--progress([[:space:]]|\$)/\1/g; s/[[:space:]]+(-v|--verbose)([[:space:]]|\$)/\2/g; }' > "\$BUILD_SCRIPT_TMP" && [ -s "\$BUILD_SCRIPT_TMP" ]; then
    if command -v script >/dev/null 2>&1; then
        script -qefc "bash \$BUILD_SCRIPT_TMP" '$mp/build.log'
        BUILD_EXIT=\$?
    else
        set -o pipefail
        bash "\$BUILD_SCRIPT_TMP" 2>&1 | tee '$mp/build.log'
        BUILD_EXIT=\$?
    fi
else
    echo ">>> [ERROR] Failed to download build script from $script_url" | tee '$mp/build.log'
    BUILD_EXIT=1
fi

if [ \$BUILD_EXIT -eq 0 ]; then
    sudo rm -f /run/gcp-build-active 2>/dev/null || true
    echo "BUILD_STATUS: SUCCESS (\$BUILD_VARIANT) - \$(date)" > '$mp/build.status' 2>/dev/null || true
    echo -e '\n\033[1;32m============================================================\033[0m'
    echo -e '\033[1;32m [SUCCESS] Build and upload completed successfully!\033[0m'
    echo -e '\033[1;32m============================================================\033[0m'
    if [ "\$WRAPPER_SHUTDOWN" = 'true' ]; then
        echo '>>> Auto-shutdown: powering off now.'
        sudo poweroff || true
        exit 0
    else
        echo "\$(date +%s)" | sudo tee /run/gcp-build-shutdown-pending >/dev/null 2>&1 || true
        echo '>>> Delayed shutdown armed: powering off after 30 minutes without build activity.'
    fi
else
    echo "BUILD_STATUS: FAILED (Exit Code \$BUILD_EXIT) - \$(date)" > '$mp/build.status' 2>/dev/null || true
    echo -e '\n\033[1;31m============================================================\033[0m'
    echo -e "\033[1;31m [ERROR] Build failed with exit code \$BUILD_EXIT!\033[0m"
    echo -e '\033[1;31m============================================================\033[0m'
    echo "Full build output is saved at: $mp/build.log"
    # The build has ended; let the idle watchdog start its 30-minute timer.
    sudo rm -f /run/gcp-build-active 2>/dev/null || true
    if [ "\$WRAPPER_SHUTDOWN" = 'true' ]; then
        echo '>>> Auto-shutdown: powering off now.'
        sudo poweroff || true
        exit 0
    else
        echo "\$(date +%s)" | sudo tee /run/gcp-build-shutdown-pending >/dev/null 2>&1 || true
        echo '>>> Auto-shutdown is disabled; the idle watchdog will power off after 30 minutes without build activity.'
        echo '>>> Press Enter to enter the bash prompt...'
        read -r
    fi
    exec bash
fi
echo -e '\nPress Enter to enter bash prompt (tmux session will remain open)...'
read -r
exec bash
EOF_RUNNER
)

    local runner_b64
    runner_b64=$(printf '%s\n' "$runner_script" | base64 | tr -d '\n')

    # Reset options so subsequent builds prompt anew
    BUILD_TYPE_OPT=""
    SF_OPT=""
    GOFILE_OPT=""
    SHUT_OPT=""
    FORMAT_DISK_OPT=""
    DEVICE_OPT=""

    local upload_script="$SCRIPT_DIR/upload.py"
    if [ -f "$upload_script" ]; then
        vm_scp "$upload_script" "$USER@$INSTANCE:~/upload.py" || \
            echo -e "${YELLOW}>>> [WARN] Could not refresh ~/upload.py on the VM; build will continue with the existing uploader.${NC}"
    else
        echo -e "${YELLOW}>>> [WARN] Local upload.py not found at $upload_script; build will continue with the existing VM uploader.${NC}"
    fi
    if ! vm_exec "if pgrep -x soong_ui >/dev/null 2>&1 || pgrep -x ninja >/dev/null 2>&1 || pgrep -f '^bash /tmp/gcp_build_runner[.]sh$' >/dev/null 2>&1 || pgrep -f '^bash /tmp/gcp_build_script[.]sh$' >/dev/null 2>&1; then echo 'An Android build became active before launch; refusing to replace it.' >&2; exit 1; fi; printf '%s' '$runner_b64' | base64 -d > /tmp/gcp_build_runner.sh && chmod +x /tmp/gcp_build_runner.sh && { tmux kill-session -t rom-build 2>/dev/null || true; } && tmux new-session -d -s rom-build 'bash /tmp/gcp_build_runner.sh'"; then
        echo -e "${RED}[ERROR] Could not create the remote tmux build session. The build was not started; check the error above and the VM build status.${NC}"
        return 1
    fi
    sync_upload_links >/dev/null 2>&1 || true
    if [ -s "$LOCAL_LINKS_FILE" ]; then
        echo -e "\n${CYAN}>>> Saved Upload Links ($LOCAL_LINKS_FILE):${NC}"
        tail -n 5 "$LOCAL_LINKS_FILE"
    fi
}

# Generates remote shell snippet to install /usr/local/bin/vm_net_speed.sh and configure tmux status bar.
function build_vm_net_speed_setup_cmd() {
    local net_script net_b64
    net_script=$(cat << 'EOF_NS'
#!/usr/bin/env bash
# GCP-NET-SPEED-V2
MODE="$1"
STATE_FILE="/tmp/vm_net_speed_$(id -u).state"
read -r now_t _ < /proc/uptime || exit 0
read -r rx tx < <(awk '
  /:/ { gsub(/:/, " "); if ($1 != "lo" && $1 !~ /^(veth|docker|br-|virbr)/) { rx += $2; tx += $10; } }
  END { printf "%.0f %.0f\n", rx, tx }
' /proc/net/dev)
[ -r "$STATE_FILE" ] && read -r prev_t prev_rx prev_tx < "$STATE_FILE" 2>/dev/null || true
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
EOF_NS
)
    net_b64=$(printf '%s\n' "$net_script" | base64 | tr -d '\n')
    local tmux_conf tmux_b64
    tmux_conf=$(cat << 'EOF_TC'

# GCP-TMUX-STYLE-V6: high-contrast dark footer, mouse-wheel scrolling, 50k scrollback, build label & DL/UL speed
# Mouse is ON so the wheel scrolls the pane. To select text natively, hold Option
# (macOS) while dragging, or press Ctrl+B then m to toggle mouse off/on.
set -g mouse on
set -g set-clipboard on
bind-key m set-option -g mouse
set -g history-limit 50000
set -g status-interval 2
set -g status-style "bg=colour235,fg=colour252"
set -g status-left-length 30
set -g status-left "#[bg=colour39,fg=colour232,bold] #{?@build_label,#{@build_label},BUILD} #[bg=colour235,fg=colour39] "
set -g window-status-style "bg=colour235,fg=colour245"
set -g window-status-current-style "bg=colour238,fg=colour255,bold"
set -g status-right-length 85
set -g status-right "#(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b "
EOF_TC
)
    tmux_b64=$(printf '%s\n' "$tmux_conf" | base64 | tr -d '\n')
    cat << EOF_CMD
if ! grep -q 'GCP-NET-SPEED-V2' /usr/local/bin/vm_net_speed.sh 2>/dev/null; then
  printf '%s' '$net_b64' | base64 -d | sudo tee /usr/local/bin/vm_net_speed.sh >/dev/null && sudo chmod +x /usr/local/bin/vm_net_speed.sh
fi
if ! grep -q 'GCP-TMUX-STYLE-V6' /etc/tmux.conf 2>/dev/null; then
  sudo touch /etc/tmux.conf 2>/dev/null || true
  sudo sed -i '/# GCP Build Farm:/,\$d;/# GCP-TMUX-STYLE-V/,\$d' /etc/tmux.conf 2>/dev/null || true
  printf '%s' '$tmux_b64' | base64 -d | sudo tee -a /etc/tmux.conf >/dev/null 2>&1 || true
fi
tmux set-option -g mouse on 2>/dev/null || true
tmux set-option -g set-clipboard on 2>/dev/null || true
tmux bind-key m set-option -g mouse 2>/dev/null || true
tmux set-option -g history-limit 50000 2>/dev/null || true
tmux set-option -g status-interval 2 2>/dev/null || true
tmux set-option -g status-style 'bg=colour235,fg=colour252' 2>/dev/null || true
tmux set-option -g status-left-length 30 2>/dev/null || true
tmux set-option -g status-left '#[bg=colour39,fg=colour232,bold] #{?@build_label,#{@build_label},BUILD} #[bg=colour235,fg=colour39] ' 2>/dev/null || true
tmux set-window-option -g window-status-style 'bg=colour235,fg=colour245' 2>/dev/null || true
tmux set-window-option -g window-status-current-style 'bg=colour238,fg=colour255,bold' 2>/dev/null || true
tmux set-option -g status-right-length 85 2>/dev/null || true
tmux set-option -g status-right '#(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b ' 2>/dev/null || true
# 'mouse' is a server option: a session-level value shadows the global one and
# would break the Ctrl+B m toggle, so clear any stale override instead.
tmux set-option -u -t rom-build mouse 2>/dev/null || true
tmux set-option -t rom-build history-limit 50000 2>/dev/null || true
tmux set-option -t rom-build status-interval 2 2>/dev/null || true
tmux set-option -t rom-build status-style 'bg=colour235,fg=colour252' 2>/dev/null || true
tmux set-option -t rom-build status-left-length 30 2>/dev/null || true
tmux set-option -t rom-build status-left '#[bg=colour39,fg=colour232,bold] #{?@build_label,#{@build_label},BUILD} #[bg=colour235,fg=colour39] ' 2>/dev/null || true
tmux set-window-option -t rom-build window-status-style 'bg=colour235,fg=colour245' 2>/dev/null || true
tmux set-window-option -t rom-build window-status-current-style 'bg=colour238,fg=colour255,bold' 2>/dev/null || true
tmux set-option -t rom-build status-right-length 85 2>/dev/null || true
tmux set-option -t rom-build status-right '#(/usr/local/bin/vm_net_speed.sh --tmux) #[fg=colour242]| #[fg=colour117]#h #[fg=colour252]%H:%M %d-%b ' 2>/dev/null || true
EOF_CMD
}

function attach_tmux_session() {
    local net_setup
    net_setup=$(build_vm_net_speed_setup_cmd)
    vm_exec_tty "$net_setup
tmux attach-session -t rom-build || tmux ls"
}

# Realtime follower that flushes newline and carriage-return records as they arrive.
function follow_build_log_live() {
    local log="$1"
    local lines="${2:-400}"
    if [ -z "$log" ]; then
        echo -e " ${YELLOW}No log file found.${NC}"
        return 1
    fi
    echo -e "${CYAN}>>> Following ${log} in realtime (last ${lines} lines, Ctrl+C to stop)${NC}\n"
    local remote_cmd
    remote_cmd=$(cat <<'REMOTE_FOLLOW'
if [ ! -f "$LOG" ]; then echo "No log file found: $LOG"; exit 1; fi
tail -n "$LINES" -F -- "$LOG" | perl -MTime::HiRes=ualarm -e '$|=1; my $buf=""; local $SIG{ALRM}=sub { if (length $buf) { $buf =~ s/\e\[[0-?]*[ -\/]*[\x40-\x7e]//g; print $buf; $buf=""; } alarm 1; }; alarm 1; while (sysread(STDIN, my $chunk, 8192)) { $buf.=$chunk; while ($buf =~ s/^(.*?)[\r\n]//s) { my $line=$1; $line =~ s/\e\[[0-?]*[ -\/]*[\x40-\x7e]//g; print "$line\n" if length $line; } }'
REMOTE_FOLLOW
)
    printf -v remote_cmd 'LOG=%q LINES=%q; %s' "$log" "$lines" "$remote_cmd"
    vm_exec_tty "$remote_cmd"
    local rc=$?
    restore_local_terminal
    return $rc
}

# Static snapshot for idle builds: full log, opens at the end, scrollable.
function view_build_log_snapshot() {
    local log="$1"
    if [ -z "$log" ]; then
        echo -e " ${YELLOW}No log file found.${NC}"
        return 1
    fi
    vm_exec_tty "if [ -f '$log' ]; then tr '\r' '\n' < '$log' | sed -E 's/\x1b\[[0-9;?]*[A-Za-z]//g' | less -R --mouse +G; else echo 'No log file found.'; fi"
    restore_local_terminal
}

function view_build_logs() {
    while true; do
        clear_screen
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}                     BUILD STATUS & LOGS                         ${NC}"
        echo -e "${CYAN}=================================================================${NC}"

        if ! require_vm_running "view build logs and status"; then
            return 0
        fi

        echo -ne " ${CYAN}⏳${NC} Fetching build status & logs from VM... "

        local raw_data
        raw_data=$(vm_exec '
ST=""
STATUS_FILE=""
# Newest status wins: /mnt/a16/build.status always exists and used to mask
# the active a17 build, making this page show stale a16 state.
for f in $(ls -t /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status 2>/dev/null); do
  [ -f "$f" ] && STATUS_FILE="$f" && break
done
BUILD_DIR=""
LOG=""
if [ -n "$STATUS_FILE" ]; then
  BUILD_DIR=$(dirname "$STATUS_FILE")
  ST=$(cat "$STATUS_FILE" 2>/dev/null)
  LOG="$BUILD_DIR/build.log"
else
  LOG=$(ls -t /mnt/a16/build.log /mnt/a17/build.log /mnt/recovery/build.log 2>/dev/null | head -n 1)
  [ -n "$LOG" ] && BUILD_DIR=$(dirname "$LOG")
  ST="NO_BUILD_RECORDED"
fi
[ -z "$ST" ] && ST="NO_BUILD_RECORDED"

# Bracket the first character of each pattern: this shell command line itself
# contains the pattern text, so an unbracketed pgrep/ps match counts itself.
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
  # Strip colors, cap width and drop the repeated "0:00 0:01 ..." timer text,
  # otherwise a single "line" can be hundreds of KB and floods the dashboard.
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
' 2>/dev/null)

        local b_status="UNKNOWN"
        local b_procs="0"
        local b_watchdog="UNKNOWN"
        local b_net_speed="N/A"
        local b_progress="N/A"
        local b_log=""
        local b_errors=""
        local b_tail=""
        local b_build_dir=""
        local b_target=""
        local b_log_size="N/A"
        local b_status_file=""

        # Strip CR here too: a stray \r makes the file path unusable for scp/less.
        b_status=$(echo "$raw_data" | grep -m1 "^STATUS: " | sed 's/^STATUS: //' | tr -d '\r')
        b_status_file=$(echo "$raw_data" | grep -m1 "^STATUS_FILE: " | sed 's/^STATUS_FILE: //' | tr -d '\r')
        b_build_dir=$(echo "$raw_data" | grep -m1 "^BUILD_DIR: " | sed 's/^BUILD_DIR: //' | tr -d '\r')
        b_target=$(echo "$raw_data" | grep -m1 "^TARGET: " | sed 's/^TARGET: //' | tr -d '\r')
        b_procs=$(echo "$raw_data" | grep -m1 "^PROCS_COUNT: " | sed 's/^PROCS_COUNT: //' | tr -d '\r')
        b_watchdog=$(echo "$raw_data" | grep -m1 "^WATCHDOG: " | sed 's/^WATCHDOG: //' | tr -d '\r')
        b_net_speed=$(echo "$raw_data" | grep -m1 "^NET_SPEED: " | sed 's/^NET_SPEED: //' | tr -d '\r')
        b_progress=$(echo "$raw_data" | grep -m1 "^PROGRESS: " | sed 's/^PROGRESS: //' | tr -d '\r')
        b_log=$(echo "$raw_data" | grep -m1 "^LOG_PATH: " | sed 's/^LOG_PATH: //' | tr -d '\r')
        b_log_size=$(echo "$raw_data" | grep -m1 "^LOG_SIZE: " | sed 's/^LOG_SIZE: //' | tr -d '\r')

        b_errors=$(echo "$raw_data" | sed -n '/===ERROR_START===/,/===ERROR_END===/p' | grep -v '===ERROR_')
        b_tail=$(echo "$raw_data" | sed -n '/===LOG_START===/,/===LOG_END===/p' | grep -v '===LOG_')

        # Redraw screen with parsed dashboard (clear scrollback too, so a huge
        # build log that was on screen cannot push the header out of view).
        clear_screen
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}                     BUILD STATUS & LOGS                         ${NC}"
        echo -e "${CYAN}=================================================================${NC}"

        local target_desc="${b_target:-build}"
        [ -n "$b_build_dir" ] && target_desc="$target_desc (${b_build_dir##*/})"

        if [ "${b_procs:-0}" -gt 0 ] 2>/dev/null; then
            echo -e " ${BOLD}BUILD STATE   :${NC} ${GREEN}● ACTIVE / COMPILING${NC} (${b_procs} compiler processes)"
            echo -e " ${BOLD}PROGRESS      :${NC} ${GREEN}${b_progress}${NC}"
        elif [[ "$b_status" == *"SUCCESS"* ]]; then
            echo -e " ${BOLD}BUILD STATE   :${NC} ${GREEN}✔ SUCCESS${NC}"
            echo -e " ${BOLD}DETAILS       :${NC} Completed and uploaded successfully."
            sync_upload_links >/dev/null 2>&1 || true
            if [ -s "$LOCAL_LINKS_FILE" ]; then
                echo -e " ${BOLD}LATEST LINKS  :${NC}"
                tail -n 3 "$LOCAL_LINKS_FILE" | sed 's/^/   /'
            fi
        elif [[ "$b_status" == *"FAILED"* || "$b_tail" == *"ninja failed"* || "$b_tail" == *"failed to build"* ]]; then
            echo -e " ${BOLD}BUILD STATE   :${NC} ${RED}✖ FAILED${NC}"
            echo -e " ${BOLD}LAST PROGRESS :${NC} ${YELLOW}${b_progress}${NC}"
        elif [[ "$b_status" == *"CANCELLED"* ]]; then
            echo -e " ${BOLD}BUILD STATE   :${NC} ${YELLOW}⏹ CANCELLED${NC}"
        elif [[ "$b_status" == *"RUNNING"* ]]; then
            echo -e " ${BOLD}BUILD STATE   :${NC} ${YELLOW}● RUNNING${NC} (no compiler process detected; between phases or runner detached)"
            echo -e " ${BOLD}LAST PROGRESS :${NC} ${YELLOW}${b_progress}${NC}"
        else
            echo -e " ${BOLD}BUILD STATE   :${NC} ${BLUE}○ IDLE${NC} (${b_status})"
        fi

        echo -e " ${BOLD}TARGET        :${NC} ${CYAN}${target_desc}${NC}"
        echo -e " ${BOLD}NET SPEED     :${NC} ${CYAN}${b_net_speed}${NC}"
        if [ -n "$b_log" ]; then
            echo -e " ${BOLD}LOG FILE      :${NC} ${b_log} (${b_log_size})"
        else
            echo -e " ${BOLD}LOG FILE      :${NC} ${YELLOW}not found${NC}"
        fi
        [ -n "$b_status_file" ] && echo -e " ${BOLD}STATUS FILE   :${NC} ${b_status_file}"
        echo -e " ${BOLD}IDLE WATCHDOG :${NC} ${CYAN}${b_watchdog}${NC} (30m auto-poweroff)"
        echo -e "${CYAN}=================================================================${NC}"

        if [ -n "$b_errors" ]; then
            echo -e "${RED}${BOLD}>>> DETECTED ERROR SUMMARY:${NC}"
            printf '%s\n' "$b_errors"
            echo -e "${YELLOW}-----------------------------------------------------------------${NC}"
        fi

        echo -e "${BOLD}>>> LAST LINES OF ACTIVE BUILD OUTPUT:${NC}"
        echo -e "${YELLOW}-----------------------------------------------------------------${NC}"
        if [ -n "$b_tail" ]; then
            echo "$b_tail"
        else
            echo "No recent build output found."
        fi
        echo -e "${YELLOW}-----------------------------------------------------------------${NC}"

        echo ""
        # Live follow only makes sense while a build is actually writing.
        # Everything else keeps the scrollable full-log pager.
        local b_active=0
        if [ "${b_procs:-0}" -gt 0 ] 2>/dev/null || [[ "$b_status" == *"RUNNING"* ]]; then
            b_active=1
        fi
        if [ "$b_active" -eq 1 ]; then
            echo -e " ${BOLD}1)${NC} ${GREEN}Follow Live Build Log${NC} (realtime updates, Ctrl+C to stop)"
        else
            echo -e " ${BOLD}1)${NC} View Full Build Log (less)"
        fi
        echo -e " ${BOLD}2)${NC} ${CYAN}Download Build Log (.log) to Downloads${NC}"
        echo -e " ${BOLD}3)${NC} ${YELLOW}Scan Log for Errors (grep FAILED with context)${NC}"
        echo -e " ${BOLD}4)${NC} ${RED}Stop Active Build${NC}"
        echo -e " ${BOLD}0)${NC} Back to Main Menu"
        echo ""
        read -rp "Select option [0-4]: " log_choice
        case "$log_choice" in
            1)
                if [ "$b_active" -eq 1 ]; then
                    follow_build_log_live "$b_log" 400
                else
                    view_build_log_snapshot "$b_log"
                fi
                ;;
            2)
                download_artifacts "log" "$b_log"
                ;;
            3)
                echo -e "\n${RED}=================================================================${NC}"
                echo -e "${BOLD} 🔍 SCANNING BUILD LOG FOR COMPILATION ERRORS                    ${NC}"
                echo -e "${RED}=================================================================${NC}"
                echo -e " Log Target: ${BOLD}$b_log${NC}\n"
                vm_exec "
LOG='$b_log'
[ -f \"\$LOG\" ] || { echo \"Error: log file not found: \$LOG\"; exit 1; }
CLEAN=\$(mktemp)
trap 'rm -f \"\$CLEAN\"' EXIT
tr '\r' '\n' < \"\$LOG\" | sed -E 's/\x1b\[[0-9;?]*[A-Za-z]//g' > \"\$CLEAN\"
FAILED_N=\$(grep -acE 'FAILED:' \"\$CLEAN\" || true)
ERROR_N=\$(grep -acE '(^|: )error:|^ERROR|^ninja: build stopped' \"\$CLEAN\" || true)
echo \">>> 1. TARGET FAILURES (FAILED: with 12 lines of context) [\$FAILED_N matches]\"
echo '-----------------------------------------------------------------'
if [ \"\${FAILED_N:-0}\" -gt 0 ]; then
    grep -anE -B 1 -A 12 'FAILED:' \"\$CLEAN\" | tail -n 140
else
    echo 'No FAILED targets found in this log.'
    echo 'If the build is still running, a failure may not have occurred yet.'
fi
echo ''
echo \">>> 2. COMPILER / NINJA ROOT CAUSES [\$ERROR_N matches]\"
echo '-----------------------------------------------------------------'
if [ \"\${ERROR_N:-0}\" -gt 0 ]; then
    grep -anE -B 1 -A 6 '(^|: )error:|^ERROR|^ninja: build stopped' \"\$CLEAN\" | tail -n 100
else
    echo 'No explicit compiler errors found.'
fi
echo '-----------------------------------------------------------------'
"
                echo ""
                read -rp "Press Enter to return (or type 'less' to page all errors): " scan_ch
                if [ "$scan_ch" = "less" ]; then
                    vm_exec_tty "tr '\r' '\n' < '$b_log' 2>/dev/null | sed -E 's/\x1b\[[0-9;?]*[A-Za-z]//g' | grep -anE -B 1 -A 12 'FAILED:' | less -R --mouse +G"
                fi
                ;;
            4)
                stop_build
                ;;
            0|*)
                return 0
                ;;
        esac
    done
}

function monitor_processes() {
    local direct_tool="$1"
    if [ "$direct_tool" = "htop" ]; then
        if ! require_vm_running "launch htop"; then
            return
        fi
        echo -e "${GREEN}>>> Launching htop on $INSTANCE... (Press 'q' or F10 to exit)${NC}\n"
        sleep 0.5
        vm_exec_tty "htop"
        return
    elif [ "$direct_tool" = "top" ]; then
        if ! require_vm_running "launch top"; then
            return
        fi
        echo -e "${GREEN}>>> Launching top on $INSTANCE... (Press 'q' to exit)${NC}\n"
        sleep 0.5
        vm_exec_tty "top"
        return
    elif [ "$direct_tool" = "net" ]; then
        if ! require_vm_running "monitor network speed"; then
            return
        fi
        echo -e "${GREEN}>>> Monitoring live VM network speed on $INSTANCE... (Press Ctrl+C to exit)${NC}\n"
        local ns_setup
        ns_setup=$(build_vm_net_speed_setup_cmd)
        vm_exec_tty "$ns_setup
while true; do
  clear
  echo '====================================================='
  echo '       LIVE VM NETWORK SPEED (1s refresh)            '
  echo '====================================================='
  printf '  Speed : %s\n' \"\$(/usr/local/bin/vm_net_speed.sh 2>/dev/null || echo 'DL -- | UL --')\"
  awk '/:/ { gsub(/:/, \" \"); if (\$1 != \"lo\" && \$1 !~ /^(veth|docker|br-|virbr)/) { printf \"  %-8s RX: %.2f GB | TX: %.2f GB\n\", \$1, \$2/1073741824, \$10/1073741824 } }' /proc/net/dev
  echo '-----------------------------------------------------'
  echo 'Press Ctrl+C to return...'
  sleep 1
done"
        return
    fi

    while true; do
        clear 2>/dev/null || true
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}                     LIVE SYSTEM MONITOR                         ${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        echo -e " ${BOLD}1)${NC} ${GREEN}htop${NC}      - Interactive color CPU & RAM monitor"
        echo -e " ${BOLD}2)${NC} ${CYAN}top${NC}       - Standard UNIX process monitor"
        echo -e " ${BOLD}3)${NC} ${YELLOW}net speed${NC} - Live VM Download / Upload speed (MB/s)"
        echo -e " ${BOLD}0)${NC} Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp " Select monitor [0-3]: " p_choice

        case "$p_choice" in
            1)
                echo ""
                if ! require_vm_running "launch htop"; then
                    continue
                fi
                echo -e "${GREEN}>>> Launching htop on $INSTANCE... (Press 'q' or F10 to exit)${NC}\n"
                sleep 0.5
                vm_exec_tty "htop"
                ;;
            2)
                echo ""
                if ! require_vm_running "launch top"; then
                    continue
                fi
                echo -e "${GREEN}>>> Launching top on $INSTANCE... (Press 'q' to exit)${NC}\n"
                sleep 0.5
                vm_exec_tty "top"
                ;;
            3)
                monitor_processes "net"
                ;;
            0|*)
                break
                ;;
        esac
    done
}

function stop_build() {
    clear 2>/dev/null || true
    echo -e "${RED}============================================================${NC}"
    echo -e "${BOLD} 🛑 STOP / CANCEL RUNNING BUILD${NC}"
    echo -e "${RED}============================================================${NC}"

    if ! require_vm_running "stop build (no active VM or build processes)"; then
        return 0
    fi

    echo -e "${YELLOW}>>> Active Build & Compilation Processes on $INSTANCE:${NC}"
    vm_exec "ps aux | grep -E 'ninja|soong|repo|python3.*upload|build/envsetup|make' | grep -v grep || echo 'None (No active compiler processes).'"
    echo ""

    read -rp "Are you sure you want to STOP and cancel the current build? (y/N): " confirm_stop
    if [[ ! "$confirm_stop" =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}>>> Aborted. Build continues running.${NC}"
        read -rp "Press Enter to return to menu..."
        return 0
    fi

    run_step "Terminating compiler processes and closing tmux build session" \
        vm_exec "tmux kill-session -t rom-build 2>/dev/null || true; \
             pkill -SIGINT -f 'ninja|soong|repo|python3.*upload' 2>/dev/null || true; \
             sleep 1; \
             pkill -9 -f 'ninja|soong|repo' 2>/dev/null || true; \
             for f in /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status; do \
                 [ -f \"\$f\" ] && echo 'BUILD_STATUS: CANCELLED by user - '\$(date) > \"\$f\"; \
             done"

    echo -e "\n${GREEN}>>> Build has been terminated.${NC}"
    read -rp "Press Enter to return to menu..."
}

function download_artifacts() {
    local type="$1"
    local requested_log="${2:-}"
    local dest_dir="$HOME/Downloads"
    mkdir -p "$dest_dir"

    echo ""
    run_step "Checking GCP VM status" check_vm_online
    if [ $? -ne 0 ]; then
        echo ""
        echo -e "${YELLOW}=================================================================${NC}"
        echo -e " ${BOLD}>>> [VM STATUS: TERMINATED / OFF]${NC}"
        echo -e "     VM '$INSTANCE' is currently powered OFF."
        echo -e "     Compute charges are paused."
        echo -e "${YELLOW}=================================================================${NC}"
        if [ -t 0 ]; then
            read -rp " Power on VM to download artifacts? (y/N): " start_in
            if [[ "$start_in" =~ ^[Yy]$ ]]; then
                start_vm
            else
                echo -e "${GREEN}>>> Aborted. VM remains powered OFF to pause compute billing.${NC}"
                read -rp "Press Enter to return to menu..."
                return 0
            fi
        else
            echo -e "${RED}Error: VM '$INSTANCE' is powered OFF. Start VM first: ./gcp_menu.sh start${NC}"
            return 1
        fi
    fi

    local ip
    ip=$(get_vm_ip 2>/dev/null)

    if [ "$type" = "log" ]; then
        local remote_log remote_size label
        if [ -n "$requested_log" ]; then
            remote_log="$requested_log"
        else
            # Prefer the newest status file so the log belongs to the build the
            # menu just showed, not merely the most recently touched file.
            remote_log=$(vm_exec 'f=$(ls -t /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status 2>/dev/null | head -n 1); if [ -n "$f" ]; then echo "$(dirname "$f")/build.log"; else ls -t /mnt/a16/build.log /mnt/a17/build.log /mnt/recovery/build.log 2>/dev/null | head -n 1; fi' 2>/dev/null || true)
            remote_log=$(echo "$remote_log" | tr -d '\r' | xargs)
        fi
        if [ -z "$remote_log" ]; then
            echo -e "${YELLOW}>>> No active build.log found on VM (/mnt/a16, /mnt/a17, or /mnt/recovery).${NC}"
            read -rp "Press Enter to return to menu..."
            return 0
        fi

        remote_size=$(vm_exec "stat -c %s '$remote_log' 2>/dev/null || echo 0" 2>/dev/null | tr -d '\r' | xargs)
        label=$(basename "$(dirname "$remote_log")")

        local log_ts
        log_ts=$(date +%Y%m%d_%H%M%S)
        local local_file="$dest_dir/build_${label}_${log_ts}.log"

        echo -e "\n${CYAN}>>> Downloading $remote_log -> $local_file${NC}"
        if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
            scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o StrictHostKeyChecking=no -i "$HOME/.ssh/google_compute_engine" \
                "$USER@$ip:$remote_log" "$local_file"
        else
            gcloud compute scp "${INSTANCE}:$remote_log" "$local_file" --zone="$ZONE" --project="$PROJECT"
        fi

        if [ -f "$local_file" ]; then
            local fsize local_size
            fsize=$(ls -lh "$local_file" | awk '{print $5}')
            local_size=$(wc -c < "$local_file" | tr -d ' ')
            echo ""
            echo -e "${GREEN}============================================================${NC}"
            echo -e "${GREEN} ✅ Full build log downloaded successfully!${NC}"
            echo -e "    Source   : ${BOLD}${remote_log}${NC} (${remote_size} bytes)"
            echo -e "    Saved to : ${BOLD}${local_file}${NC} (${fsize}, ${local_size} bytes)"
            if [ "${remote_size:-0}" != "$local_size" ]; then
                echo -e "    ${YELLOW}WARNING: byte count differs — the log may still be growing.${NC}"
            fi

            # Also write a readable copy: PTY logs are full of ANSI/CR control codes.
            local clean_file="${local_file%.log}_clean.log"
            if command -v perl >/dev/null 2>&1; then
                perl -pe 's/\e\[[0-9;?]*[A-Za-z]//g; s/\r/\n/g' "$local_file" > "$clean_file" 2>/dev/null || true
                if [ -s "$clean_file" ]; then
                    echo -e "    Clean log: ${BOLD}${clean_file}${NC} (ANSI/CR removed)"
                fi
            fi
            echo -e "${GREEN}============================================================${NC}"
        else
            echo -e "${RED}>>> Download failed: $local_file was not created.${NC}"
        fi
        read -rp "Press Enter to return to menu..."
        return 0
    fi

    for d in /mnt/a16 /mnt/a17 /mnt/recovery; do
        if [ "$type" = "rom" ]; then
            local has_rom
            has_rom=$(vm_exec "ls ${d}/*/out/target/product/*/*.zip 2>/dev/null | head -n 1" 2>/dev/null || true)
            if [ -n "$has_rom" ]; then
                echo -e "\n${CYAN}>>> Downloading ROM zip(s) from $d -> $dest_dir${NC}"
                if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
                    scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o StrictHostKeyChecking=no -i "$HOME/.ssh/google_compute_engine" \
                        "$USER@$ip:${d}/*/out/target/product/*/*.zip" "$dest_dir/"
                else
                    gcloud compute scp --recurse "${INSTANCE}:${d}/*/out/target/product/*/*.zip" "$dest_dir/" \
                        --zone="$ZONE" --project="$PROJECT"
                fi
            else
                echo -e " ${YELLOW}ℹ${NC} No ROM zip packages found in $d."
            fi
        else
            local has_rec
            has_rec=$(vm_exec "ls ${d}/*/out/target/product/*/*OrangeFox* ${d}/*/out/target/product/*/*twrp* ${d}/*/out/target/product/*/*TWRP* 2>/dev/null | head -n 1" 2>/dev/null || true)
            if [ -n "$has_rec" ]; then
                echo -e "\n${CYAN}>>> Downloading Recovery packages from $d -> $dest_dir${NC}"
                if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
                    scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o StrictHostKeyChecking=no -i "$HOME/.ssh/google_compute_engine" \
                        "$USER@$ip:${d}/*/out/target/product/*/*OrangeFox*" "$USER@$ip:${d}/*/out/target/product/*/*twrp*" "$USER@$ip:${d}/*/out/target/product/*/*TWRP*" "$dest_dir/" 2>/dev/null || true
                else
                    gcloud compute scp --recurse \
                        "${INSTANCE}:${d}/*/out/target/product/*/*OrangeFox*" \
                        "${INSTANCE}:${d}/*/out/target/product/*/*twrp*" \
                        "${INSTANCE}:${d}/*/out/target/product/*/*TWRP*" \
                        "$dest_dir/" --zone="$ZONE" --project="$PROJECT" 2>/dev/null || true
                fi
            else
                echo -e " ${YELLOW}ℹ${NC} No Recovery packages found in $d."
            fi
        fi
    done

    echo ""
    echo -e "${GREEN}>>> Check downloaded files in: $dest_dir${NC}"
    ls -lh "$dest_dir" 2>/dev/null | tail -n 8 || true
    read -rp "Press Enter to return to menu..."
}

function upload_sf_key() {
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD} Uploading SourceForge SSH Key to VM${NC}"
    echo -e "${CYAN}============================================================${NC}"

    ensure_vm_online

    local local_key="$HOME/.ssh/crave_sf"
    if [ ! -f "$local_key" ]; then
        echo -e "${RED}Error: Local key $local_key not found.${NC}"
        read -rp "Press Enter to continue..."
        return 1
    fi

    local ip
    ip=$(get_vm_ip 2>/dev/null)
    if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
        run_step "Uploading $local_key to VM (~/.ssh/crave_sf)" \
            scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o StrictHostKeyChecking=no -i "$HOME/.ssh/google_compute_engine" "$local_key" "$USER@$ip:~/.ssh/crave_sf"
    else
        run_step "Uploading $local_key to VM (~/.ssh/crave_sf)" \
            gcloud compute scp "$local_key" "${INSTANCE}:~/.ssh/crave_sf" --zone="$ZONE" --project="$PROJECT"
    fi
    vm_exec "mkdir -p ~/.ssh && chmod 700 ~/.ssh && chmod 600 ~/.ssh/crave_sf"

    run_step "Testing SourceForge SSH authentication" \
        vm_exec "ssh -i ~/.ssh/crave_sf -o StrictHostKeyChecking=no -T nikhilpal2705@frs.sourceforge.net 2>&1 | head -n 5 || true"

    echo ""
    echo -e "${GREEN}>>> SourceForge key uploaded and verified successfully!${NC}"
    read -rp "Press Enter to return to menu..."
}

function deploy_watchdog() {
    ensure_vm_online
    local target
    target="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || realpath "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
    local script_dir
    script_dir="$(cd "$(dirname "$target")" && pwd)"
    if [ -f "$script_dir/idle_watchdog.sh" ]; then
        local ip
        ip=$(get_vm_ip 2>/dev/null)
        if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
            run_step "Uploading idle_watchdog.sh to VM" \
                scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o StrictHostKeyChecking=no -i "$HOME/.ssh/google_compute_engine" \
                    "$script_dir/idle_watchdog.sh" "$USER@$ip:/tmp/idle_watchdog.sh"
            vm_exec "sudo mv /tmp/idle_watchdog.sh /usr/local/bin/idle_watchdog.sh && sudo chmod +x /usr/local/bin/idle_watchdog.sh"
        else
            run_step "Uploading idle_watchdog.sh to VM" \
                gcloud compute scp "$script_dir/idle_watchdog.sh" "${INSTANCE}:/tmp/idle_watchdog.sh" --zone="$ZONE" --project="$PROJECT"
            vm_exec "sudo mv /tmp/idle_watchdog.sh /usr/local/bin/idle_watchdog.sh && sudo chmod +x /usr/local/bin/idle_watchdog.sh"
        fi
    fi
    run_step "Configuring idle-shutdown systemd unit on VM" \
        vm_exec "sudo tee /etc/systemd/system/idle-shutdown.service > /dev/null << 'EOF_UNIT'
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
EOF_UNIT"
    run_step "Enabling and restarting idle-shutdown service" \
        vm_exec "sudo systemctl daemon-reload && sudo systemctl enable --now idle-shutdown.service && sudo systemctl restart idle-shutdown.service"
    echo -e "${GREEN}>>> Auto-Idle Shutdown Watchdog successfully active (30 min threshold).${NC}"
}

function setup_environment() {
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD} Setting up Build Environment on GCP Instance${NC}"
    echo -e "${CYAN}============================================================${NC}"

    ensure_vm_online

    local target
    target="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || realpath "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
    SCRIPT_DIR="$(cd "$(dirname "$target")" && pwd)"
    if [ -f "$SCRIPT_DIR/setup_host.sh" ]; then
        local ip
        ip=$(get_vm_ip 2>/dev/null)
        if [ -n "$ip" ] && [ -f "$HOME/.ssh/google_compute_engine" ]; then
            run_step "Deploying host scripts to VM" \
                scp -o ControlMaster=auto -o ControlPath="$SSH_SOCK" -o StrictHostKeyChecking=no -i "$HOME/.ssh/google_compute_engine" \
                    "$SCRIPT_DIR/setup_host.sh" "$SCRIPT_DIR/upload.py" "$SCRIPT_DIR/idle_watchdog.sh" "$USER@$ip:~/"
        else
            run_step "Deploying host scripts to VM" \
                gcloud compute scp "$SCRIPT_DIR/setup_host.sh" "$SCRIPT_DIR/upload.py" "$SCRIPT_DIR/idle_watchdog.sh" \
                    "${INSTANCE}:~/" --zone="$ZONE" --project="$PROJECT"
        fi
    else
        run_step "Downloading host scripts from GitHub" \
            vm_exec "T=\$(date +%s); \
                 curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' \"https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/setup_host.sh?t=\$T\" -o ~/setup_host.sh && \
                 curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' \"https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/upload.py?t=\$T\" -o ~/upload.py && \
                 curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' \"https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/idle_watchdog.sh?t=\$T\" -o ~/idle_watchdog.sh"
    fi

    echo -e "\n${YELLOW}>>> Running setup_host.sh on VM (system packages, repo, udev rules)...${NC}"
    vm_exec_tty "chmod +x ~/setup_host.sh ~/upload.py && bash ~/setup_host.sh"

    upload_sf_key
}

function menu_tools() {
    while true; do
        clear 2>/dev/null || true
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}                     VM SETUP & TOOLS                            ${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        echo -e " ${BOLD}1)${NC} Run Environment Setup (Packages, repo, ccache)"
        echo -e " ${BOLD}2)${NC} Upload SourceForge SSH Key (~/.ssh/crave_sf)"
        echo -e " ${BOLD}3)${NC} Deploy 30m Auto-Idle Shutdown Watchdog"
        echo -e " ${BOLD}0)${NC} Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp "Select option [0-3]: " t_choice

        case "$t_choice" in
            1) setup_environment; break ;;
            2) upload_sf_key; break ;;
            3)
                if ! require_vm_running "check watchdog status"; then
                    continue
                fi
                echo -e "\n${CYAN}>>> Auto-Idle Shutdown Watchdog Status on $INSTANCE:${NC}"
                vm_exec "if [ -f /run/idle_watchdog.status ]; then echo -n 'Current Status: '; cat /run/idle_watchdog.status; fi; \
                     echo -e '\nService Info:'; systemctl status idle-shutdown.service --no-pager || true"
                echo ""
                read -rp "Re-deploy / Restart watchdog service now? (y/N): " r_w
                if [[ "$r_w" =~ ^[Yy]$ ]]; then
                    deploy_watchdog
                fi
                read -rp "Press Enter to return to menu..."
                ;;
            0|*) break ;;
        esac
    done
}

function create_swap_on_mount() {
    local target_mp="$1"
    if ! require_vm_running "allocate swap memory"; then
        return 1
    fi
    if ! vm_exec "mountpoint -q '$target_mp'" 2>/dev/null; then
        echo -e "${RED}Error: '$target_mp' is not mounted on $INSTANCE.${NC}"
        echo -e "${YELLOW}Start a build or attach/mount a specific disk for $target_mp first (prevents filling the boot disk).${NC}"
        read -rp "Press Enter to continue..."
        return 1
    fi
    read -rp "Enter swap size in GB [default: 16]: " s_size
    s_size="${s_size:-16}"
    run_step "Allocating ${s_size}GB swapfile at ${target_mp}/swapfile and activating" \
        vm_exec "sudo fallocate -l ${s_size}G '${target_mp}/swapfile' && \
             sudo chmod 600 '${target_mp}/swapfile' && \
             sudo mkswap '${target_mp}/swapfile' && \
             sudo swapon '${target_mp}/swapfile'"
    vm_exec "swapon --show"
    read -rp "Press Enter to continue..."
}

function print_disk_tables() {
    local disks_csv attached_csv vm_usage=""
    disks_csv=$(gcloud compute disks list --project="$PROJECT" --zones="$ZONE" \
        --format="csv[no-heading](name,zone.basename(),sizeGb,type.basename(),status)" 2>/dev/null || true)
    attached_csv=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --flatten="disks" --format="csv[no-heading](disks.source.basename(),disks.deviceName,disks.boot)" 2>/dev/null || true)

    if is_vm_running; then
        vm_usage=$(vm_exec '
for link in /dev/disk/by-id/google-*; do
  [ -e "$link" ] || continue
  case "$link" in *-part*) continue ;; esac
  devname="${link##*/google-}"
  blk=$(readlink -f "$link" 2>/dev/null || true)
  [ -b "$blk" ] || continue

  mp=$(lsblk -rn -o MOUNTPOINT "$blk" 2>/dev/null | grep -v "^$" | grep -v "^\[SWAP\]$" | awk "\$0 !~ /^\\/boot/ { print; found=1; exit } END { if (!found) exit 0 }")
  if [ -n "$mp" ]; then
    df -hP "$mp" 2>/dev/null | awk -v d="$devname" -v m="$mp" "NR==2{ printf \"%s|%s|%s|%s|%s\\n\", d, \$3, \$4, \$5, m }"
    continue
  fi

  fs=$(lsblk -rn -o FSTYPE "$blk" 2>/dev/null | head -n 1)
  if [ "$fs" = "ext4" ] || [ "$fs" = "ext3" ] || [ "$fs" = "ext2" ]; then
    out=$(sudo -n dumpe2fs -h "$blk" 2>/dev/null | awk -v d="$devname" "
      function h(bytes) {
        if (bytes >= 1073741824) return sprintf(\"%.0fG\", bytes / 1073741824);
        if (bytes >= 1048576)    return sprintf(\"%.0fM\", bytes / 1048576);
        return sprintf(\"%.0fK\", bytes / 1024);
      }
      /^Block count:/  { bc = \$NF + 0 }
      /^Free blocks:/  { fb = \$NF + 0 }
      /^Block size:/   { bs = \$NF + 0 }
      END {
        if (bc > 0 && bs > 0) {
          used = (bc - fb) * bs;
          free = fb * bs;
          pct  = (bc > 0) ? sprintf(\"%.0f%%\", ((bc - fb) * 100.0) / bc) : \"-\";
          printf \"%s|%s|%s|%s|(unmounted)\\n\", d, h(used), h(free), pct;
        }
      }
    ")
    if [ -n "$out" ]; then
      echo "$out"
      continue
    fi
  fi
  echo "${devname}|-|-|-|(unmounted)"
done
' 2>/dev/null || true)
    fi

    local vm_state_label="(VM OFF)"
    [ -n "$vm_usage" ] && vm_state_label=""

    {
        echo "===DISKS==="
        [ -n "$disks_csv" ] && printf '%s\n' "$disks_csv"
        echo "===ATTACHED==="
        [ -n "$attached_csv" ] && printf '%s\n' "$attached_csv"
        echo "===USAGE==="
        [ -n "$vm_usage" ] && printf '%s\n' "$vm_usage"
    } | awk -v state_off="$vm_state_label" '
        /^===DISKS===$/    { sec = "disks"; next }
        /^===ATTACHED===$/ { sec = "attached"; next }
        /^===USAGE===$/    { sec = "usage"; next }
        /^$/ { next }
        sec == "disks" {
            dc++;
            split($0, f, ",");
            d_name[dc] = f[1]; d_zone[dc] = f[2]; d_size[dc] = f[3]; d_type[dc] = f[4]; d_stat[dc] = f[5];
        }
        sec == "attached" {
            ac++;
            split($0, f, ",");
            a_disk[ac] = f[1]; a_dev[ac] = f[2]; a_boot[ac] = f[3];
            dev_for_disk[f[1]] = f[2];
        }
        sec == "usage" {
            split($0, f, "|");
            u_used[f[1]] = f[2]; u_free[f[1]] = f[3]; u_pct[f[1]] = f[4]; u_mp[f[1]] = f[5];
        }
        END {
            print "Disks in Project:";
            printf "%-18s %-14s %-7s %-7s %-7s %-6s %-12s %-5s %s\n", "NAME", "ZONE", "SIZE", "USED", "FREE", "USE%", "TYPE", "MEDIA", "STATUS";
            if (dc == 0) {
                print "  (none)";
            } else {
                for (i = 1; i <= dc; i++) {
                    dn = d_name[i];
                    dv = (dn in dev_for_disk) ? dev_for_disk[dn] : "";
                    us = (dv != "" && dv in u_used) ? u_used[dv] : "-";
                    fr = (dv != "" && dv in u_free) ? u_free[dv] : "-";
                    pc = (dv != "" && dv in u_pct)  ? u_pct[dv]  : "-";
                    # pd-standard is spinning HDD; pd-balanced/pd-ssd/extreme
                    # and hyperdisk/local-ssd are flash.  Show it explicitly so
                    # the size/price trade-off is obvious at a glance.
                    media = (d_type[i] == "pd-standard") ? "HDD" : \
                            (d_type[i] ~ /^(pd-ssd|pd-balanced|pd-extreme|hyperdisk|local-ssd)/) ? "SSD" : "?";
                    printf "%-18s %-14s %-7s %-7s %-7s %-6s %-12s %-5s %s\n",
                        dn, d_zone[i], d_size[i] "G", us, fr, pc, d_type[i], media, d_stat[i];
                }
            }
            print "";
            printf "Attached to VM:%s\n", (state_off != "" ? " " state_off : "");
            printf "%-18s %-22s %-6s %-15s %-7s %-7s %s\n", "ATTACHED_DISK", "DEVICE_NAME", "BOOT", "MOUNT", "USED", "FREE", "USE%";
            if (ac == 0) {
                print "  (none or VM unreachable)";
            } else {
                for (i = 1; i <= ac; i++) {
                    dv = a_dev[i];
                    mp = (dv in u_mp)   ? u_mp[dv]   : (state_off != "" ? state_off : "-");
                    us = (dv in u_used) ? u_used[dv] : "-";
                    fr = (dv in u_free) ? u_free[dv] : "-";
                    pc = (dv in u_pct)  ? u_pct[dv]  : "-";
                    printf "%-18s %-22s %-6s %-15s %-7s %-7s %s\n",
                        a_disk[i], dv, a_boot[i], mp, us, fr, pc;
                }
            }
        }
    '
}

function menu_manage_disk() {
    while true; do
        clear 2>/dev/null || true
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}                     GCP ROM DISK MANAGER                        ${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        print_disk_tables
        echo ""
        echo -e " ${BOLD}[CREATE & LINK]${NC}"
        echo -e "  ${GREEN}1)${NC} Create General Shared Disk (500GB - For all ROMs & Recovery)"
        echo -e "  ${GREEN}2)${NC} Create A16 Disk (400GB - Dedicated /mnt/a16)"
        echo -e "  ${GREEN}3)${NC} Create A17 Disk (400GB - Dedicated /mnt/a17)"
        echo -e "  ${GREEN}4)${NC} Create Recovery Disk (400GB - Dedicated /mnt/recovery)"
        echo ""
        echo -e " ${BOLD}[ATTACH / DETACH / FORMAT]${NC}"
        echo -e "  ${BLUE}5)${NC} Attach Disk"
        echo -e "  ${YELLOW}6)${NC} Detach Disk"
        echo -e "  ${RED}7)${NC} Format Disk (Clean ext4 wipe)"
        echo ""
        echo -e " ${BOLD}[DELETE - STOP BILLING]${NC}"
        echo -e "  ${RED}8)${NC} Delete Disk Permanently"
        echo ""
        echo -e " ${BOLD}[INFO]${NC}"
        echo -e "  ${CYAN}9)${NC} Check Disk Space & Mounts"
        echo "  0) Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp "Select option [0-9]: " disk_choice

        case "$disk_choice" in
            1)
                create_and_attach_disk "$GENERAL_DISK" 500 "Shared 500GB general SSD storage for ROM and Recovery builds"
                read -rp "Press Enter to continue..."
                ;;
            2)
                create_and_attach_disk "gcp-rom-a16-disk" 400 "Dedicated 400GB SSD storage for Android 16 ROM builds"
                read -rp "Press Enter to continue..."
                ;;
            3)
                create_and_attach_disk "gcp-rom-a17-disk" 400 "Dedicated 400GB SSD storage for Android 17 ROM builds"
                read -rp "Press Enter to continue..."
                ;;
            4)
                create_and_attach_disk "gcp-recovery-disk" 400 "Dedicated 400GB storage for Recovery builds (OFRP + TWRP)"
                read -rp "Press Enter to continue..."
                ;;
            5)
                attach_disk_action
                read -rp "Press Enter to continue..."
                ;;
            6)
                detach_disk_action
                read -rp "Press Enter to continue..."
                ;;
            7)
                format_disk_action
                read -rp "Press Enter to continue..."
                ;;
            8)
                detach_and_delete_disk
                read -rp "Press Enter to continue..."
                ;;
            9)
                if ! require_vm_running "check live disk space and mounts"; then
                    continue
                fi
                vm_exec "df -h / /mnt/a16 /mnt/a17 /mnt/recovery 2>/dev/null; echo ''; lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT /dev/sd* 2>/dev/null || true; echo ''; free -h; swapon --show 2>/dev/null"
                read -rp "Press Enter to continue..."
                ;;
            0|*)
                break
                ;;
        esac
    done
}

function menu_manage_swap() {
    while true; do
        clear 2>/dev/null || true
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}                    VM SWAP MEMORY MANAGER                       ${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}Current Memory & Swap on $INSTANCE:${NC}"
        if is_vm_running; then
            vm_exec "free -h; echo ''; echo 'Active Swap:'; swapon --show 2>/dev/null || echo 'None (Pure 64GB RAM mode)'"
        else
            echo -e " ${YELLOW}●${NC} VM is powered OFF (TERMINATED). Swap is configured on disk."
        fi
        echo ""
        echo -e " ${BOLD}1)${NC} ${GREEN}Create Swap on A16 Mount (/mnt/a16)${NC}"
        echo -e " ${BOLD}2)${NC} ${GREEN}Create Swap on A17 Mount (/mnt/a17)${NC}"
        echo -e " ${BOLD}3)${NC} ${GREEN}Create Swap on Recovery Mount (/mnt/recovery)${NC}"
        echo -e " ${BOLD}4)${NC} ${GREEN}Create Swap on Boot Disk (/)${NC}"
        echo -e " ${BOLD}5)${NC} ${YELLOW}Turn Off All Swap (swapoff -a)${NC}"
        echo -e " ${BOLD}6)${NC} ${RED}Delete Swapfile${NC}"
        echo -e " ${BOLD}0)${NC} Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp "Select option [0-6]: " swap_ch

        case "$swap_ch" in
            1) create_swap_on_mount "/mnt/a16" ;;
            2) create_swap_on_mount "/mnt/a17" ;;
            3) create_swap_on_mount "/mnt/recovery" ;;
            4)
                if ! require_vm_running "allocate swap memory"; then
                    continue
                fi
                read -rp "Enter swap size in GB [default: 4]: " s_size
                s_size="${s_size:-4}"
                run_step "Allocating ${s_size}GB swapfile on boot disk (/swapfile) and activating" \
                    vm_exec "sudo fallocate -l ${s_size}G /swapfile && \
                         sudo chmod 600 /swapfile && \
                         sudo mkswap /swapfile && \
                         sudo swapon /swapfile"
                vm_exec "swapon --show"
                read -rp "Press Enter to continue..."
                ;;
            5)
                if ! require_vm_running "disable swap memory"; then
                    continue
                fi
                run_step "Disabling all active swap" vm_exec "sudo swapoff -a"
                vm_exec "free -h"
                read -rp "Press Enter to continue..."
                ;;
            6)
                if ! require_vm_running "delete swapfile"; then
                    continue
                fi
                echo -e "${YELLOW}>>> Searching for existing swapfiles on VM...${NC}"
                vm_exec "ls -lh /swapfile /mnt/a16/swapfile /mnt/a17/swapfile /mnt/recovery/swapfile 2>/dev/null || echo 'No common swapfiles found.'"
                read -rp "Enter full path of swapfile to DELETE: " del_path
                if [ -n "$del_path" ]; then
                    run_step "Disabling and deleting swapfile $del_path" \
                        vm_exec "sudo swapoff '$del_path' 2>/dev/null || true; sudo rm -f '$del_path'"
                    vm_exec "free -h"
                fi
                read -rp "Press Enter to continue..."
                ;;
            0|*)
                break
                ;;
        esac
    done
}

function sync_upload_links() {
    local tmp_merge
    tmp_merge=$(mktemp /tmp/gcp_links.XXXXXX)
    [ -f "$LOCAL_LINKS_FILE" ] && cat "$LOCAL_LINKS_FILE" > "$tmp_merge"

    # Pull from GCP instance metadata (works even when VM is STOPPED).
    # Parse JSON locally because gcloud's value() projection can silently emit
    # an empty result for metadata map entries on some SDK versions.
    local metadata_json
    metadata_json=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
        --format=json 2>/dev/null || true)
    if [ -n "$metadata_json" ]; then
        printf '%s' "$metadata_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((x.get("value","") for x in d.get("metadata",{}).get("items",[]) if x.get("key")=="upload-links"), ""))' \
            2>/dev/null >> "$tmp_merge" || true
    fi

    # If VM is running, also pull ~/.upload_links.log directly
    local vm_state
    vm_state=$(get_vm_state 2>/dev/null || echo "UNKNOWN,")
    if [ "${vm_state%%,*}" = "RUNNING" ]; then
        vm_exec "cat ~/.upload_links.log 2>/dev/null || true" 2>/dev/null >> "$tmp_merge" || true
    fi

    # Deduplicate preserving order and drop empty lines
    awk 'NF && !seen[$0]++' "$tmp_merge" > "${tmp_merge}.clean"
    mv "${tmp_merge}.clean" "$LOCAL_LINKS_FILE"
    rm -f "$tmp_merge"
}

function cache_upload_links_from_output() {
    local output_file="$1"
    [ -s "$output_file" ] || return 0

    python3 - "$output_file" "$LOCAL_LINKS_FILE" <<'PY_LINKS'
import re
import sys
from pathlib import Path

source, destination = map(Path, sys.argv[1:])
ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
pattern = re.compile(r"^\[[^\]]+\]\s+\[[^\]]+\]\s+\[[^\]]+\]\s+.+\s->\shttps?://\S+")
lines = destination.read_text(encoding="utf-8", errors="replace").splitlines() if destination.exists() else []
seen = set(lines)
for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
    line = ansi.sub("", raw).strip()
    if pattern.match(line) and line not in seen:
        lines.append(line)
        seen.add(line)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY_LINKS
}

function view_upload_links() {
    echo ""
    run_step "Syncing upload links (local cache + VM log)" sync_upload_links
    clear 2>/dev/null || true
    echo -e "${CYAN}=================================================================${NC}"
    echo -e "${BOLD}           UPLOAD LINKS HISTORY (Gofile / SF / Filebin)          ${NC}"
    echo -e "${CYAN}=================================================================${NC}"
    echo -e " Cache File: ${CYAN}${LOCAL_LINKS_FILE}${NC} (Accessible even when VM is stopped)"
    echo -e "${CYAN}-----------------------------------------------------------------${NC}"
    if [ -s "$LOCAL_LINKS_FILE" ]; then
        nl -w2 -s') ' "$LOCAL_LINKS_FILE"
    else
        echo -e " ${YELLOW}No upload links recorded yet.${NC}"
    fi
    echo -e "${CYAN}=================================================================${NC}"
    if [ -t 0 ]; then
        read -rp " Press Enter to return (or 'c' to clear local cache): " link_ch
        if [[ "$link_ch" =~ ^[Cc]$ ]]; then
            rm -f "$LOCAL_LINKS_FILE"
            echo -e "${YELLOW}>>> Local link cache cleared.${NC}"
            sleep 1
        fi
    fi
}

function show_info() {
    local current_mt
    current_mt=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format="value(machineType.basename())" 2>/dev/null || echo "c2d-standard-16")
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${BOLD} GCP Build Farm Specifications & Architecture${NC}"
    echo -e "${CYAN}============================================================${NC}"
    echo -e "Machine Type : ${BOLD}${current_mt}${NC} (16 vCPUs, 64 GB RAM, Compute-Optimized for ROM Builds)"
    echo -e "Failover     : Auto-cascades across compute types (c2d -> t2d -> c2) if pool is exhausted"
    echo -e "RAM          : ${BOLD}64 GB Physical RAM${NC} (Pure RAM mode by default)"
    echo -e "Boot Disk    : ${BOLD}50 GB Standard Disk${NC} (Always Free Tier eligible)"
    echo -e "             - Contains OS, build packages & persistent ccache (5 GB cap)"
    echo -e "Storage Disks: Dedicated mountpoints (/mnt/a16, /mnt/a17, /mnt/recovery)"
    echo -e "             - ROM/Recovery Specific: gcp-rom-a16-disk, gcp-rom-a17-disk, gcp-recovery-disk (auto-mounted at boot)"
    echo -e "             - General Shared ($GENERAL_DISK, 500GB): fallback if no specific disk exists; mounts on demand at build time"
    echo -e "             - Format anytime via Disk Manager (Option 7) or './gcp_menu.sh format-disk' to reuse across ROMs"
    echo -e "             - Auto-deleted on demand to pause storage billing"
    echo -e "Swap Memory  : Optional on demand via Swap Manager (Option 25)"
    echo -e "Zone         : ${BOLD}${ZONE}${NC} (Lowest compute/storage rates globally)"
    echo -e "Running Cost : ~\$0.67-\$0.72 / hr (Only billed while RUNNING)"
    echo -e "Auto-Shutdown: ${BOLD}${GREEN}Enabled (30-Minute Idle Watchdog)${NC}"
    echo -e "             - Auto-powers off if no builds & no SSH users for 30 min"
    echo -e "             - Protects against runaway billing if build fails or completes"
    echo -e "Storage Cost : ~\$1.65 / day (500GB ROM SSD - delete when done to pause billing)"
    echo -e "${CYAN}============================================================${NC}"
}

function menu_manage_machine_type() {
    while true; do
        clear 2>/dev/null || true
        local current_mt
        current_mt=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format="value(machineType.basename())" 2>/dev/null || echo "UNKNOWN")
        local current_status
        current_status=$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format="value(status)" 2>/dev/null || echo "UNKNOWN")

        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}              MACHINE TYPE & FAILOVER MANAGER                    ${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        echo -e " Current Machine: ${GREEN}${BOLD}$current_mt${NC} | Status: ${GREEN}${BOLD}$current_status${NC}"
        echo ""
        echo -e "${BOLD}16-Core 64GB RAM Machine Options:${NC}"
        echo -e "  -----------------------------------------------------------------"
        echo -e "  #   Machine Type       Processor / Architecture       Hourly Cost"
        echo -e "  -----------------------------------------------------------------"
        local idx=1
        for mt in "${MACHINE_TYPES[@]}"; do
            local marker="  "
            [ "$mt" = "$current_mt" ] && marker="${GREEN}* ${NC}"
            local desc="AMD EPYC Milan (Compute-Opt)   ~\$0.726/hr  (Fastest)"
            [ "$mt" = "t2d-standard-16" ] && desc="AMD EPYC Milan (1:1 Cores)     ~\$0.675/hr  (Cheapest)"
            [ "$mt" = "c2-standard-16" ]  && desc="Intel Cascade Lake (Comp-Opt)  ~\$0.835/hr  (Intel Fallback)"
            printf "  %s%2d) %-18s %s\n" "$marker" "$idx" "$mt" "$desc"
            ((idx++))
        done
        echo -e "  -----------------------------------------------------------------"
        echo -e "  ${GREEN}A)${NC} ${BOLD}Set Fastest (c2d-standard-16)${NC}"
        echo -e "  ${GREEN}B)${NC} ${BOLD}Set Cheapest (t2d-standard-16)${NC}"
        echo -e "  ${BOLD}0)${NC} Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp "Select machine type [1-${#MACHINE_TYPES[@]}, A, B, or 0]: " mt_choice

        local target_mt=""
        case "$mt_choice" in
            1) target_mt="c2d-standard-16" ;;
            2) target_mt="t2d-standard-16" ;;
            3) target_mt="c2-standard-16" ;;
            [aA]) target_mt="c2d-standard-16" ;;
            [bB]) target_mt="t2d-standard-16" ;;
            0|"") return ;;
            *) echo -e "${RED}Invalid selection.${NC}"; sleep 1; continue ;;
        esac

        if [ "$target_mt" = "$current_mt" ]; then
            echo -e "${YELLOW}VM is already using $target_mt.${NC}"
            sleep 1
            continue
        fi

        echo -e "\n${YELLOW}>>> Switching VM machine type to $target_mt...${NC}"
        if [ "$current_status" = "RUNNING" ]; then
            run_step "Stopping VM to update machine type" \
                gcloud compute instances stop "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet
        fi

        run_step "Changing machine type to $target_mt" \
            gcloud compute instances set-machine-type "$INSTANCE" --machine-type="$target_mt" --zone="$ZONE" --project="$PROJECT" --quiet

        if [ "$current_status" = "RUNNING" ]; then
            run_step "Restarting VM with $target_mt" start_vm_with_failover
        fi
        read -rp "Press Enter to continue..."
    done
}

# Non-interactive CLI flag support
case "$1" in
    build|build-rom)
        ROM="evox"; VER="16"; DEV="RMX2170"; TYPE=""; SF="false"; GOFILE="true"; SHUT="true"; FORMAT_DISK_OPT="false"
        shift
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --rom) ROM="$2"; shift 2 ;;
                --ver|--version) VER="$2"; shift 2 ;;
                --dev|--device) DEV="$2"; shift 2 ;;
                --type|--variant) TYPE="$2"; shift 2 ;;
                --sf) SF="true"; shift ;;
                --gofile) GOFILE="true"; shift ;;
                --no-gofile) GOFILE="false"; shift ;;
                --shutdown)
                    if [[ "${2:-}" =~ ^(true|false)$ ]]; then SHUT="$2"; shift 2; else SHUT="true"; shift; fi
                    ;;
                --shutdown=true) SHUT="true"; shift ;;
                --shutdown=false) SHUT="false"; shift ;;
                --keep-alive) SHUT="false"; shift ;;
                --format) FORMAT_DISK_OPT="true"; shift ;;
                *) shift ;;
            esac
        done
        [ -n "$TYPE" ] && BUILD_TYPE_OPT="$TYPE"
        SF_OPT="$SF"; GOFILE_OPT="$GOFILE"; SHUT_OPT="$SHUT"
        ROM_LOWER=$(echo "$ROM" | tr '[:upper:]' '[:lower:]')
        [ "$ROM_LOWER" = "evox" ] && ROM_LOWER="evolution"
        DEV_LOWER=$(echo "$DEV" | tr '[:upper:]' '[:lower:]')
        if [ "$DEV_LOWER" = "all" ]; then
            run_build_with_feedback "aosp-${VER}" "${ROM_LOWER}_all.sh" "$ROM (A${VER}) - All Devices" || exit 1
        else
            run_build_with_feedback "aosp-${VER}" "${ROM_LOWER}_${DEV_LOWER}.sh" "$ROM (A${VER}) - $DEV" || exit 1
        fi
        exit 0
        ;;

    recovery|build-recovery)
        FLAVOR="ofrp"; DEV="sm7125"; TYPE=""; SF="false"; GOFILE="true"; SHUT="true"; FORMAT_DISK_OPT="false"
        shift
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --flavor) FLAVOR="$2"; shift 2 ;;
                --dev|--device) DEV="$2"; shift 2 ;;
                --type|--variant) TYPE="$2"; shift 2 ;;
                --sf) SF="true"; shift ;;
                --gofile) GOFILE="true"; shift ;;
                --no-gofile) GOFILE="false"; shift ;;
                --shutdown)
                    if [[ "${2:-}" =~ ^(true|false)$ ]]; then SHUT="$2"; shift 2; else SHUT="true"; shift; fi
                    ;;
                --shutdown=true) SHUT="true"; shift ;;
                --shutdown=false) SHUT="false"; shift ;;
                --keep-alive) SHUT="false"; shift ;;
                --format) FORMAT_DISK_OPT="true"; shift ;;
                *) shift ;;
            esac
        done
        [ -n "$TYPE" ] && BUILD_TYPE_OPT="$TYPE"
        SF_OPT="$SF"; GOFILE_OPT="$GOFILE"; SHUT_OPT="$SHUT"
        DEVICE_OPT="$DEV"
        if [ "$FLAVOR" = "twrp" ]; then
            run_build_with_feedback "recovery" "twrp_sm7125.sh" "TWRP Recovery ($DEV)" || exit 1
        elif [ "$FLAVOR" = "all" ]; then
            run_build_with_feedback "recovery" "recovery_all.sh" "All Recoveries Batch" || exit 1
        else
            run_build_with_feedback "recovery" "ofrp_sm7125.sh" "OrangeFox Recovery ($DEV)" || exit 1
        fi
        exit 0
        ;;

    setup) setup_environment; exit 0 ;;
    upload-key) upload_sf_key; exit 0 ;;
    create-disk) create_and_attach_disk "$2" "$3" "$4"; exit 0 ;;
    delete-disk) detach_and_delete_disk "$2"; exit 0 ;;
    attach-disk) attach_disk_action "$2"; exit 0 ;;
    detach-disk) detach_disk_action "$2"; exit 0 ;;
    format-disk) shift; format_disk_action "$@"; exit $? ;;
    type|machine-type)
        shift
        if [ -n "$1" ]; then
            target_mt="$1"
            [ "$target_mt" = "cheapest" ] && target_mt="t2d-standard-16"
            gcloud compute instances set-machine-type "$INSTANCE" --machine-type="$target_mt" --zone="$ZONE" --project="$PROJECT"
        else
            menu_manage_machine_type
        fi
        exit 0
        ;;
    swap) menu_manage_swap; exit 0 ;;
    swap-create)
        s_size="${2:-16}"
        s_path="${3:-/mnt/a16/swapfile}"
        case "$s_path" in
            /mnt/a17/*)
                resolve_build_disk "aosp-17"
                ensure_running "$RESOLVED_DISK" "$RESOLVED_MP" >/dev/null 2>&1
                ;;
            /mnt/recovery/*)
                resolve_build_disk "recovery"
                ensure_running "$RESOLVED_DISK" "$RESOLVED_MP" >/dev/null 2>&1
                ;;
            /mnt/a16/*)
                resolve_build_disk "aosp-16"
                ensure_running "$RESOLVED_DISK" "$RESOLVED_MP" >/dev/null 2>&1
                ;;
            *)
                ensure_vm_online >/dev/null 2>&1
                ;;
        esac
        vm_exec "sudo fallocate -l ${s_size}G '$s_path' && sudo chmod 600 '$s_path' && sudo mkswap '$s_path' && sudo swapon '$s_path' && swapon --show"
        exit 0
        ;;
    swap-off)
        if require_vm_running "disable swap"; then
            vm_exec "sudo swapoff -a; free -h"
        fi
        exit 0
        ;;
    start) start_vm; exit 0 ;;
    stop) stop_vm; exit 0 ;;
    links|upload-links) view_upload_links; exit 0 ;;
    download-log|log-download) download_artifacts "log"; exit 0 ;;
    logs|log|status-build) view_build_logs; exit 0 ;;
    stop-build|cancel-build|kill-build) stop_build; exit 0 ;;
    htop) monitor_processes "htop"; exit 0 ;;
    top) monitor_processes "top"; exit 0 ;;
    net|netspeed|net-speed) monitor_processes "net"; exit 0 ;;
    monitor) monitor_processes; exit 0 ;;
    status)
        gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
            --format="table(name,status,networkInterfaces[0].accessConfigs[0].natIP:label=EXTERNAL_IP,machineType.basename():label=MACHINE,zone.basename():label=ZONE)"
        exit 0
        ;;
    ssh)
        if ! require_vm_running "open SSH console"; then
            exit 1
        fi
        echo -e "\n${GREEN}>>> Connecting to VM console... (Type 'exit' to return)${NC}\n"
        vm_exec_tty "bash -l"
        exit 0
        ;;
    attach)
        if ! require_vm_running "attach to tmux session"; then
            exit 1
        fi
        attach_tmux_session
        exit 0
        ;;
    info|help|-h|--help) show_info; exit 0 ;;
esac

# ==============================================================================
# Interactive Visual Menu
# ==============================================================================
# Submenu: select single device or all-devices batch for a given ROM
function menu_select_rom_device() {
    local branch="$1"
    local rom_prefix="$2"
    local rom_label="$3"

    while true; do
        clear 2>/dev/null || true
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}  ${rom_label} - Select Target Device${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "   ${GREEN}1)${NC} Realme 7 Pro (RMX2170)"
        echo -e "   ${GREEN}2)${NC} Realme 6 Pro (RMX2061)"
        echo -e "   ${GREEN}3)${NC} Realme 8 Pro (RMX3081)"
        echo -e "   ${GREEN}4)${NC} All Devices Batch (RMX2170 + RMX2061 + RMX3081)"
        echo ""
        echo -e "   ${YELLOW}0)${NC} Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp " Select device [0-4]: " dev_choice

        case "$dev_choice" in
            1) run_build_with_feedback "$branch" "${rom_prefix}_rmx2170.sh" "${rom_label} - Realme 7 Pro (RMX2170)"; return ;;
            2) run_build_with_feedback "$branch" "${rom_prefix}_rmx2061.sh" "${rom_label} - Realme 6 Pro (RMX2061)"; return ;;
            3) run_build_with_feedback "$branch" "${rom_prefix}_rmx3081.sh" "${rom_label} - Realme 8 Pro (RMX3081)"; return ;;
            4) run_build_with_feedback "$branch" "${rom_prefix}_all.sh"     "${rom_label} - All Devices Batch"; return ;;
            0|q|Q) return ;;
            *) echo -e "${RED}Invalid choice.${NC}"; sleep 1 ;;
        esac
    done
}

# Submenu: select unified sm7125, device-specific, or all-devices batch for Recovery
function menu_select_recovery_device() {
    local script_name="$1"
    local rec_label="$2"

    while true; do
        clear 2>/dev/null || true
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "${BOLD}  ${rec_label} - Select Target Device${NC}"
        echo -e "${CYAN}=================================================================${NC}"
        echo -e "   ${YELLOW}1)${NC} Unified sm7125 (RMX2061 / RMX2170 / RMX3081)"
        echo -e "   ${YELLOW}2)${NC} Realme 7 Pro (RMX2170)"
        echo -e "   ${YELLOW}3)${NC} Realme 6 Pro (RMX2061)"
        echo -e "   ${YELLOW}4)${NC} Realme 8 Pro (RMX3081)"
        echo -e "   ${YELLOW}5)${NC} All Devices Batch (RMX2170 + RMX2061 + RMX3081)"
        echo ""
        echo -e "   ${CYAN}0)${NC} Back to Main Menu"
        echo -e "${CYAN}=================================================================${NC}"
        read -rp " Select target [0-5]: " dev_choice

        case "$dev_choice" in
            1) DEVICE_OPT="sm7125";  run_build_with_feedback "recovery" "$script_name" "${rec_label} (sm7125 Unified)"; return ;;
            2) DEVICE_OPT="RMX2170"; run_build_with_feedback "recovery" "$script_name" "${rec_label} - Realme 7 Pro (RMX2170)"; return ;;
            3) DEVICE_OPT="RMX2061"; run_build_with_feedback "recovery" "$script_name" "${rec_label} - Realme 6 Pro (RMX2061)"; return ;;
            4) DEVICE_OPT="RMX3081"; run_build_with_feedback "recovery" "$script_name" "${rec_label} - Realme 8 Pro (RMX3081)"; return ;;
            5) DEVICE_OPT="all";     run_build_with_feedback "recovery" "$script_name" "${rec_label} - All Devices Batch"; return ;;
            0|q|Q) return ;;
            *) echo -e "${RED}Invalid choice.${NC}"; sleep 1 ;;
        esac
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
# Clear stale mouse reporting before drawing the first menu. This only affects
# the host terminal; an enclosing tmux keeps its own mouse-wheel setting.
restore_local_terminal
# Never leave the host terminal stuck in mouse-reporting or alt-screen mode.
trap 'restore_local_terminal' EXIT
while true; do
    # Re-assert it on every redraw in case a step re-enabled mouse reporting.
    disable_mouse_reporting
    clear_screen
    echo -e "${CYAN}=================================================================${NC}"
    echo -e "${BOLD}              GCP BUILD & DOWNLOAD MANAGER                       ${NC}"
    echo -e "${CYAN}=================================================================${NC}"
    echo -e " ${BOLD}[ANDROID 16 ROM]${NC}"
    echo -e "   ${GREEN}1)${NC} Evolution X 16"
    echo -e "   ${GREEN}2)${NC} LineageOS 16"
    echo -e "   ${GREEN}3)${NC} Lunaris AOSP 16"
    echo ""
    echo -e " ${BOLD}[ANDROID 17 ROM]${NC}"
    echo -e "   ${CYAN}4)${NC} Evolution X 17"
    echo -e "   ${CYAN}5)${NC} LineageOS 17"
    echo ""
    echo -e " ${BOLD}[RECOVERY]${NC}"
    echo -e "   ${YELLOW}6)${NC} OrangeFox Recovery"
    echo -e "   ${YELLOW}7)${NC} TWRP Recovery"
    echo -e "   ${YELLOW}8)${NC} All Recoveries Batch (OFRP + TWRP)"
    echo ""
    echo -e " ${BOLD}[DOWNLOADS TO MAC]${NC}"
    echo -e "   ${BLUE}9)${NC} Download ROM (.zip)"
    echo -e "  ${BLUE}10)${NC} Download Recovery (.img/.zip)"
    echo -e "  ${BLUE}11)${NC} ${CYAN}Download Build Log (.log)${NC}"
    echo ""
    echo -e " ${BOLD}[VM & BUILD CONTROLS]${NC}"
    echo -e "  ${BOLD}12)${NC} ${GREEN}Live Build Progress (tmux)${NC}"
    echo -e "  ${BOLD}13)${NC} ${CYAN}Build Status & Error Logs${NC}"
    echo -e "  ${BOLD}14)${NC} ${GREEN}Live System Monitor (htop/top/net)${NC}"
    echo -e "  ${BOLD}15)${NC} ${RED}Stop Active Build${NC}"
    echo -e "  ${BOLD}16)${NC} VM Status & Info"
    echo -e "  ${BOLD}17)${NC} Start VM"
    echo -e "  ${BOLD}18)${NC} Stop VM"
    echo -e "  ${BOLD}19)${NC} Disk Manager"
    echo -e "  ${BOLD}20)${NC} Swap Manager"
    echo -e "  ${BOLD}21)${NC} ${CYAN}Machine Type Switcher${NC}"
    echo -e "  ${BOLD}22)${NC} SSH Console"
    echo -e "  ${BOLD}23)${NC} Setup & Tools"
    echo -e "  ${BOLD}24)${NC} ${CYAN}Upload Links (Gofile / SF - Works when VM stopped)${NC}"
    echo ""
    echo -e "   ${RED}q)${NC} Quit"
    echo -e "${CYAN}=================================================================${NC}"
    read -rp " Select option [1-24 or q]: " choice

    case "$choice" in
        1) menu_select_rom_device "aosp-16" "evolution" "Evolution X 16" ;;
        2) menu_select_rom_device "aosp-16" "lineage"   "LineageOS 16" ;;
        3) menu_select_rom_device "aosp-16" "lunaris"   "Lunaris AOSP 16" ;;

        4) menu_select_rom_device "aosp-17" "evolution" "Evolution X 17" ;;
        5) menu_select_rom_device "aosp-17" "lineage"   "LineageOS 17" ;;

        6) menu_select_recovery_device "ofrp_sm7125.sh"   "OrangeFox Recovery" ;;
        7) menu_select_recovery_device "twrp_sm7125.sh"   "TWRP Recovery" ;;
        8) menu_select_recovery_device "recovery_all.sh"  "All Recoveries Batch (OFRP + TWRP)" ;;

        9) download_artifacts "rom" ;;
        10) download_artifacts "recovery" ;;
        11) download_artifacts "log" ;;

        12)
            echo ""
            if ! require_vm_running "attach to live tmux session (no build is active)"; then
                continue
            fi
            echo -e "${GREEN}>>> Connecting to live build session in tmux... (Detach: Ctrl+B then D)${NC}\n"
            attach_tmux_session
            ;;
        13)
            view_build_logs
            ;;
        14)
            echo ""
            if ! require_vm_running "monitor processes (no system is active)"; then
                continue
            fi
            monitor_processes
            ;;
        15)
            stop_build
            ;;
        16)
            clear 2>/dev/null || true
            echo -e "${CYAN}=================================================================${NC}"
            echo -e "${BOLD}                     VM STATUS & INFO                            ${NC}"
            echo -e "${CYAN}=================================================================${NC}"
            local gcp_info_tmp
            gcp_info_tmp=$(mktemp /tmp/gcp_info.XXXXXX)
            run_step "Querying GCP instance status and IP" \
                "gcloud compute instances describe '$INSTANCE' --zone='$ZONE' --project='$PROJECT' --format='table(name,status,networkInterfaces[0].accessConfigs[0].natIP:label=EXTERNAL_IP,machineType.basename():label=MACHINE,zone.basename():label=ZONE)' > '$gcp_info_tmp'"
            echo ""
            cat "$gcp_info_tmp" 2>/dev/null || true
            rm -f "$gcp_info_tmp"
            show_info
            read -rp "Press Enter to return to menu..."
            ;;
        17)
            start_vm
            ;;
        18)
            stop_vm
            ;;
        19)
            menu_manage_disk
            ;;
        20)
            menu_manage_swap
            ;;
        21)
            menu_manage_machine_type
            ;;
        22)
            echo ""
            if ! require_vm_running "open SSH console"; then
                continue
            fi
            echo -e "\n${GREEN}>>> Connecting to VM console... (Type 'exit' to return to menu)${NC}\n"
            vm_exec_tty "bash -l"
            ;;
        23)
            menu_tools
            ;;
        24)
            view_upload_links
            ;;
        q|Q|exit)
            echo "Goodbye!"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice.${NC}"
            sleep 1
            ;;
    esac
done
fi
