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
    create_new_vm, delete_vm_instance, start_vm, stop_vm, restart_vm,
    get_region_from_zone, get_vm_boot_disk, list_disks_in_region
)


def submenu_select_vm(vms: list[dict], project: str) -> dict | None:
    """Submenu 1: List and select a build VM from available instances."""
    while True:
        clear_screen()
        print_divider()
        print(f"{BOLD}                     SELECT BUILD VM                             {NC}")
        print_divider()

        if not vms:
            print(f"\n   {YELLOW}No VMs found in project {project}.{NC}")
            print(f"\n   {YELLOW}r){NC} Reload / Refresh")
            print(f"   {RED}0 / q){NC} Cancel / Back")
            print_divider()
            choice = read_input(" Select option [r, 0, q]: ").strip().lower()
            if choice in ("r", "reload", "refresh"):
                vms = discover_instances(project, refresh=True)
                continue
            return None

        all_vms = sorted(vms, key=lambda v: v["name"])
        current_active = get_active_vm().get("instance", "")

        print(f" Choose an instance to manage / build on:\n")
        for idx, v in enumerate(all_vms, 1):
            is_active = (v["name"] == current_active)
            status_color = GREEN if v["status"] == "RUNNING" else RED
            active_badge = f" {GREEN}{BOLD}[ACTIVE]{NC}" if is_active else ""
            ip_str = f"IP: {v.get('ip', 'None')}" if v["status"] == "RUNNING" else ""
            disks_str = f"Disks: {', '.join(v['disks'])}" if v.get('disks') else "Disks: none"
            print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {status_color}{v['status']:<10}{NC}{active_badge} {ip_str}".rstrip())
            print(f"       {CYAN}{disks_str}{NC}")

        print(f"\n   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Cancel / Back")
        print_divider()

        choice = read_input(f" Select VM 1-{len(all_vms)}, r or 0/q [default: 1]: ").strip().lower()
        if not choice:
            choice = "1"
        if choice in ("0", "q", "quit", "exit"):
            return None
        if choice in ("r", "reload", "refresh"):
            vms = discover_instances(project, refresh=True)
            continue
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
    print(f"   - Machine:   {GREEN}t2d-standard-16 (16 Dedicated AMD Milan Cores, 64GB RAM){NC}")
    print(f"   - Data Disk: {GREEN}Optional (External attachment){NC}")
    print_divider()

    existing_vms = [v["name"] for v in discover_instances(project)]
    if "rom-build-farm" not in existing_vms:
        default_name = "rom-build-farm"
    else:
        idx = 2
        while f"rom-build-farm-{idx}" in existing_vms:
            idx += 1
        default_name = f"rom-build-farm-{idx}"

    in_name = read_input(f"\nEnter VM name [default: {default_name}, q=abort]: ").strip()
    if in_name.lower() in ("q", "quit", "exit"):
        print(">>> Creation cancelled.")
        time.sleep(1)
        return None
    new_vm = in_name or default_name

    print("\n Select Region & Zone (Lowest-cost Tier 1 regions):")
    print(f"   {GREEN}1){NC} us-east1-b    (South Carolina - Co-cheapest)")
    print(f"   {GREEN}2){NC} us-central1-a (Iowa - Co-cheapest)")
    print(f"   {GREEN}3){NC} us-west1-b    (Oregon - Co-cheapest)")
    print(f"   {GREEN}4){NC} Custom zone")
    z_choice = read_input("Choose zone 1-4 [default: 1 (us-east1-b), q=abort]: ").strip()
    if z_choice.lower() in ("q", "quit", "exit"):
        print(">>> Creation cancelled.")
        time.sleep(1)
        return None

    zone_map = {"1": "us-east1-b", "2": "us-central1-a", "3": "us-west1-b"}
    if z_choice == "4":
        cz = read_input("Enter custom GCP zone [default: us-east1-b, q=abort]: ").strip()
        if cz.lower() in ("q", "quit", "exit"):
            print(">>> Creation cancelled.")
            time.sleep(1)
            return None
        new_zone = cz or "us-east1-b"
    else:
        new_zone = zone_map.get(z_choice or "1", "us-east1-b")

    print("\n Machine Type:")
    print(f"   {GREEN}1){NC} t2d-standard-16 (16 dedicated Milan cores, 64GB RAM - Recommended)")
    print(f"   {GREEN}2){NC} c2d-standard-16 (16 compute cores, 64GB RAM)")
    print(f"   {GREEN}3){NC} t2d-standard-8  (8 dedicated cores, 32GB RAM)")
    mt_choice = read_input("Choose machine type 1-3 [default: 1 (t2d-standard-16), q=abort]: ").strip()
    if mt_choice.lower() in ("q", "quit", "exit"):
        print(">>> Creation cancelled.")
        time.sleep(1)
        return None
    mt_map = {"1": "t2d-standard-16", "2": "c2d-standard-16", "3": "t2d-standard-8"}
    new_mt = mt_map.get(mt_choice or "1", "t2d-standard-16")

    default_disk = "gcp-general-disk"
    att_choice = read_input("\nAttach data disk? [y/N, default: N, q=abort]: ").strip().lower()
    if att_choice in ("q", "quit", "exit"):
        print(">>> Creation cancelled.")
        time.sleep(1)
        return None
    attach_disk = att_choice in ("y", "yes")

    new_disk = ""
    if attach_disk:
        in_disk = read_input(f"Data disk name [default: {default_disk}, q=abort]: ").strip()
        if in_disk.lower() in ("q", "quit", "exit"):
            print(">>> Creation cancelled.")
            time.sleep(1)
            return None
        new_disk = in_disk or default_disk

    print(f"\n{YELLOW}Ready to create VM:{NC}")
    print(f"  Instance Name: {BOLD}{new_vm}{NC}")
    print(f"  Zone:          {BOLD}{new_zone}{NC}")
    print(f"  Machine Type:  {BOLD}{new_mt}{NC}")
    print(f"  Boot Disk:     {BOLD}30GB Free HDD (pd-standard){NC}")
    if attach_disk:
        print(f"  Data Disk:     {BOLD}{new_disk}{NC}")

    conf = read_input("\nProceed with VM creation? [Y/n, default: Y, q=abort]: ").strip().lower()
    if conf in ("n", "no", "q", "quit", "exit"):
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
    while True:
        clear_screen()
        print_divider()
        print(f"{BOLD}                     DELETE A GCP BUILD VM                       {NC}")
        print_divider()

        if not vms:
            print(f"\n   {YELLOW}No VMs found to delete.{NC}")
            pause()
            return False

        all_vms = sorted(vms, key=lambda v: v["name"])
        current_active = get_active_vm().get("instance", "")
        print(" Available VMs to delete:\n")
        for idx, v in enumerate(all_vms, 1):
            is_active = (v["name"] == current_active)
            active_badge = f" {GREEN}{BOLD}[ACTIVE]{NC}" if is_active else ""
            status_color = GREEN if v["status"] == "RUNNING" else RED
            print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {status_color}{v['status']}{NC}{active_badge}")
            print(f"      Attached disks: {CYAN}{', '.join(v['disks'])}{NC}")

        print(f"\n   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Cancel / Back")
        print_divider()

        choice = read_input(f" Select VM 1-{len(all_vms)}, r or 0/q: ").strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return False
        if choice in ("r", "reload", "refresh"):
            vms = discover_instances(project, refresh=True)
            continue
        try:
            c_idx = int(choice)
            if not (1 <= c_idx <= len(all_vms)):
                return False
        except ValueError:
            return False
        break

    target = all_vms[c_idx - 1]
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
    while True:
        clear_screen()
        print_divider()
        print(f"{BOLD}                   START OR STOP A BUILD VM                      {NC}")
        print_divider()
        if not vms:
            print(f"\n   {YELLOW}No VMs found in project {project}.{NC}")
            print(f"\n   {YELLOW}r){NC} Reload / Refresh")
            print(f"   {RED}0 / q){NC} Cancel / Back")
            print_divider()
            c = read_input(" Select option [r, 0, q]: ").strip().lower()
            if c in ("r", "reload", "refresh"):
                vms = discover_instances(project, refresh=True)
                continue
            return

        all_vms = sorted(vms, key=lambda v: v["name"])
        current_active = get_active_vm().get("instance", "")
        print(" Select VM to toggle power:\n")
        for idx, v in enumerate(all_vms, 1):
            is_active = (v["name"] == current_active)
            active_badge = f" {GREEN}{BOLD}[ACTIVE]{NC}" if is_active else ""
            status_color = GREEN if v["status"] == "RUNNING" else RED
            action_hint = f"{RED}[Will Stop]{NC}" if v["status"] == "RUNNING" else f"{GREEN}[Will Start]{NC}"
            print(f"   {GREEN}{idx}){NC} {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {status_color}{v['status']:<10}{NC} {action_hint}{active_badge}")

        print(f"\n   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Cancel / Back")
        print_divider()
        pwr_num = read_input(f" Enter VM number 1-{len(all_vms)}, r or 0/q [default: 1]: ").strip().lower()
        if not pwr_num:
            pwr_num = "1"
        if pwr_num in ("0", "q", "quit", "exit"):
            return
        if pwr_num in ("r", "reload", "refresh"):
            vms = discover_instances(project, refresh=True)
            continue
        if pwr_num.isdigit() and 1 <= int(pwr_num) <= len(all_vms):
            target_v = all_vms[int(pwr_num) - 1]
            target_dict = {
                "instance": target_v["name"],
                "zone": target_v["zone"],
                "project": project,
                "general_disk": target_v.get("general_disk", "gcp-general-disk"),
            }
            if target_v["status"] == "RUNNING":
                action = read_input(f" Choose action for '{target_v['name']}': 1) Stop  2) Restart [default: 1, q=abort]: ").strip()
                if action == "2":
                    restart_vm(target_dict)
                elif action in ("q", "quit", "exit"):
                    continue
                else:
                    stop_vm(target_dict)
            else:
                start_vm(target_dict)
            pause()
            vms = discover_instances(project, refresh=True)


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

        all_vms = sorted(vms, key=lambda v: v["name"])
        current_active = vm.get("instance", "")

        if count == 0:
            print(f" Build VMs:\n")
            print(f"   {YELLOW}(No VMs currently found in project {project}){NC}\n")
        else:
            print(f" {BOLD}Build VMs ({count}):{NC}")
            for v in all_vms:
                is_active = (v["name"] == current_active)
                status_color = GREEN if v["status"] == "RUNNING" else RED
                active_badge = f" {GREEN}{BOLD}[ACTIVE]{NC}" if is_active else ""
                ip_str = f"IP: {v.get('ip', 'None')}" if v["status"] == "RUNNING" else ""
                disks_str = f"Disks: {', '.join(v['disks'])}" if v.get('disks') else "Disks: none"
                print(f"   • {BOLD}{v['name']:<18}{NC} {v['zone']:<13} {v['machine_type']:<15} {status_color}{v['status']:<9}{NC}{active_badge} {ip_str}".rstrip())
                print(f"     {CYAN}{disks_str}{NC}")
            print()

        print_thin_divider()
        print(f" Actions:")
        print(f" {GREEN}1){NC} Select VM")
        print(f" {CYAN}2){NC} Create VM")
        print(f" {RED}3){NC} Delete VM")
        print(f" {YELLOW}4){NC} Start, Stop or Restart VM")
        print(f" {MAGENTA}5){NC} Google Account")
        print()
        print(f" {BOLD}0 / Enter){NC} Back to Build Menu")
        print(f" {YELLOW}r){NC} Reload / Refresh VMs")
        print(f" {RED}q){NC} Quit")
        print_divider()

        choice = read_input(" Select 1-5, 0, r or q [default: 0]: ").strip().lower()

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
        elif choice in ("r", "reload", "refresh", "refresh-vms"):
            print(f"{CYAN}Refreshing from GCP...{NC}")
            discover_instances(project, refresh=True)
            time.sleep(0.5)
            continue
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
            from cli.tools.auth import submenu_gcloud_auth
            submenu_gcloud_auth()
