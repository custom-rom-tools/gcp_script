#!/bin/bash
# =============================================================================
# Multi-Recovery Batch Build - Realme Atoll (sm7125)
# Recoveries: OrangeFox Recovery Project & TeamWin Recovery Project (TWRP)
#
# Examples:
#   Standard Build:
#     gcp-menu run -- "curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/recovery_all.sh | bash"
#   With SourceForge Upload (Devspace Key):
#     gcp-menu run -- "export SF_UPLOAD=true; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/recovery_all.sh | bash"
#   With SourceForge Upload (Base64 SF_KEY):
#     gcp-menu run -- "export SF_KEY='<b64_key>'; curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/<branch>/recovery_all.sh | bash"
# =============================================================================

# Note: set -e is deliberately omitted so a failure on one recovery does not abort the other

# =============================================================================
# CONFIGURATION
# =============================================================================

DEVICE="${1:-${DEVICE:-sm7125}}"
BUILD_TARGET="recoveryimage"
BUILD_VARIANT="${BUILD_VARIANT:-eng}"
OFRP_RELEASE="${OFRP_RELEASE:-bp2a}"
OFRP_LUNCH_TARGET="twrp_${DEVICE}-${OFRP_RELEASE}-${BUILD_VARIANT}"
LUNCH_TARGET="twrp_${DEVICE}-${BUILD_VARIANT}"

# Base Manifests
OFRP_BASE_MANIFEST_URL="https://gitlab.com/OrangeFox/Manifest.git"
OFRP_BASE_MANIFEST_BRANCH="fox_16.0"

TWRP_BASE_MANIFEST_URL="https://github.com/minimal-manifest-twrp/platform_manifest_twrp_aosp.git"
TWRP_BASE_MANIFEST_BRANCH="twrp-12.1"

# Shared Recovery Manifest (local_manifests containing device trees)
RECOVERY_MANIFEST_URL="https://github.com/realme-atoll/recovery-manifest"
OFRP_DEVICE_BRANCH="ofrp"
TWRP_DEVICE_BRANCH="twrp"

# Workspace & Cloud Upload
WORK_DIR_OFRP="/mnt/recovery/ofrp"
WORK_DIR_TWRP="/mnt/recovery/twrp"
UPLOAD_SUBDIR_BASE="${DEVICE^^}/Recovery"

# Helper Scripts URLs
SCRIPT_BRANCH="${SCRIPT_BRANCH:-recovery}"
UPLOAD_SCRIPT_URL="https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/upload.py"

# =============================================================================
# SCRIPT LOGIC
# =============================================================================

SUCCESS_BUILDS=()
FAILED_BUILDS=()

echo "============================================================"
echo " Starting Multi-Recovery Batch Build for Realme Atoll ($DEVICE)"
echo " Recoveries: OrangeFox & TWRP"
echo " Workspace (OFRP): $WORK_DIR_OFRP"
echo " Workspace (TWRP): $WORK_DIR_TWRP"
echo "============================================================"

# Helper: Ensure Qualcomm recovery dependencies are present
ensure_qcom_dependencies() {
    echo ">> Checking Qualcomm common recovery dependencies..."
    if [ ! -d "device/qcom/common" ]; then
        echo ">> Cloning device/qcom/common (android-12.1)..."
        git clone --depth=1 https://github.com/TeamWin/android_device_qcom_common.git -b android-12.1 device/qcom/common
    fi
    if [ ! -d "device/qcom/twrp-common" ]; then
        echo ">> Cloning device/qcom/twrp-common (android-12.1)..."
        git clone --depth=1 https://github.com/TeamWin/android_device_qcom_twrp-common.git -b android-12.1 device/qcom/twrp-common
    fi
}

