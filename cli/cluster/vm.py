"""
VM Cluster & Selector Screen (cli/cluster.py)
Handles VM discovery, active instance selection, creation with 30GB free HDD boot disk,
and instance deletion.
"""

import sys
import time
from pathlib import Path
from cli.base import (
    clear_screen, print_divider, print_thin_divider,
    CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA, BOLD, NC, pause, read_input
)
from cli.services.vm import (
    get_active_vm, save_active_vm, discover_instances,
    get_default_project, ensure_gcloud_authenticated,
    create_new_vm, delete_vm_instance, start_vm, stop_vm
)


def submenu_select_vm(vms: list[dict], project: str) -> dict | None:
    """Submenu 1: List and select an active target VM from available instances."""
    clear_screen()
    print_divider()
    print(f"{BOLD}                SELECT ACTIVE TARGET VM                          {NC}")
    print_divider()

    if not vms:
        print(f"\n   {YELLOW}No VMs found in project {project}.{NC}")
        pause()
        return None

    print(f" Choose an instance to set as active build target:\n")
    for idx, v in enumerate(vms, 1):
        status_color = GREEN if v["status"] == "RUNNING" else RED
        print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {status_color}{v['status']}{NC} IP: {v['ip']:<15}")
        print(f"       Disks: {CYAN}{', '.join(v['disks'])}{NC}")

    print(f"\n   {RED}0){NC} Cancel")
    print_divider()

    choice = read_input(f" Select VM 1-{len(vms)} or 0: ").strip()
    try:
        c_idx = int(choice)
        if 1 <= c_idx <= len(vms):
            selected = vms[c_idx - 1]
            save_active_vm(selected["name"], selected["zone"], project,
                           selected["general_disk"], selected.get("machine_type", ""))
            print(f"\n{GREEN}>>> Active VM: {BOLD}{selected['name']}{NC} {selected['zone']}{NC}")
            time.sleep(1)
            return {
                "instance": selected["name"],
                "zone": selected["zone"],
                "project": project,
                "general_disk": selected["general_disk"]
            }
    except ValueError:
        pass
    return None


def submenu_create_vm(project: str) -> dict | None:
    """Submenu 2: Provision a new build VM with 30GB free standard HDD boot disk."""
    clear_screen()
    print_divider()
    print(f"{BOLD}                 CREATE NEW GCP BUILD VM                         {NC}")
    print_divider()
    print(f" Specifications:")
    print(f"   - Boot Disk: {GREEN}30GB Free Standard Persistent Disk (pd-standard HDD){NC}")
    print(f"   - OS Image:  {GREEN}Ubuntu 22.04 LTS (x86_64){NC}")
    print(f"   - Machine:   {GREEN}t2d-standard-16 (16 Dedicated AMD Milan Cores, 60GB RAM){NC}")
    print(f"   - Data Disk: {GREEN}Optional (External attachment){NC}")
    print_divider()

    existing_vms = [v["name"] for v in discover_instances(project)]
    idx = 2
    while f"rom-build-farm-{idx}" in existing_vms or f"rom-build-farm" == f"rom-build-farm-{idx}":
        idx += 1
    default_name = f"rom-build-farm-{idx}"

    in_name = read_input("\nEnter VM name: ").strip()
    new_vm = in_name or default_name

    print("\n Select Region & Zone (Lowest-cost Tier 1 regions):")
    print(f"   {GREEN}1){NC} us-east1-b    (South Carolina - Co-cheapest)")
    print(f"   {GREEN}2){NC} us-central1-a (Iowa - Co-cheapest)")
    print(f"   {GREEN}3){NC} us-west1-b    (Oregon - Co-cheapest)")
    print(f"   {GREEN}4){NC} Custom zone")
    z_choice = read_input("Choose zone 1-4: ").strip()

    zone_map = {"1": "us-east1-b", "2": "us-central1-a", "3": "us-west1-b"}
    if z_choice == "4":
        cz = read_input("Enter custom GCP zone: ").strip()
        new_zone = cz or "us-east1-b"
    else:
        new_zone = zone_map.get(z_choice, "us-east1-b")

    print("\n Machine Type:")
    print(f"   {GREEN}1){NC} t2d-standard-16 (16 dedicated Milan cores, 60GB RAM - Recommended)")
    print(f"   {GREEN}2){NC} c2d-standard-16 (16 compute cores, 64GB RAM)")
    print(f"   {GREEN}3){NC} t2d-standard-8  (8 dedicated cores, 32GB RAM)")
    mt_choice = read_input("Choose machine type 1-3: ").strip()
    mt_map = {"1": "t2d-standard-16", "2": "c2d-standard-16", "3": "t2d-standard-8"}
    new_mt = mt_map.get(mt_choice, "t2d-standard-16")

    default_disk = f"gcp-data-disk-{idx}"
    att_choice = read_input("\nAttach data disk? y/N: ").strip().lower()
    attach_disk = att_choice in ("y", "yes")

    new_disk = ""
    if attach_disk:
        in_disk = read_input("Data disk name: ").strip()
        new_disk = in_disk or default_disk

    print(f"\n{YELLOW}Ready to create VM:{NC}")
    print(f"  Instance Name: {BOLD}{new_vm}{NC}")
    print(f"  Zone:          {BOLD}{new_zone}{NC}")
    print(f"  Machine Type:  {BOLD}{new_mt}{NC}")
    print(f"  Boot Disk:     {BOLD}30GB Free HDD (pd-standard){NC}")
    if attach_disk:
        print(f"  Data Disk:     {BOLD}{new_disk}{NC}")

    conf = read_input("\nProceed with VM creation? y/N: ").strip().lower()
    if conf not in ("y", "yes"):
        print(">>> Creation cancelled.")
        time.sleep(1)
        return None

    created = create_new_vm(name=new_vm, zone=new_zone, machine_type=new_mt, data_disk=new_disk, project=project)
    if created:
        print(f"\n{GREEN}>>> VM created: {new_vm}{NC}")
        pause()
        return created
    pause()
    return None


