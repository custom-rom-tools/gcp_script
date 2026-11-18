from cli.base import clear_screen, print_header, print_divider, pause, read_input, GREEN, CYAN, YELLOW, RED, NC, BLUE, BOLD
from cli.services.disk import list_disks, get_attached_disks, attach_disk, detach_disk, format_disk_ext4, delete_disk_gcp, get_mount_info, mount_disk
from cli.services.vm import is_vm_running


def submenu_disk_manager(vm: dict):
    while True:
        clear_screen()
        print_header("Disk Manager", f"{vm['instance']}  {vm['zone']}")
        disks = list_disks(vm)
        attached = get_attached_disks(vm)
        mounts = get_mount_info(vm) if is_vm_running(vm) else {}
        
        print(f"{'Name':<24} {'Size':<10} {'Status'}")
        for disk in disks:
            name = disk.get("name", "")
            size = str(disk.get("sizeGb", "")) + " GB"
            
            # Determine status
            attached_disk = next((d for d in attached if d['name'] == name), None)
            status = "Unattached"
            if attached_disk:
                device = attached_disk['device_name']
                # Check for mount info
                mnt = mounts.get(device)
                if mnt:
                    status = f"Mounted at {mnt['mount_point']} (Used: {mnt['used']}, Free: {mnt['free']})"
                else:
                    status = f"Attached ({device}) - Not mounted"
                
            print(f" {name:<24} {size:<10} {status}")
        print_divider()
        print(f" {GREEN}1){NC} Attach")
        print(f" {YELLOW}2){NC} Detach")
        print(f" {BLUE}3){NC} Mount")
        print(f" {RED}4){NC} Format")
        print(f" {RED}5){NC} Delete")
        print(f" {RED}0){NC} Back")
        choice = read_input("\nSelect 0-5: ").strip()
        if choice == "0":
            break
        candidates = [d for d in disks if d.get("name") not in (vm["instance"], "gcp-boot-ccache-disk")]
        if choice in ("1", "2", "3", "4", "5"):
            if not candidates:
                print(">>> No secondary disks found.")
                pause()
                continue
            print("")
            for i, disk in enumerate(candidates, 1):
                print(f" {i}) {disk.get('name')}")
            selected = read_input("Select disk number: ").strip()
            if not selected.isdigit():
                continue
            index = int(selected) - 1
            if not 0 <= index < len(candidates):
                continue
            name = candidates[index]["name"]
            if choice == "1":
                attach_disk(vm, name)
            elif choice == "2":
                detach_disk(vm, name)
            elif choice == "3":
                mount_point = read_input("Enter mount point (e.g. /mnt/a16): ").strip()
                if mount_point:
                    if mount_disk(vm, name, mount_point):
                        print(f">>> Success: Mounted {name} to {mount_point}")
                    else:
                        print(f">>> Error: Failed to mount {name} to {mount_point}")
            elif choice == "4":
                confirm = read_input(f"Type FORMAT to wipe {name}: ").strip()
                if confirm == "FORMAT":
                    format_disk_ext4(vm, name)
            elif choice == "5":
                confirm = read_input(f"Type DELETE to permanently delete {name}: ").strip()
                if confirm == "DELETE":
                    delete_disk_gcp(vm, name)
            pause()
