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
        rec = DISK_PRESETS["recovery"]

        print(f" {BOLD}[CREATE & LINK]{NC}")
        print(f"  {GREEN}1){NC} Create ROM & Kernel Disk "
              f"({general['size_gb']}GB - default /mnt/rom)")
        print(f"  {GREEN}2){NC} Create Recovery Disk "
              f"({rec['size_gb']}GB - default /mnt/recovery)")
        print(f"  {GREEN}3){NC} Create Custom Disk "
              f"(Any size & name)")
        print()
        print(f" {BOLD}[ATTACH / DETACH / MOUNT / FORMAT]{NC}")
        print(f"  {BLUE}4){NC} Attach Disk")
        print(f"  {YELLOW}5){NC} Detach Disk")
        print(f"  {GREEN}6){NC} Mount Disk")
        print(f"  {RED}7){NC} Format Disk (Clean ext4 wipe)")
        print()
        print(f" {BOLD}[DELETE - STOP BILLING]{NC}")
        print(f"  {RED}8){NC} Delete Disk Permanently")
        print()
        print(f" {BOLD}[INFO]{NC}")
        print(f"  {CYAN}9){NC} Check Disk Space & Mounts")
        print()
        print(f"  {YELLOW}r){NC} Reload / Refresh Disks")
        print(f"  {RED}0 / q){NC} Back to Main Menu / Quit")
        print_divider()

        choice = read_input(" Select option [0-9, r, q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return
        if choice in ("r", "reload", "refresh"):
            from cli.services.vm import invalidate_vm_cache
            from cli.services.disk import invalidate_disk_cache
            invalidate_vm_cache(vm)
            invalidate_disk_cache(vm)
            continue
        if choice in ("1", "2"):
            is_rom = (choice == "1")
            preset = general if is_rom else rec
            role_title = "ROM & Kernel Disk" if is_rom else "Recovery Disk"
            target_mp = "/mnt/rom" if is_rom else "/mnt/recovery"
            default_dname = preset["name"]
            default_dsize = preset["size_gb"]
            desc = preset.get("description", f"Dedicated {default_dsize}GB storage for {role_title} ({target_mp})")

            print(f"\n{BOLD}--- Create & Link {role_title} ---{NC}")
            print(f" Default Disk Name  : {GREEN}{default_dname}{NC}")
            print(f" Default Disk Size  : {GREEN}{default_dsize} GB{NC}")
            print(f" Target Mount Point : {CYAN}{target_mp}{NC}\n")

            ans = read_input(f"Create '{default_dname}' with default values ({default_dsize}GB for {target_mp})? [Y/n, e=edit, default: Y]: ").strip().lower()
            if ans in ("q", "quit", "abort", "n", "no"):
                print(">>> Disk creation cancelled.")
                pause()
                continue

            target_dname = default_dname
            target_dsize = default_dsize
            if ans in ("e", "edit", "custom"):
                in_dname = read_input(f"Enter disk name [default: {default_dname}, q=abort]: ").strip()
                if in_dname.lower() in ("q", "quit", "exit"):
                    print(">>> Disk creation cancelled.")
                    pause()
                    continue
                target_dname = in_dname or default_dname
                in_dsize = read_input(f"Enter disk size in GB [default: {default_dsize}, q=abort]: ").strip()
                if in_dsize.lower() in ("q", "quit", "exit"):
                    print(">>> Disk creation cancelled.")
                    pause()
                    continue
                target_dsize = int(in_dsize) if in_dsize.isdigit() and int(in_dsize) > 0 else default_dsize

            create_and_attach_disk(vm, target_dname, target_dsize, desc)
            pause()
        elif choice == "3":
            print(f"\n{BOLD}--- Create & Link Custom Disk ---{NC}")
            print("Create a disk with any custom name, size, and mount point.\n")
            in_dname = read_input("Enter disk name [default: gcp-custom-disk, q=abort]: ").strip()
            if in_dname.lower() in ("q", "quit", "exit"):
                print(">>> Disk creation cancelled.")
                pause()
                continue
            target_dname = in_dname or "gcp-custom-disk"

            in_dsize = read_input("Enter disk size in GB [default: 100, q=abort]: ").strip()
            if in_dsize.lower() in ("q", "quit", "exit"):
                print(">>> Disk creation cancelled.")
                pause()
                continue
            target_dsize = int(in_dsize) if in_dsize.isdigit() and int(in_dsize) > 0 else 100

            in_mp = read_input("Enter target mount point [default: /mnt/custom, q=abort]: ").strip()
            if in_mp.lower() in ("q", "quit", "exit"):
                print(">>> Disk creation cancelled.")
                pause()
                continue
            target_mp = in_mp or "/mnt/custom"

            desc = f"Custom {target_dsize}GB storage disk {target_dname} ({target_mp})"
            conf = read_input(f"Proceed with creating '{target_dname}' ({target_dsize}GB for {target_mp})? [Y/n, default: Y, q=abort]: ").strip().lower()
            if conf not in ("n", "no", "q", "quit", "exit"):
                create_and_attach_disk(vm, target_dname, target_dsize, desc)
            else:
                print(">>> Disk creation cancelled.")
            pause()
        elif choice in ("4", "attach"):
            attach_disk_action(vm)
            pause()
        elif choice in ("5", "detach"):
            detach_disk_action(vm)
            pause()
        elif choice in ("6", "mount"):
            mount_disk_action(vm)
            pause()
        elif choice in ("7", "format"):
            format_disk_action(vm)
            pause()
        elif choice in ("8", "delete"):
            delete_disk_action(vm)
            pause()
        elif choice in ("9", "10", "info", "space"):
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it to inspect live disk space and mounts.")
                pause()
                continue
            res = vm_exec(
                vm,
                "df -h / /mnt/rom /mnt/recovery 2>/dev/null; echo ''; "
                "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT /dev/sd* 2>/dev/null || true; "
                "echo ''; free -h; swapon --show 2>/dev/null",
                check=False,
                timeout=45,
            )
            print(res.stdout or "")
            pause()