def submenu_delete_vm(vms: list[dict], project: str) -> bool:
    """Submenu 3: Delete a selected VM, with option to delete attached secondary data disks."""
    clear_screen()
    print_divider()
    print(f"{BOLD}                     DELETE A GCP BUILD VM                       {NC}")
    print_divider()

    if not vms:
        print(f"\n   {YELLOW}No VMs found to delete.{NC}")
        pause()
        return False

    print(" Available VMs to delete:\n")
    cur_vm = get_active_vm()
    for idx, v in enumerate(vms, 1):
        is_act = f" {YELLOW}ACTIVE{NC}" if v["name"] == cur_vm.get("instance") else ""
        print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']}{NC} {v['zone']}{is_act}")
        print(f"      Attached disks: {CYAN}{', '.join(v['disks'])}{NC}")

    print(f"\n   {RED}0){NC} Cancel")
    print_divider()

    choice = read_input(f" Select VM 1-{len(vms)} or 0: ").strip()
    try:
        c_idx = int(choice)
        if c_idx == 0:
            return False
        if not (1 <= c_idx <= len(vms)):
            return False
    except ValueError:
        return False

    target = vms[c_idx - 1]
    target_name = target["name"]
    target_zone = target["zone"]
    data_disks = [d for d in target["disks"] if d != target_name]

    print(f"\nTarget VM: {RED}{target_name}{NC} in {CYAN}{target_zone}{NC}")
    print(f"Boot disk '{target_name}' will be automatically deleted.")

    delete_data_disks = []
    if data_disks:
        print(f"\nAttached data disk(s) found: {', '.join(data_disks)}")
        del_disk_in = read_input("Also delete attached disks? y/N: ").strip().lower()
        if del_disk_in in ("y", "yes"):
            delete_data_disks = data_disks
            print(f"{RED}>>> Data disk(s) WILL BE PERMANENTLY DELETED.{NC}")
        else:
            print(f"{GREEN}>>> Data disk(s) will be PRESERVED (detached only).{NC}")

    print(f"\n{RED}⚠️  WARNING: You are about to permanently delete VM '{target_name}'.{NC}")
    final_conf = read_input("Type yes to confirm: ").strip()
    if final_conf != "yes":
        print(">>> Deletion cancelled by user.")
        time.sleep(1)
        return False

    deleted = delete_vm_instance(target_name, target_zone, delete_data_disks, project)
    if deleted:
        if target_name == cur_vm.get("instance"):
            remaining = [v for v in discover_instances(project) if v["name"] != target_name]
            if remaining:
                save_active_vm(remaining[0]["name"], remaining[0]["zone"], project,
                               remaining[0]["general_disk"], remaining[0].get("machine_type", ""))
        print(f"\n{GREEN}>>> VM deleted.{NC}")
        pause()
        return True
    pause()
    return False