# -----------------------------------------------------------------------------
# 1. OrangeFox Recovery Build (Step 1/2)
# -----------------------------------------------------------------------------
build_orangefox() {
    echo ""
    echo "============================================================"
    echo " [Step 1/2] [Recovery 1/2: OrangeFox] Starting Build (${DEVICE})..."
    echo "============================================================"
    mkdir -p "$WORK_DIR_OFRP"
    cd "$WORK_DIR_OFRP"

    # Preserve out/ for incremental builds; only refresh device manifest & generated BoardConfig stubs
    rm -rf .repo/local_manifests device/realme/sm7125 device/realme/RMX* .repo/projects/device/realme/sm7125.git .repo/projects/device/realme/"${DEVICE}".git

    # Substep 1/5: Initialize official OrangeFox manifest
    echo ""
    echo "------------------------------------------------------------"
    echo " [OrangeFox] Step 1/5: Initializing OrangeFox manifest ($OFRP_BASE_MANIFEST_BRANCH)..."
    echo "------------------------------------------------------------"
    repo init -u "$OFRP_BASE_MANIFEST_URL" -b "$OFRP_BASE_MANIFEST_BRANCH"

    # Substep 2/5: Setup device manifest and sync source
    echo ""
    echo "------------------------------------------------------------"
    echo " [OrangeFox] Step 2/5: Setting up device manifest & syncing source..."
    echo "------------------------------------------------------------"
    if ! git clone --progress -b "$OFRP_DEVICE_BRANCH" "$RECOVERY_MANIFEST_URL" .repo/local_manifests; then
        echo ">>> [FAILED] OrangeFox local manifest clone failed!"
        FAILED_BUILDS+=("OrangeFox [manifest clone failed]")
        return 1
    fi

    echo ">> Running repo sync for OrangeFox source tree (progress shown below)..."
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
    ensure_qcom_dependencies

    # Substep 3/5: Setup environment & lunch
    echo ""
    echo "------------------------------------------------------------"
    echo " [OrangeFox] Step 3/5: Setting up environment & lunching $OFRP_LUNCH_TARGET..."
    echo "------------------------------------------------------------"
    export ALLOW_MISSING_DEPENDENCIES=true
    export FOX_USE_TWRP_RECOVERY_IMAGE_BUILDER=1
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

    local target_devs=("$DEVICE")
    [ "${DEVICE,,}" = "all" ] && target_devs=("RMX2170" "RMX2061" "RMX3081")

    local any_fail=0
    for dev in "${target_devs[@]}"; do
        if [ "$dev" != "sm7125" ] && [ -f "device/realme/sm7125/BoardConfig.mk" ] && [ ! -f "device/realme/${dev}/BoardConfig.mk" ]; then
            mkdir -p "device/realme/${dev}"
            printf "DEVICE_PATH := device/realme/sm7125\ninclude device/realme/sm7125/BoardConfig.mk\n" > "device/realme/${dev}/BoardConfig.mk"
        fi
        local dev_lunch="twrp_${dev}-${OFRP_RELEASE}-${BUILD_VARIANT}"
        local out_dir="${WORK_DIR_OFRP}/out/target/product/${dev}"
        rm -f "$out_dir/recovery.img" "$out_dir"/OrangeFox*.img "$out_dir"/OrangeFox*.zip

        if lunch "$dev_lunch" || lunch "twrp_${dev}-trunk_staging-${BUILD_VARIANT}" || lunch "twrp_${dev}-${BUILD_VARIANT}"; then
            # Substep 4/5: Compile recovery
            echo ""
            echo "------------------------------------------------------------"
            echo " [OrangeFox] Step 4/5: Compiling recovery image ($dev - $BUILD_TARGET)..."
            echo "------------------------------------------------------------"
            if m -j$(nproc --all) "$BUILD_TARGET"; then
                if [ ! -s "$out_dir/recovery.img" ] && [ -z "$(find "$out_dir" -maxdepth 1 \( -name 'OrangeFox*.img' -o -name 'OrangeFox*.zip' \) -size +0c 2>/dev/null | head -n 1)" ]; then
                    echo ">>> [FAILED] No valid OrangeFox recovery image/zip produced for $dev! Skipping upload."
                    any_fail=1
                    continue
                fi
                echo ">>> [SUCCESS] OrangeFox Recovery build succeeded for $dev!"

                # Substep 5/5: Upload official artifacts (check for existing branded names first)
                echo ""
                echo "------------------------------------------------------------"
                echo " [OrangeFox] Step 5/5: Uploading recovery artifacts for $dev..."
                echo "------------------------------------------------------------"
                local BUILD_DATE=$(date +%Y%m%d)

                if [ -s "$out_dir/recovery.img" ]; then
                    local existing_img=$(find "$out_dir" -maxdepth 1 -name "OrangeFox*.img" -size +0c | head -n 1)
                    if [ -z "$existing_img" ]; then
                        mv -v "$out_dir/recovery.img" "$out_dir/OrangeFox-R12.0-${BUILD_DATE}-Unofficial-${dev}.img"
                    fi
                fi

                if [ -s "$out_dir/recovery.zip" ]; then
                    local existing_zip=$(find "$out_dir" -maxdepth 1 -name "OrangeFox*.zip" -size +0c | head -n 1)
                    if [ -z "$existing_zip" ]; then
                        mv -v "$out_dir/recovery.zip" "$out_dir/OrangeFox-R12.0-${BUILD_DATE}-Unofficial-${dev}.zip"
                    fi
                fi

                local UPLOAD_SUBDIR="${dev^^}/Recovery/OrangeFox"
                curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' "${UPLOAD_SCRIPT_URL}?t=$(date +%s)" | python3 - "${UPLOAD_SUBDIR}" "${dev}" "recovery"
            else
                echo ">>> [FAILED] OrangeFox Recovery compilation failed for $dev! Skipping upload."
                any_fail=1
            fi
        else
            echo ">>> [FAILED] OrangeFox lunch failed for $dev!"
            any_fail=1
        fi
    done
    return $any_fail
}

