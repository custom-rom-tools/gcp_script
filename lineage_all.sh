#!/bin/bash
# =============================================================================
# LineageOS (Android 17) - Multi-Device Batch Build
# Devices: Realme 7 Pro (RMX2170), Realme 6 Pro (RMX2061), Realme 8 Pro (RMX3081)
#
# Examples:
#   Standard Build:
#     gcp-menu run -- "curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/lineage_all.sh | bash"
#   With SourceForge Upload (Devspace Key):
#     gcp-menu run -- "export SF_UPLOAD=true; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/lineage_all.sh | bash"
#   With SourceForge Upload (Base64 SF_KEY):
#     gcp-menu run -- "export SF_KEY='<b64_key>'; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/lineage_all.sh | bash"
# =============================================================================

# Note: set -e is deliberately omitted so a failure on one device does not abort remaining devices

# =============================================================================
# CONFIGURATION - MODIFY ONLY THIS SECTION
# =============================================================================

# ROM Source Repository
ROM_NAME="LineageOS"
ROM_MANIFEST_URL="https://github.com/LineageOS/android.git"
ROM_MANIFEST_BRANCH="lineage-24.0"

# Device Local Manifests (Configured independently per device)
RMX2170_MANIFEST_URL="https://github.com/realme-atoll/RMX2170-manifest"
RMX2170_MANIFEST_BRANCH="aosp-17"

RMX2061_MANIFEST_URL="https://github.com/realme-atoll/RMX2061-manifest"
RMX2061_MANIFEST_BRANCH="aosp-17"

RMX3081_MANIFEST_URL="https://github.com/realme-atoll/RMX3081-manifest"
RMX3081_MANIFEST_BRANCH="aosp-17"

# Build Settings
BUILD_VARIANT="${1:-${BUILD_VARIANT:-userdebug}}"
BUILD_TARGET="bacon"
WORK_DIR="/mnt/a17/los"

# Cloud Upload Destination (Destination path: <DEVICE>/<UPLOAD_SUBDIR_SUFFIX>)
UPLOAD_SUBDIR_SUFFIX="LineageOS/17"

# Helper Scripts URLs
SCRIPT_BRANCH="${SCRIPT_BRANCH:-aosp-17}"
CLEAN_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/${SCRIPT_BRANCH}/clean.py"
PATCH_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/${SCRIPT_BRANCH}/patch.py"
UPLOAD_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/upload.py"

# =============================================================================
# SCRIPT LOGIC - DO NOT MODIFY BELOW THIS LINE
# =============================================================================

SUCCESS_BUILDS=()
FAILED_BUILDS=()

# Setup workspace
if [ -d "$WORK_DIR" ]; then
    cd "$WORK_DIR"
else
    WORK_DIR="$(pwd)"
fi

echo "============================================================"
echo " Starting $ROM_NAME Batch Build ($BUILD_VARIANT)"
echo " Devices: RMX2170, RMX2061, RMX3081"
echo " Workspace: $WORK_DIR"
echo "============================================================"

# Step 1/4: Initialize ROM repo (shared base)
echo ""
echo "============================================================"
echo " [Step 1/4] Initializing ROM Source ($ROM_NAME - $ROM_MANIFEST_BRANCH)..."
echo "============================================================"
repo init -u "$ROM_MANIFEST_URL" -b "$ROM_MANIFEST_BRANCH" --git-lfs

