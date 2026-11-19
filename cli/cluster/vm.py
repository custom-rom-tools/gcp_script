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
    create_new_vm, delete_vm_instance, start_vm, stop_vm,
    get_region_from_zone, get_vm_boot_disk, list_disks_in_region
)


def submenu_select_vm(vms: list[dict], project: str) -> dict | None:
    """Submenu 1: List and select a build VM from available instances."""
    clear_screen()
    print_divider()
    print(f"{BOLD}                     SELECT BUILD VM                             {NC}")
    print_divider()

    if not vms:
        print(f"\n   {YELLOW}No VMs found in project {project}.{NC}")
        pause()
        return None

    online_vms = [v for v in vms if v["status"] == "RUNNING"]
    offline_vms = [v for v in vms if v["status"] != "RUNNING"]
    all_vms = online_vms + offline_vms

    print(f" Choose an instance to manage / build on:\n")
    if online_vms:
        print(f" {GREEN}{BOLD}Online VMs:{NC}")
        for idx, v in enumerate(online_vms, 1):
            ip_str = f"IP: {v.get('ip', 'None')}"
            disks_str = f"Disks: {', '.join(v['disks'])}" if v.get('disks') else "Disks: none"
            print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {GREEN}{v['status']}{NC} {ip_str}")
            print(f"       {CYAN}{disks_str}{NC}")

    if offline_vms:
        start_idx = len(online_vms) + 1
        print(f"\n {RED}{BOLD}Offline VMs:{NC}")
        for idx, v in enumerate(offline_vms, start_idx):
            disks_str = f"Disks: {', '.join(v['disks'])}" if v.get('disks') else "Disks: none"
            print(f"   {YELLOW}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {RED}{v['status']}{NC}")
            print(f"       {CYAN}{disks_str}{NC}")

    print(f"\n   {RED}0){NC} Cancel")
    print_divider()

    choice = read_input(f" Select VM 1-{len(all_vms)} or 0: ").strip()
    try:
        c_idx = int(choice)
        if 1 <= c_idx <= len(all_vms):
            selected = all_vms[c_idx - 1]
            save_active_vm(selected["name"], selected["zone"], project,
                           selected["general_disk"], selected.get("machine_type", ""))
            print(f"\n{GREEN}>>> Selected VM: {BOLD}{selected['name']}{NC} ({selected['zone']}){NC}")
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
    for idx, v in enumerate(vms, 1):
        status_color = GREEN if v["status"] == "RUNNING" else RED
        print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {status_color}{v['status']}{NC}")
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
    target_region = get_region_from_zone(target_zone)

    # 1. Resolve boot disk
    boot_disk_name = get_vm_boot_disk(target_name, target_zone, project) or target_name

    print(f"\nTarget VM:    {RED}{BOLD}{target_name}{NC} in zone {CYAN}{target_zone}{NC} (Region: {CYAN}{target_region}{NC})")
    print(f"Boot Disk:    {RED}{BOLD}{boot_disk_name}{NC} (will be automatically deleted with the VM)")

    # 2. Discover all disks in this region (both attached and unattached)
    print(f"\n{CYAN}>>> Scanning region '{target_region}' for all disks (attached & unattached)...{NC}")
    region_disks = list_disks_in_region(target_region, project)

    # Filter out boot disk from the list of other regional disks
    other_disks = [d for d in region_disks if d.get("name") != boot_disk_name]
    disks_to_delete = []

    if other_disks:
        print(f"\n{BOLD}Found {len(other_disks)} other disk(s) in region '{target_region}':{NC}")
        for d in other_disks:
            d_name = d.get("name", "")
            d_size = d.get("sizeGb", "")
            d_zone = d.get("zone", "")
            users = d.get("users", [])

            if target_name in users:
                status_desc = f"{YELLOW}Attached to this VM ({target_name}){NC}"
            elif users:
                status_desc = f"{YELLOW}Attached to {', '.join(users)}{NC}"
            else:
                status_desc = f"{CYAN}Unattached (standalone){NC}"

            print(f"\n • Disk: {BOLD}{d_name}{NC} ({d_size} GB, Zone: {d_zone}) - {status_desc}")
            ans = read_input(f"   Delete disk '{d_name}'? (y/N): ").strip().lower()
            if ans in ("y", "yes"):
                disks_to_delete.append(d)
                print(f"   {RED}✔ Marked '{d_name}' for deletion.{NC}")
            else:
                print(f"   {GREEN}✔ Preserving '{d_name}'.{NC}")
    else:
        print(f"\n{GREEN}ℹ No other secondary or standalone disks found in region '{target_region}'.{NC}")

    # 3. Final summary of actions
    print(f"\n{RED}{'=' * 65}{NC}")
    print(f"{BOLD}{RED}⚠️  SUMMARY OF PERMANENT DELETION:{NC}")
    print(f"{RED}{'=' * 65}{NC}")
    print(f"  • VM Instance: {BOLD}{target_name}{NC} ({target_zone})")
    print(f"  • Boot Disk:   {BOLD}{boot_disk_name}{NC} {RED}(will be permanently deleted){NC}")
    if disks_to_delete:
        print(f"  • Other Disks ({len(disks_to_delete)} to delete in {target_region}):")
        for d in disks_to_delete:
            print(f"    - {RED}{d['name']}{NC} ({d['sizeGb']} GB, Zone: {d['zone']})")
    else:
        print(f"  • Other Disks: None (all other disks in {target_region} will be preserved)")
    print(f"{RED}{'=' * 65}{NC}")

    final_conf = read_input("Type yes to confirm permanent deletion: ").strip().lower()
    if final_conf not in ("yes", "delete"):
        print(">>> Deletion cancelled by user.")
        time.sleep(1)
        return False

    deleted = delete_vm_instance(
        name=target_name,
        zone=target_zone,
        boot_disk=boot_disk_name,
        delete_disks=disks_to_delete,
        project=project
    )
    if deleted:
        remaining = [v for v in discover_instances(project) if v["name"] != target_name]
        if remaining:
            save_active_vm(remaining[0]["name"], remaining[0]["zone"], project,
                           remaining[0]["general_disk"], remaining[0].get("machine_type", ""))
        else:
            save_active_vm("", "", project, "gcp-general-disk", "")
            from cli.services.vm import ACTIVE_VM_FILE
            if ACTIVE_VM_FILE.is_file():
                try:
                    ACTIVE_VM_FILE.unlink()
                except OSError:
                    pass
        print(f"\n{GREEN}>>> VM '{target_name}' and selected disks have been permanently deleted.{NC}")
        pause()
        return True
    pause()
    return False