def submenu_toggle_power(vms: list[dict], project: str):
    """Submenu 4: Toggle Start / Stop directly on any VM."""
    clear_screen()
    print("\nToggle Power:")
    for idx, v in enumerate(vms, 1):
        status_color = GREEN if v["status"] == "RUNNING" else RED
        print(f"   {idx}) {v['name']} {status_color}{v['status']}{NC}")

    pwr_num = read_input(f" Enter VM number 1-{len(vms)}: ").strip()
    if pwr_num.isdigit() and 1 <= int(pwr_num) <= len(vms):
        target_v = vms[int(pwr_num) - 1]
        target_dict = {
            "instance": target_v["name"],
            "zone": target_v["zone"],
            "project": project,
            "general_disk": target_v.get("general_disk", "gcp-general-disk"),
        }
        if target_v["status"] == "RUNNING":
            stop_vm(target_dict)
        else:
            start_vm(target_dict)
        pause()


def run_cluster_menu() -> dict:
    """Main VM Selector & Cluster Manager loop (Startup Screen)."""
    ensure_gcloud_authenticated()

    while True:
        clear_screen()
        vm = get_active_vm()
        project = vm.get("project") or get_default_project()
        vms = discover_instances(project)
        count = len(vms)

        print_divider()
        print(f"{BOLD}             GCP BUILD CLUSTER - VM SELECTOR & MANAGER           {NC}")
        print_divider()
        print(f" Active VM: {GREEN}{vm['instance']}{NC} | Zone: {CYAN}{vm['zone']}{NC}")
        print(f" Disk: {BLUE}{vm['general_disk']}{NC}")
        print_thin_divider()
        print(f" Build VMs:\n")

        if count == 0:
            print(f"   {YELLOW}(No VMs currently found in project {project}){NC}")
        else:
            for idx, v in enumerate(vms, 1):
                status_color = GREEN if v["status"] == "RUNNING" else RED
                active_marker = f"  {YELLOW}◄ ACTIVE TARGET{NC}" if v["name"] == vm["instance"] else ""
                print(f"   {idx}) {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {status_color}{v['status']}{NC}{active_marker}")
                print(f"       Disks: {CYAN}{', '.join(v['disks'])}{NC}")

        print(f"\n{CYAN}-----------------------------------------------------------------{NC}")
        print(f" Actions:")
        print(f" {GREEN}1){NC} Select VM")
        print(f" {CYAN}2){NC} Create VM")
        print(f" {RED}3){NC} Delete VM")
        print(f" {YELLOW}4){NC} Start or Stop VM")
        print(f" {BLUE}5){NC} Refresh")
        print(f" {MAGENTA}6){NC} Google Account")
        print(f" Enter or 0) Build Menu")
        print("")
        print(f" {RED}q){NC} Quit")
        print_divider()

        choice = read_input(" Select 1-6, 0 or q: ").strip().lower()

        if choice in ("", "0"):
            return vm
        elif choice in ("q", "quit", "exit"):
            print("\nGoodbye!")
            sys.exit(0)
        elif choice == "1":
            new_v = submenu_select_vm(vms, project)
            if new_v:
                return new_v
        elif choice == "2":
            new_v = submenu_create_vm(project)
            if new_v:
                return new_v
        elif choice == "3":
            submenu_delete_vm(vms, project)
        elif choice == "4":
            submenu_toggle_power(vms, project)
        elif choice == "5":
            print(f"{CYAN}Refreshing from GCP...{NC}")
            discover_instances(project, refresh=True)
            time.sleep(0.5)
        elif choice == "6":
            from cli.tools.auth import submenu_gcloud_auth
            submenu_gcloud_auth()
