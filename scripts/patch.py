#!/usr/bin/env python3
"""
Patch hook template.

Add custom patch logic here. This template performs no source modifications.
"""

import argparse
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / "configs"


def load_json(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Dummy patch hook")
    parser.add_argument("--target", default="all")
    parser.add_argument("--rom", default="")
    parser.add_argument("--flavor", default="")
    parser.add_argument("--device", "--dev", dest="device", default="")
    args = parser.parse_args()

    rom_config = load_json("rom.json")
    recovery_config = load_json("recovery.json")

    print("[PATCH] Dummy patch hook")
    print(f"[PATCH] Target: {args.target}")
    print(f"[PATCH] ROM: {args.rom or 'not specified'}")
    print(f"[PATCH] Recovery: {args.flavor or 'not specified'}")
    print(f"[PATCH] Device: {args.device or 'from configuration'}")
    print(f"[PATCH] Configs: rom={bool(rom_config)}, recovery={bool(recovery_config)}")
    print("[PATCH] No patch actions performed.")


if __name__ == "__main__":
    main()