def submenu_toggle_power(vms: list[dict], project: str):
    """Submenu 4: Toggle Start / Stop directly on any VM."""
    clear_screen()
    print_divider()
    print(f"{BOLD}                   START OR STOP A BUILD VM                      {NC}")
    print_divider()
    if not vms:
        print(f"\n   {YELLOW}No VMs found in project {project}.{NC}")
        pause()
        return

    print(" Select VM to toggle power:\n")
    for idx, v in enumerate(vms, 1):
        status_color = GREEN if v["status"] == "RUNNING" else RED
        action_hint = f"{RED}[Will Stop]{NC}" if v["status"] == "RUNNING" else f"{GREEN}[Will Start]{NC}"
        print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {status_color}{v['status']:<10}{NC} {action_hint}")

    print(f"\n   {RED}0){NC} Cancel")
    print_divider()
    pwr_num = read_input(f" Enter VM number 1-{len(vms)} or 0: ").strip()
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

        online_vms = [v for v in vms if v["status"] == "RUNNING"]
        offline_vms = [v for v in vms if v["status"] != "RUNNING"]

        if count == 0:
            print(f" Build VMs:\n")
            print(f"   {YELLOW}(No VMs currently found in project {project}){NC}\n")
        else:
            print(f" {GREEN}{BOLD}Online VMs ({len(online_vms)}):{NC}")
            if not online_vms:
                print(f"   {YELLOW}(No VMs currently online){NC}")
            else:
                for v in online_vms:
                    ip_str = f"IP: {v.get('ip', 'None')}"
                    disks_str = f"Disks: {', '.join(v['disks'])}" if v.get('disks') else "Disks: none"
                    print(f"   {GREEN}•{NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {GREEN}{v['status']:<9}{NC} {ip_str}")
                    print(f"     {CYAN}{disks_str}{NC}")

            print(f"\n {RED}{BOLD}Offline VMs ({len(offline_vms)}):{NC}")
            if not offline_vms:
                print(f"   {YELLOW}(No VMs currently offline){NC}")
            else:
                for v in offline_vms:
                    disks_str = f"Disks: {', '.join(v['disks'])}" if v.get('disks') else "Disks: none"
                    print(f"   {RED}•{NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {RED}{v['status']:<9}{NC}")
                    print(f"     {CYAN}{disks_str}{NC}")
            print()

        print_thin_divider()
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
            if count == 0:
                print(f"\n{YELLOW}⚠️  No VMs found in project '{project}'. Please create a VM first (Option 2).{NC}")
                pause()
                continue
            # Pick a valid VM from discovered instances
            valid_vm = None
            if vm.get("instance"):
                valid_vm = next((v for v in vms if v["name"] == vm["instance"]), None)
            if not valid_vm:
                valid_vm = next((v for v in vms if v["status"] == "RUNNING"), vms[0])
                save_active_vm(valid_vm["name"], valid_vm["zone"], project,
                               valid_vm["general_disk"], valid_vm.get("machine_type", ""))
            return {
                "instance": valid_vm["name"],
                "zone": valid_vm["zone"],
                "project": project,
                "general_disk": valid_vm["general_disk"],
            }
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