# Helper: Build single device
build_device() {
    local DEVICE="$1"
    local manifest_url="$2"
    local manifest_branch="$3"
    local DEV_IDX="$4"
    local TOTAL_DEVS="$5"
    local OVERALL_STEP="$6"
    local TOTAL_STEPS=4
    local UPLOAD_SUBDIR="${DEVICE}/${UPLOAD_SUBDIR_SUFFIX}"

    echo ""
    echo "============================================================"
    echo " [Step ${OVERALL_STEP}/${TOTAL_STEPS}] [Device ${DEV_IDX}/${TOTAL_DEVS}: ${DEVICE}] Starting Build (${BUILD_VARIANT})..."
    echo "============================================================"

    cd "$WORK_DIR"

    # Clean previous device-specific trees to prevent module collisions
    rm -rf device/realme/RMX* vendor/realme/RMX*
    
    # Substep 1/5: Setup device manifest
    echo ""
    echo "------------------------------------------------------------"
    echo " [Device ${DEV_IDX}/${TOTAL_DEVS}: ${DEVICE}] Step 1/5: Setting up device manifest..."
    echo "------------------------------------------------------------"
    rm -rf .repo/local_manifests
    if git clone -b "$manifest_branch" "$manifest_url" .repo/local_manifests; then
        # Substep 2/5: Clean and sync source
        echo ""
        echo "------------------------------------------------------------"
        echo " [Device ${DEV_IDX}/${TOTAL_DEVS}: ${DEVICE}] Step 2/5: Syncing source and applying patches..."
        echo "------------------------------------------------------------"
        curl -sLf "$CLEAN_SCRIPT_URL" | python3
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
        curl -sLf "$PATCH_SCRIPT_URL" | python3
        
        # Substep 3/5: Setup build environment and lunch
        echo ""
        echo "------------------------------------------------------------"
        echo " [Device ${DEV_IDX}/${TOTAL_DEVS}: ${DEVICE}] Step 3/5: Lunching lineage_${DEVICE}-${BUILD_VARIANT}..."
        echo "------------------------------------------------------------"

        source build/envsetup.sh
export USE_CCACHE=1
export CCACHE_DIR="$HOME/.ccache"
export CCACHE_EXEC="$(which ccache)"

        if lunch "lineage_${DEVICE}-${BUILD_VARIANT}"; then
            # Ensure clean intermediate product state when switching device targets
            m installclean

            # Substep 4/5: Compile ROM
            echo ""
            echo "------------------------------------------------------------"
            echo " [Device ${DEV_IDX}/${TOTAL_DEVS}: ${DEVICE}] Step 4/5: Compiling $ROM_NAME ($BUILD_TARGET)..."
            echo "------------------------------------------------------------"
            if m -j$(nproc --all) "$BUILD_TARGET"; then
                echo ">>> [SUCCESS] $DEVICE build succeeded!"
                SUCCESS_BUILDS+=("$DEVICE")

                # Substep 5/5: Upload build artifacts
                echo ""
                echo "------------------------------------------------------------"
                echo " [Device ${DEV_IDX}/${TOTAL_DEVS}: ${DEVICE}] Step 5/5: Uploading build artifacts..."
                echo "------------------------------------------------------------"
                curl -sLf "$UPLOAD_SCRIPT_URL" | python3 - "${UPLOAD_SUBDIR}" "${DEVICE}" "rom"
            else
                echo ">>> [FAILED] $DEVICE build compilation failed!"
                FAILED_BUILDS+=("$DEVICE")
            fi
        else
            echo ">>> [FAILED] $DEVICE lunch failed!"
            FAILED_BUILDS+=("$DEVICE [lunch failed]")
        fi
    else
        echo ">>> [FAILED] $DEVICE local manifest clone failed!"
        FAILED_BUILDS+=("$DEVICE [manifest clone failed]")
    fi
}

# Build all devices sequentially (Overall steps 2/4, 3/4, 4/4)
build_device "RMX2170" "$RMX2170_MANIFEST_URL" "$RMX2170_MANIFEST_BRANCH" 1 3 2
build_device "RMX2061" "$RMX2061_MANIFEST_URL" "$RMX2061_MANIFEST_BRANCH" 2 3 3
build_device "RMX3081" "$RMX3081_MANIFEST_URL" "$RMX3081_MANIFEST_BRANCH" 3 3 4

# Final Summary Report
echo ""
echo "============================================================"
echo "                   BUILD SUMMARY REPORT                     "
echo "============================================================"
[ ${#SUCCESS_BUILDS[@]} -gt 0 ] && echo " Successful Builds: ${SUCCESS_BUILDS[*]}"
[ ${#FAILED_BUILDS[@]} -gt 0 ] && echo " Failed Builds:     ${FAILED_BUILDS[*]}"
echo "============================================================"

if [ ${#FAILED_BUILDS[@]} -gt 0 ]; then
    exit 1
fi

# Auto-shutdown if requested (only when all builds succeed)
if [ "${AUTO_SHUTDOWN:-true}" = "true" ]; then
    echo "Auto-shutdown enabled: Powering down VM in 10 seconds to stop billing..."
    sleep 10
    sudo poweroff
fi
exit 0
