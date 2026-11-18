#!/usr/bin/env python3
"""
Clean hook template.

Add custom cleanup logic here. This template performs no destructive actions.
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
    parser = argparse.ArgumentParser(description="Dummy clean hook")
    parser.add_argument("--clean-trees", action="store_true")
    parser.add_argument("--target", default="all")
    parser.add_argument("--device", default="")
    args = parser.parse_args()

    rom_config = load_json("rom.json")
    recovery_config = load_json("recovery.json")

    print("[CLEAN] Dummy clean hook")
    print(f"[CLEAN] Target: {args.target}")
    print(f"[CLEAN] Device: {args.device or 'from configuration'}")
    print(f"[CLEAN] Configs: rom={bool(rom_config)}, recovery={bool(recovery_config)}")
    print("[CLEAN] No cleanup actions performed.")


if __name__ == "__main__":
    main()
