"""
GCP Disk Manager Menu (cli/storage/disk.py)
Create, attach, detach, format, delete and inspect the build data disks.
"""

from cli.base import (
    clear_screen, print_header, print_divider, pause, read_input,
    GREEN, CYAN, YELLOW, RED, BLUE, BOLD, NC,
)
from cli.services.disk import (
    DISK_PRESETS, print_disk_tables, create_and_attach_disk,
    attach_disk_action, detach_disk_action, mount_disk_action,
    format_disk_action, delete_disk_action,
)
from cli.services.vm import is_vm_running, vm_exec


def submenu_disk_manager(vm: dict):
    """Main disk management loop."""
    while True:
        clear_screen()
        print_header("GCP ROM DISK MANAGER", f"{vm['instance']}  {vm['zone']}")
        print_disk_tables(vm)
        print()
        general = DISK_PRESETS["general"]
        a16 = DISK_PRESETS["a16"]
        a17 = DISK_PRESETS["a17"]
        rec = DISK_PRESETS["recovery"]

        print(f" {BOLD}[CREATE & LINK]{NC}")
        print(f"  {GREEN}1){NC} Create General Shared Disk "
              f"({general['size_gb']}GB - all ROMs & recovery)")
        print(f"  {GREEN}2){NC} Create A16 Disk ({a16['size_gb']}GB - dedicated /mnt/a16)")
        print(f"  {GREEN}3){NC} Create A17 Disk ({a17['size_gb']}GB - dedicated /mnt/a17)")
        print(f"  {GREEN}4){NC} Create Recovery Disk "
              f"({rec['size_gb']}GB - dedicated /mnt/recovery)")
        print()
        print(f" {BOLD}[ATTACH / DETACH / MOUNT / FORMAT]{NC}")
        print(f"  {BLUE}5){NC} Attach Disk")
        print(f"  {YELLOW}6){NC} Detach Disk")
        print(f"  {GREEN}7){NC} Mount Disk")
        print(f"  {RED}8){NC} Format Disk (Clean ext4 wipe)")
        print()
        print(f" {BOLD}[DELETE - STOP BILLING]{NC}")
        print(f"  {RED}9){NC} Delete Disk Permanently")
        print()
        print(f" {BOLD}[INFO]{NC}")
        print(f"  {CYAN}10){NC} Check Disk Space & Mounts")
        print(f"  {BOLD}0){NC} Back to Main Menu")
        print_divider()

        choice = read_input(" Select option [0-10, default: 0]: ").strip().lower()
        if choice in ("0", "q", ""):
            return
        if choice in ("1", "2", "3", "4"):
            preset_map = {
                "1": general,
                "2": a16,
                "3": a17,
                "4": rec,
            }
            preset = preset_map[choice]
            default_dname = preset["name"]
            default_dsize = preset["size_gb"]
            desc = preset.get("description", "Dedicated storage for ROM builds")
            print(f"\n{BOLD}--- Create & Link {default_dname} ---{NC}")
            in_dname = read_input(f"Enter disk name [default: {default_dname}]: ").strip()
            target_dname = in_dname or default_dname
            in_dsize = read_input(f"Enter disk size in GB [default: {default_dsize}]: ").strip()
            target_dsize = int(in_dsize) if in_dsize.isdigit() and int(in_dsize) > 0 else default_dsize
            conf = read_input(f"Proceed with creating '{target_dname}' ({target_dsize}GB)? [Y/n, default: Y]: ").strip().lower()
            if conf not in ("n", "no"):
                create_and_attach_disk(vm, target_dname, target_dsize, desc)
            else:
                print(">>> Disk creation cancelled.")
            pause()
        elif choice == "5":
            attach_disk_action(vm)
            pause()
        elif choice == "6":
            detach_disk_action(vm)
            pause()
        elif choice == "7":
            mount_disk_action(vm)
            pause()
        elif choice == "8":
            format_disk_action(vm)
            pause()
        elif choice == "9":
            delete_disk_action(vm)
            pause()
        elif choice == "10":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it to inspect live disk space and mounts.")
                pause()
                continue
            res = vm_exec(
                vm,
                "df -h / /mnt/a16 /mnt/a17 /mnt/recovery 2>/dev/null; echo ''; "
                "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT /dev/sd* 2>/dev/null || true; "
                "echo ''; free -h; swapon --show 2>/dev/null",
                check=False,
                timeout=45,
            )
            print(res.stdout or "")
            pause()