# -----------------------------------------------------------------------------
# 2. TWRP Recovery Build (Step 2/2)
# -----------------------------------------------------------------------------
build_twrp() {
    echo ""
    echo "============================================================"
    echo " [Step 2/2] [Recovery 2/2: TWRP] Starting Build (${DEVICE})..."
    echo "============================================================"
    mkdir -p "$WORK_DIR_TWRP"
    cd "$WORK_DIR_TWRP"

    # Preserve out/ for incremental builds; only refresh device manifest & generated BoardConfig stubs
    rm -rf .repo/local_manifests device/realme/sm7125 device/realme/RMX* .repo/projects/device/realme/sm7125.git .repo/projects/device/realme/"${DEVICE}".git

    # Substep 1/5: Initialize TWRP minimal manifest
    echo ""
    echo "------------------------------------------------------------"
    echo " [TWRP] Step 1/5: Initializing TWRP base repo ($TWRP_BASE_MANIFEST_BRANCH)..."
    echo "------------------------------------------------------------"
    repo init -u "$TWRP_BASE_MANIFEST_URL" -b "$TWRP_BASE_MANIFEST_BRANCH"

    # Substep 2/5: Setup device manifest and sync source
    echo ""
    echo "------------------------------------------------------------"
    echo " [TWRP] Step 2/5: Setting up device manifest & syncing source..."
    echo "------------------------------------------------------------"
    if ! git clone --progress -b "$TWRP_DEVICE_BRANCH" "$RECOVERY_MANIFEST_URL" .repo/local_manifests; then
        echo ">>> [FAILED] TWRP local manifest clone failed!"
        FAILED_BUILDS+=("TWRP [manifest clone failed]")
        return 1
    fi

    echo ">> Running repo sync for TWRP source tree (progress shown below)..."
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
    ensure_qcom_dependencies

    # Substep 3/5: Setup environment & lunch
    echo ""
    echo "------------------------------------------------------------"
    echo " [TWRP] Step 3/5: Setting up environment & lunching $LUNCH_TARGET..."
    echo "------------------------------------------------------------"
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

    local target_devs=("$DEVICE")
    [ "${DEVICE,,}" = "all" ] && target_devs=("RMX2170" "RMX2061" "RMX3081")

    local any_fail=0
    for dev in "${target_devs[@]}"; do
        if [ "$dev" != "sm7125" ] && [ -f "device/realme/sm7125/BoardConfig.mk" ] && [ ! -f "device/realme/${dev}/BoardConfig.mk" ]; then
            mkdir -p "device/realme/${dev}"
            printf "DEVICE_PATH := device/realme/sm7125\ninclude device/realme/sm7125/BoardConfig.mk\n" > "device/realme/${dev}/BoardConfig.mk"
        fi
        local dev_lunch="twrp_${dev}-${BUILD_VARIANT}"
        local out_dir="${WORK_DIR_TWRP}/out/target/product/${dev}"
        rm -f "$out_dir/recovery.img" "$out_dir"/TWRP*.img "$out_dir"/TWRP*.zip

        if lunch "$dev_lunch"; then
            # Substep 4/5: Compile recovery
            echo ""
            echo "------------------------------------------------------------"
            echo " [TWRP] Step 4/5: Compiling recovery image ($dev - $BUILD_TARGET)..."
            echo "------------------------------------------------------------"
            if m -j$(nproc --all) "$BUILD_TARGET"; then
                if [ ! -s "$out_dir/recovery.img" ] && [ -z "$(find "$out_dir" -maxdepth 1 \( -name 'TWRP*.img' -o -name 'TWRP*.zip' \) -size +0c 2>/dev/null | head -n 1)" ]; then
                    echo ">>> [FAILED] No valid TWRP recovery image/zip produced for $dev! Skipping upload."
                    any_fail=1
                    continue
                fi
                echo ">>> [SUCCESS] TWRP Recovery build succeeded for $dev!"

                # Substep 5/5: Upload official artifacts (check for existing branded names first)
                echo ""
                echo "------------------------------------------------------------"
                echo " [TWRP] Step 5/5: Uploading recovery artifacts for $dev..."
                echo "------------------------------------------------------------"
                local BUILD_DATE=$(date +%Y%m%d)

                if [ -s "$out_dir/recovery.img" ]; then
                    local existing_img=$(find "$out_dir" -maxdepth 1 -name "TWRP*.img" -size +0c | head -n 1)
                    if [ -z "$existing_img" ]; then
                        mv -v "$out_dir/recovery.img" "$out_dir/TWRP-3.7.1_${BUILD_DATE}-Unofficial-${dev}.img"
                    fi
                fi

                if [ -s "$out_dir/recovery.zip" ]; then
                    local existing_zip=$(find "$out_dir" -maxdepth 1 -name "TWRP*.zip" -size +0c | head -n 1)
                    if [ -z "$existing_zip" ]; then
                        mv -v "$out_dir/recovery.zip" "$out_dir/TWRP-3.7.1_${BUILD_DATE}-Unofficial-${dev}.zip"
                    fi
                fi

                local UPLOAD_SUBDIR="${dev^^}/Recovery/TWRP"
                curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' "${UPLOAD_SCRIPT_URL}?t=$(date +%s)" | python3 - "${UPLOAD_SUBDIR}" "${dev}" "recovery"
            else
                echo ">>> [FAILED] TWRP Recovery compilation failed for $dev! Skipping upload."
                any_fail=1
            fi
        else
            echo ">>> [FAILED] TWRP lunch failed for $dev!"
            any_fail=1
        fi
    done
    return $any_fail
}

