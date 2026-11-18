from cli.base import clear_screen, print_header, print_divider, pause, read_input, GREEN, CYAN, YELLOW, RED, NC
from cli.services.disk import list_disks, get_attached_disks, attach_disk, detach_disk, format_disk_ext4, delete_disk_gcp


def submenu_disk_manager(vm: dict):
    while True:
        clear_screen()
        print_header("Disk Manager", f"{vm['instance']}  {vm['zone']}")
        disks = list_disks(vm)
        attached = get_attached_disks(vm)
        print("Disks:")
        for disk in disks:
            name = disk.get("name", "")
            size = disk.get("sizeGb", "")
            print(f" {name:<24} {size:>4} GB")
        print("")
        print("Attached:")
        for disk in attached:
            print(f" {disk['name']:<24} {disk['device_name']}")
        print_divider()
        print(f" {GREEN}1){NC} Attach")
        print(f" {YELLOW}2){NC} Detach")
        print(f" {RED}3){NC} Format")
        print(f" {RED}4){NC} Delete")
        print(f" {RED}0){NC} Back")
        choice = read_input("\nSelect 0-4: ").strip()
        if choice == "0":
            break
        candidates = [d for d in disks if d.get("name") not in (vm["instance"], "gcp-boot-ccache-disk")]
        if choice in ("1", "2", "3", "4"):
            if not candidates:
                print(">>> No secondary disks found.")
                pause()
                continue
            print("")
            for i, disk in enumerate(candidates, 1):
                print(f" {i}) {disk.get('name')}")
            selected = read_input("Select disk: ").strip()
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
                confirm = read_input(f"Type FORMAT to wipe {name}: ").strip()
                if confirm == "FORMAT":
                    format_disk_ext4(vm, name)
            elif choice == "4":
                confirm = read_input(f"Type DELETE to permanently delete {name}: ").strip()
                if confirm == "DELETE":
                    delete_disk_gcp(vm, name)
            pause()
