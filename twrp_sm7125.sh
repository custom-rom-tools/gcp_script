#!/bin/bash
# =============================================================================
# TeamWin Recovery Project (TWRP) - Realme Atoll (sm7125)
# Unified: Realme 6 Pro (RMX2061), Realme 7 Pro (RMX2170), Realme 8 Pro (RMX3081)
#
# Examples:
#   Standard Build:
#     gcp-menu run -- "curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/twrp_sm7125.sh | bash"
#   With SourceForge Upload (Devspace Key):
#     gcp-menu run -- "export SF_UPLOAD=true; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/twrp_sm7125.sh | bash"
#   With SourceForge Upload (Base64 SF_KEY):
#     gcp-menu run -- "export SF_KEY='<b64_key>'; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/twrp_sm7125.sh | bash"
# =============================================================================

set -e

# =============================================================================
# CONFIGURATION
# =============================================================================

DEVICE="${1:-${DEVICE:-sm7125}}"
RECOVERY_NAME="TWRP"
BASE_MANIFEST_URL="https://github.com/minimal-manifest-twrp/platform_manifest_twrp_aosp.git"
BASE_MANIFEST_BRANCH="twrp-12.1"
DEVICE_MANIFEST_URL="https://github.com/realme-atoll/recovery-manifest"
DEVICE_MANIFEST_BRANCH="twrp"

BUILD_TARGET="recoveryimage"
BUILD_VARIANT="${BUILD_VARIANT:-eng}"
LUNCH_TARGET="twrp_${DEVICE}-${BUILD_VARIANT}"
WORK_DIR="/mnt/recovery/twrp"

UPLOAD_SUBDIR="${DEVICE^^}/Recovery/TWRP"

SCRIPT_BRANCH="${SCRIPT_BRANCH:-recovery}"
UPLOAD_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/upload.py"

# =============================================================================
# SCRIPT LOGIC
# =============================================================================

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
echo "============================================================"
echo " Building $RECOVERY_NAME for $DEVICE"
echo " Working Directory: $(pwd)"
echo "============================================================"

# Preserve out/ for incremental builds; only refresh device manifest & generated BoardConfig stubs
rm -rf .repo/local_manifests device/realme/sm7125 device/realme/RMX*

# 1. Initialize repo
echo ""
echo "=============================="
echo " [1/6] Initializing $RECOVERY_NAME base repo ($BASE_MANIFEST_BRANCH)..."
echo "=============================="
repo init -u "$BASE_MANIFEST_URL" -b "$BASE_MANIFEST_BRANCH"

# 2. Setup device manifest
echo ""
echo "=============================="
echo " [2/6] Setting up device manifest ($DEVICE_MANIFEST_BRANCH)..."
echo "=============================="
git clone --progress -b "$DEVICE_MANIFEST_BRANCH" "$DEVICE_MANIFEST_URL" .repo/local_manifests

# 3. Sync source via crave resync
echo ""
echo "=============================="
echo " [3/6] Syncing source via crave resync..."
echo "=============================="
export PYTHONUNBUFFERED=1
SYNC_OK=0
for sync_jobs in $(nproc --all) 8 4 2; do
    if [ -t 2 ]; then
        repo sync -c -j"${sync_jobs}" --force-sync --no-clone-bundle --no-tags && { SYNC_OK=1; break; }
    elif command -v script >/dev/null 2>&1; then
        script -qefc "repo sync -c -j${sync_jobs} --force-sync --no-clone-bundle --no-tags" /dev/null && { SYNC_OK=1; break; }
    else
        repo sync -c -j"${sync_jobs}" --force-sync --no-clone-bundle --no-tags && { SYNC_OK=1; break; }
    fi
    echo ">>> [WARN] repo sync failed (possible HTTP 429 rate limit); retrying in 60s..."
    sleep 60
done
[ "$SYNC_OK" -eq 1 ]

# Qualcomm common dependencies
if [ ! -d "device/qcom/common" ]; then
    git clone --depth=1 https://github.com/TeamWin/android_device_qcom_common.git -b android-12.1 device/qcom/common
fi
if [ ! -d "device/qcom/twrp-common" ]; then
    git clone --depth=1 https://github.com/TeamWin/android_device_qcom_twrp-common.git -b android-12.1 device/qcom/twrp-common
fi

