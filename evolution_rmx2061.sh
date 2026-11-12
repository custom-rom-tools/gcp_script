#!/bin/bash
# =============================================================================
# Evolution X (Android 17) - Realme 6 Pro (RMX2061)
#
# Examples:
#   Standard Build:
#     gcp-menu run -- "curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-17/evolution_rmx2170.sh | bash"
#   With SourceForge Upload (Devspace Key /tmp/crave_sf):
#     gcp-menu run -- "export SF_UPLOAD=true; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-17/evolution_rmx2170.sh | bash"
#   With SourceForge Upload (Base64 SF_KEY):
#     gcp-menu run -- "export SF_KEY='<b64_key>'; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-17/evolution_rmx2170.sh | bash"
# =============================================================================

set -e

# =============================================================================
# CONFIGURATION - MODIFY ONLY THIS SECTION
# =============================================================================

# Device & ROM Settings
DEVICE="RMX2061"
ROM_NAME="Evolution X"
ROM_MANIFEST_URL="https://github.com/Evolution-X/manifest"
ROM_MANIFEST_BRANCH="cnb"
DEVICE_MANIFEST_URL="https://github.com/realme-atoll/RMX2061-manifest"
DEVICE_MANIFEST_BRANCH="${DEVICE_MANIFEST_BRANCH:-evox-cnb}"

# Build Settings
BUILD_VARIANT="${1:-${BUILD_VARIANT:-userdebug}}"
BUILD_TARGET="evolution"
LUNCH_TARGET="lineage_${DEVICE}-cp2a-${BUILD_VARIANT}"
WORK_DIR="/mnt/a17/evox"

# Cloud Upload Destination
UPLOAD_SUBDIR="${DEVICE}/EvolutionX/17"

# Helper Scripts URLs
SCRIPT_BRANCH="${SCRIPT_BRANCH:-aosp-17}"
CLEAN_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/${SCRIPT_BRANCH}/clean.py"
PATCH_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/${SCRIPT_BRANCH}/patch.py"
UPLOAD_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/upload.py"

# =============================================================================
# SCRIPT LOGIC - DO NOT MODIFY BELOW THIS LINE
# =============================================================================

# Workspace Setup
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
echo "============================================================"
echo " Building $ROM_NAME for $DEVICE ($BUILD_VARIANT)"
echo " Working Directory: $(pwd)"
echo "============================================================"

# 1. Initialize ROM repo
echo ""
echo "=============================="
echo " [1/6] Initializing $ROM_NAME ($ROM_MANIFEST_BRANCH)..."
echo "=============================="
repo init -u "$ROM_MANIFEST_URL" -b "$ROM_MANIFEST_BRANCH" --git-lfs

# 2. Setup device manifest
echo ""
echo "=============================="
echo " [2/6] Setting up device manifest..."
echo "=============================="
rm -rf .repo/local_manifests
git clone -b "$DEVICE_MANIFEST_BRANCH" "$DEVICE_MANIFEST_URL" .repo/local_manifests

# 3. Clean and sync source
echo ""
echo "=============================="
echo " [3/6] Syncing source and applying patches..."
echo "=============================="
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

# 4. Setup build environment and lunch
echo ""
echo "=============================="
echo " [4/6] Setting up environment & lunching $LUNCH_TARGET..."
echo "=============================="

source build/envsetup.sh
export USE_CCACHE=1
export CCACHE_DIR="$HOME/.ccache"
export CCACHE_EXEC="$(which ccache)"
if ! lunch "$LUNCH_TARGET" 2>/dev/null; then
    LUNCH_TARGET="lineage_${DEVICE}-${BUILD_VARIANT}"
    echo "Fallback to lunch target: $LUNCH_TARGET"
    lunch "$LUNCH_TARGET"
fi

# 5. Compile ROM
echo ""
echo "=============================="
echo " [5/6] Building $ROM_NAME ($BUILD_TARGET)..."
echo "=============================="
m -j$(nproc --all) "$BUILD_TARGET"

# 6. Upload build artifacts
echo ""
echo "=============================="
echo " [6/6] Uploading build artifacts..."
echo "=============================="
curl -sLf "$UPLOAD_SCRIPT_URL" | python3 - "${UPLOAD_SUBDIR}" "${DEVICE}" "rom"

echo ""
echo "============================================================"
echo " Build process completed successfully!"
echo "============================================================"

# Auto-shutdown if requested
if [ "${AUTO_SHUTDOWN:-true}" = "true" ]; then
    echo "Auto-shutdown enabled: Powering down VM in 10 seconds to stop billing..."
    sleep 10
    sudo poweroff
fi
