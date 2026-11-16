#!/usr/bin/env python3
"""
Upstream and tree patches for realme-atoll ROM builds.
Can be executed directly via:
    curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-17/patch.py | python3
"""

import os


def ensure_qcom_soong_namespaces():
    """
    Ensure all hardware/qcom-caf platform directories have an enclosing soong_namespace.
    When CAF audio-ar repositories (such as sm8450/audio/primary-hal or sm8550/audio/primary-hal)
    are present on disk without an enclosing soong_namespace guard (e.g. if hardware/qcom-caf/common
    was not synced or linkfiles were unlinked), Soong evaluates their modules in the root namespace
    and attempts to build 'audio.primary.<target>' for non-target platforms, failing on missing configs.
    """
    platforms = [
        "msm8953",
        "msm8996",
        "msm8998",
        "sdm660",
        "sdm845",
        "sm8150",
        "sm8250",
        "sm8350",
        "sm8450",
        "sm8450-6.6",
        "sm8550",
        "sm8650",
        "sm8750",
        "sm8850",
    ]

    qssi_bp_content = """soong_namespace {
    imports: [
        "vendor/qcom/opensource/commonsys-intf/display",
        "vendor/qcom/opensource/display",
    ],
}
"""

    base_dir = "hardware/qcom-caf"
    if not os.path.isdir(base_dir):
        return

    for plat in platforms:
        plat_dir = os.path.join(base_dir, plat)
        if os.path.isdir(plat_dir):
            bp_file = os.path.join(plat_dir, "Android.bp")
            needs_write = False
            if not os.path.exists(bp_file):
                needs_write = True
            elif os.path.islink(bp_file) and not os.path.exists(os.path.realpath(bp_file)):
                try:
                    os.unlink(bp_file)
                except OSError:
                    pass
                needs_write = True
            else:
                try:
                    with open(bp_file, "r") as f:
                        content = f.read()
                    if "soong_namespace" not in content:
                        needs_write = True
                except Exception:
                    needs_write = True

            if needs_write:
                print(f"Creating/repairing soong_namespace guard in {bp_file}")
                with open(bp_file, "w") as f:
                    f.write(qssi_bp_content)

            # Extra defense-in-depth: also ensure audio/ sub-directory is namespace-isolated
            audio_dir = os.path.join(plat_dir, "audio")
            if os.path.isdir(audio_dir):
                audio_bp = os.path.join(audio_dir, "Android.bp")
                if not os.path.exists(audio_bp) or (os.path.islink(audio_bp) and not os.path.exists(os.path.realpath(audio_bp))):
                    try:
                        if os.path.islink(audio_bp):
                            os.unlink(audio_bp)
                        print(f"Creating soong_namespace guard in {audio_bp}")
                        with open(audio_bp, "w") as f:
                            f.write("soong_namespace {\n}\n")
                    except Exception as e:
                        print(f"Warning: could not write {audio_bp}: {e}")


def patch():
    print("=== Applying Upstream Patches ===")
    ensure_qcom_soong_namespaces()
    print("=== Upstream Patches Applied ===")


if __name__ == "__main__":
    patch()