# Execute both recovery builds in isolated subshells so TOP/OUT_DIR never leak between trees
if ( unset TOP ANDROID_BUILD_TOP ANDROID_PRODUCT_OUT ANDROID_HOST_OUT OUT_DIR OUT_DIR_COMMON_BASE TARGET_PRODUCT TARGET_BUILD_VARIANT TARGET_RELEASE; build_orangefox ); then
    SUCCESS_BUILDS+=("OrangeFox")
else
    FAILED_BUILDS+=("OrangeFox [failed]")
fi

if ( unset TOP ANDROID_BUILD_TOP ANDROID_PRODUCT_OUT ANDROID_HOST_OUT OUT_DIR OUT_DIR_COMMON_BASE TARGET_PRODUCT TARGET_BUILD_VARIANT TARGET_RELEASE; build_twrp ); then
    SUCCESS_BUILDS+=("TWRP")
else
    FAILED_BUILDS+=("TWRP [failed]")
fi

# Final Summary Report
echo ""
echo "============================================================"
echo "              RECOVERY BUILD SUMMARY REPORT                 "
echo "============================================================"
[ ${#SUCCESS_BUILDS[@]} -gt 0 ] && echo " Successful Builds: ${SUCCESS_BUILDS[*]}"
[ ${#FAILED_BUILDS[@]} -gt 0 ] && echo " Failed Builds:     ${FAILED_BUILDS[*]}"
echo "============================================================"

if [ ${#FAILED_BUILDS[@]} -gt 0 ]; then
    echo -e "\n>>> Multi-Recovery batch build completed with failures. Session will remain open."
    read -rp "Press [Enter] to exit..." || true
    exit 1
fi

echo -e "\n>>> Multi-Recovery batch build completed successfully. Session will remain open."
read -rp "Press [Enter] to exit..." || true
exit 0