# 4. Setup build environment and lunch
# 4. Setup build environment, lunch & compile (supports single device or DEVICE=all)
echo ""
echo "=============================="
echo " [4/6] Setting up environment..."
echo "=============================="
export ALLOW_MISSING_DEPENDENCIES=true
export TW_DEVICE_VERSION="12.1"
export LC_ALL="C.UTF-8"
source build/envsetup.sh
export USE_CCACHE=0
export CCACHE_DISABLE=1
unset CCACHE_EXEC CCACHE_DIR
if [ -f "device/realme/${DEVICE}/vendorsetup.sh" ]; then
    source "device/realme/${DEVICE}/vendorsetup.sh"
elif [ -f "device/realme/sm7125/vendorsetup.sh" ]; then
    source "device/realme/sm7125/vendorsetup.sh"
fi

if [ "${DEVICE,,}" = "all" ]; then
    TARGET_DEVICES=("RMX2170" "RMX2061" "RMX3081")
else
    TARGET_DEVICES=("$DEVICE")
fi

SUCCESS_DEVS=()
FAILED_DEVS=()

set +e
for dev in "${TARGET_DEVICES[@]}"; do
    if [ "$dev" != "sm7125" ] && [ -f "device/realme/sm7125/BoardConfig.mk" ] && [ ! -f "device/realme/${dev}/BoardConfig.mk" ]; then
        mkdir -p "device/realme/${dev}"
        printf "DEVICE_PATH := device/realme/sm7125\ninclude device/realme/sm7125/BoardConfig.mk\n" > "device/realme/${dev}/BoardConfig.mk"
    fi
    dev_lunch="twrp_${dev}-${BUILD_VARIANT}"
    echo ""
    echo "=============================="
    echo " [5/6] Lunching $dev_lunch & compiling $RECOVERY_NAME ($BUILD_TARGET)..."
    echo "=============================="
    local_out="out/target/product/${dev}"
    rm -f "$local_out/recovery.img" "$local_out"/TWRP*.img "$local_out"/TWRP*.zip

    if ! (lunch "$dev_lunch" >/dev/null 2>&1; m -j$(nproc --all) "$BUILD_TARGET"); then
        echo ">>> [FAILED] $RECOVERY_NAME compilation failed for $dev! Skipping upload."
        FAILED_DEVS+=("$dev")
        continue
    fi

    if [ ! -s "$local_out/recovery.img" ] && [ -z "$(find "$local_out" -maxdepth 1 \( -name 'TWRP*.img' -o -name 'TWRP*.zip' \) -size +0c 2>/dev/null | head -n 1)" ]; then
        echo ">>> [FAILED] No valid recovery image/zip produced for $dev! Skipping upload."
        FAILED_DEVS+=("$dev")
        continue
    fi

    echo ""
    echo "=============================="
    echo " [6/6] Uploading $RECOVERY_NAME artifacts for $dev..."
    echo "=============================="
    BUILD_DATE=$(date +%Y%m%d)

    if [ -s "$local_out/recovery.img" ]; then
        mv -vf "$local_out/recovery.img" "$local_out/TWRP-12.1-${BUILD_DATE}-Unofficial-${dev}.img"
    fi

    if [ -s "$local_out/recovery.zip" ]; then
        mv -vf "$local_out/recovery.zip" "$local_out/TWRP-12.1-${BUILD_DATE}-Unofficial-${dev}.zip"
    fi

    for z in "$local_out"/TWRP*.zip "$local_out"/twrp*.zip; do
        if [ -s "$z" ] && [[ "$z" != *"$BUILD_DATE"* ]]; then
            mv -vf "$z" "$local_out/TWRP-12.1-${BUILD_DATE}-Unofficial-${dev}.zip"
            break
        fi
    done

    curl -sLf "$UPLOAD_SCRIPT_URL" | python3 - "${dev^^}/Recovery/TWRP" "${dev}" "recovery"
    SUCCESS_DEVS+=("$dev")
done
set -e

echo ""
echo "============================================================"
echo " $RECOVERY_NAME Build Summary"
[ ${#SUCCESS_DEVS[@]} -gt 0 ] && echo " Succeeded: ${SUCCESS_DEVS[*]}"
[ ${#FAILED_DEVS[@]} -gt 0 ] && echo " Failed:    ${FAILED_DEVS[*]}"
echo "============================================================"

if [ ${#FAILED_DEVS[@]} -gt 0 ]; then
    exit 1
fi

# Auto-shutdown if requested
if [ "${AUTO_SHUTDOWN:-true}" = "true" ]; then
    echo "Auto-shutdown enabled: Powering down VM in 10 seconds to stop billing..."
    sleep 10
    sudo poweroff
fi
