# GCP Build Farm for Android ROMs, Recoveries & Kernels

Cloud build automation and management CLI for compiling custom Android ROMs,
recoveries and kernels for Realme Atoll devices (`sm7125`, `RMX2061`, `RMX2170`,
`RMX3081`).

The project uses a single modular Python branch: the interactive dashboard,
build pipelines, VM lifecycle and storage helpers all live together under
`cli/` and `scripts/`.

---

## Architecture & Storage Design

* **Compute instance (`c2d-standard-16`)**:
  * 16 AMD EPYC Milan vCPUs, 64 GB physical RAM, `us-central1-a` by default.
  * Compute-optimised for Clang/Soong/Ninja throughput; billing only while RUNNING.
* **Boot disk (`30-50 GB pd-standard`)**:
  * Ubuntu 22.04 LTS, toolchains, `repo`, Ninja, Python and a persistent ccache
    (5 GB cap) on the boot disk.
* **Build data disks (`pd-balanced SSD`)**:
  * `gcp-rom-disk` (500 GB) -> `/mnt/rom` (all ROM & Kernel builds)
  * `gcp-recovery-disk` (300 GB) -> `/mnt/recovery` (OrangeFox, TWRP)
  * `gcp-general-disk` (500 GB) -> shared fallback for any build. Its guest
    device name is `rom-general`; it is deliberately left unmounted at boot and
    mounted on demand at the target mountpoint.
  * Workspaces stay isolated: `/mnt/rom/evox`, `/mnt/rom/los`,
    `/mnt/rom/lunaris`, `/mnt/recovery/ofrp`, `/mnt/recovery/twrp`.
* **Memory & swap**:
  * Pure 64 GB RAM mode by default.
  * On-demand swapfiles can be created on `/mnt/rom`,
    `/mnt/recovery` or the boot disk.

### Remote execution & caching

The CLI talks to the VM through direct `ssh`/`scp` with
`ControlMaster`/`ControlPersist`, falling back to `gcloud compute ssh` only when
the local Google key is unavailable. VM state/IP lookups and GCP API results are
cached for a few seconds, so menu redraws do not repeatedly hit the API.

Builds run inside the remote `rom-build` tmux session. The runner writes
`build.status`, `build.log` and `.gcp_build_target` at the root of the active
mount, and deploys the 30-minute idle-shutdown systemd watchdog before launch.

---

## Quick Start

```bash
./gcp_menu.sh            # Interactive dashboard (VM selector first)
./gcp_menu.sh setup      # First-time host provisioning on the VM
./gcp_menu.sh upload-key # Upload ~/.ssh/crave_sf for SourceForge uploads
```

The startup selector discovers all VMs in the project and lets you choose the
active build target, create a VM, delete a VM, or start/stop any VM.

---

## Interactive Dashboard

```
[BUILD TARGETS]                 [DOWNLOADS TO MAC]
 1) Android 16 (ROMs & Kernel)   5) Download ROM (.zip)
 2) Android 17 (ROMs & Kernel)   6) Download Recovery (.img/.zip)
 3) Recovery (OFRP & TWRP)       7) Download Kernel (.img)
 4) Kernel (A16 & A17 boot.img)  8) Download Build Log (.log)

[MONITOR]                       [VM MANAGEMENT]
 9) Live Build Progress (tmux)  13) VM Status & Info
10) Build Status & Error Logs   14) Start VM
11) Live System Monitor         15) Stop VM
12) Stop Active Build           16) Restart VM
                                17) Disk Manager
[TOOLS]                         18) Swap Manager
21) Setup & Tools               19) Machine Type Switcher
22) Upload Links                20) SSH Console
23) Switch / Manage VMs
 r) Reload / Refresh Menu
 q) Quit
```

Device menus mirror the original shell menu:
* ROM: `RMX2170`, `RMX2061`, `RMX3081`, or all-devices batch.
* Recovery: unified `sm7125` first, then each device, then the batch.
* Kernel: Android 16/17 devices via the Evolution X ROM workspace.

Interactive builds ask for the variant, SourceForge/Gofile upload and shutdown
behaviour. Disk formatting is a CLI-only option (`--format`); the general disk
prompts before wiping existing data when a different target is detected.

---

## Command-Line Usage

### Build ROM
```bash
# Defaults: evox / 16 / RMX2170 (userdebug)
./gcp_menu.sh build

# Evolution X 16, Realme 7 Pro, SourceForge upload
./gcp_menu.sh build --rom evox --ver 16 --dev RMX2170 --sf

# LineageOS 17, Realme 6 Pro, user build
./gcp_menu.sh build --rom los --ver 17 --dev RMX2061 --type user

# All devices batch, clean disk first
./gcp_menu.sh build --rom evox --ver 16 --dev all --format

# Keep the VM running after the build (default CLI behaviour is shutdown)
./gcp_menu.sh build --rom evox --dev RMX2170 --keep-alive
```

### Build Recovery
```bash
# Defaults: ofrp / sm7125 (eng)
./gcp_menu.sh recovery

# TWRP for a single device
./gcp_menu.sh recovery --flavor twrp --dev RMX2170 --type userdebug

# OrangeFox + TWRP batch for all devices
./gcp_menu.sh recovery --flavor all --dev all --format
```

### Build Kernel
```bash
# Defaults: Android 16 / RMX2170 / userdebug -> boot.img
./gcp_menu.sh kernel

# Android 17 kernel for Realme 8 Pro, upload, clean disk first
./gcp_menu.sh kernel --ver 17 --dev RMX3081 --sf --format

# Keep the VM online for debugging
./gcp_menu.sh kernel --ver 16 --dev RMX2061 --keep-alive
```

The kernel build reuses the Evolution X ROM workspace and runs
`m bootimage` after lunch, so it shares the same sync/clean/upload pipeline
instead of a fragile standalone kernel checkout. Artifacts are discovered as
`boot.img` and uploaded under `DEVICE/Kernel/<version>`.

### Downloads
```bash
./gcp_menu.sh download-rom              # Interactive file picker
./gcp_menu.sh download-recovery
./gcp_menu.sh download-kernel
./gcp_menu.sh download-log

./gcp_menu.sh download-rom --all        # Download every matching artifact
./gcp_menu.sh download-kernel --pick 1,3
```

ROM, recovery and kernel downloads only scan each workspace's
`out/target/product/<device>` output tree, so unrelated archives on the data
disks are ignored. In the interactive menu you can pick individual files
(`1,3`), a range (`1-3`), all files (Enter/`all`), or cancel (`q`). All files
land in `~/Downloads`. Log downloads also create a `_clean.log` copy with ANSI
escapes and carriage returns removed.

### VM, Disk, Swap & Session Management
```bash
./gcp_menu.sh status                  # VM state, IP, machine type, zone
./gcp_menu.sh start | stop            # Power lifecycle with capacity failover
./gcp_menu.sh ssh                     # Interactive SSH shell
./gcp_menu.sh tmux | attach           # Attach to the live build session
./gcp_menu.sh htop | top | net        # Live process / network monitor
./gcp_menu.sh vms                     # Switch active VM
./gcp_menu.sh links                   # Show upload links (works while stopped)
./gcp_menu.sh info                    # Farm specifications and rates

./gcp_menu.sh create-disk general 500
./gcp_menu.sh delete-disk general
./gcp_menu.sh attach-disk | detach-disk [name]
./gcp_menu.sh format-disk general     # accepts rom/recovery/general or full name
./gcp_menu.sh swap                    # Interactive swap manager
./gcp_menu.sh swap-create 16 /mnt/rom/swapfile
./gcp_menu.sh swap-off

./gcp_menu.sh logs                    # Build status dashboard
./gcp_menu.sh log-errors              # Scan newest log for FAILED/error lines
./gcp_menu.sh stop-build              # Kill the runner and compiler processes
./gcp_menu.sh machine-type [type]     # Switch machine type in place
```

---

## Machine Types & Auto-Failover

| Rank | Machine Type | CPU Architecture | Hourly Cost | Notes |
| :---: | :--- | :--- | :---: | :--- |
| **1** | **`c2d-standard-16`** *(Default)* | AMD EPYC Milan (Compute-Opt) | **~$0.726 / hr** | Fastest AOSP build: large L3 cache and high boost. |
| **2** | **`t2d-standard-16`** | AMD EPYC Milan (1:1 Cores) | **~$0.675 / hr** | Cheapest 16-core option. |
| **3** | **`c2-standard-16`** | Intel Xeon Cascade Lake | **~$0.835 / hr** | Intel fallback pool. |

If GCP reports `ZONE_RESOURCE_POOL_EXHAUSTED`, the start flow changes the
machine type in place and retries through `c2d -> t2d -> c2`. Persistent disks,
mountpoints, ccache and workspaces are preserved.

---

## Cost Protection

1. **30-minute idle watchdog**:
   * `idle-shutdown.service` runs `/usr/local/bin/idle_watchdog.sh` every minute.
   * If no build processes and no interactive SSH users are active for 30
     minutes, the VM powers off.
   * Deploy or inspect it from **Setup & Tools -> 3**, or CLI
     `./gcp_menu.sh watchdog`.
2. **Post-build shutdown**:
   * CLI builds shut down on success by default; `--keep-alive` or
     `--shutdown false` keeps the VM running.
   * Failed builds stay online for debugging and rely on the idle watchdog.
3. **Disk lifecycle**:
   * Storage is billed while disks exist. Delete unused disks from the Disk
     Manager (option 8) or `./gcp_menu.sh delete-disk`.
   * Missing disks are recreated/attached automatically when a build starts.

---

## Repository Layout

```
cli/
  main.py             Dashboard + CLI dispatcher
  base.py             ANSI UI helpers and terminal restoration
  helper.py           Device/variant/upload prompts
  builds/             ROM, recovery and kernel submenus
  ops/                Download, monitor and upload-link menus
  storage/            Disk and swap menus
  tools/              Setup, authentication and SSH console menus
  services/           VM/SSH, disk, runner, monitor, download, link,
                      swap and tool backends
  cluster/vm.py       VM discovery, selection, creation and deletion
scripts/
  build.py            Unified ROM / recovery / kernel build engine
  clean.py            Workspace clean implementation
  patch.py            Patch hook
  upload.py           SourceForge / Gofile / Filebin uploader
configs/
  rom.json            ROM versions, devices and manifests
  recovery.json       OrangeFox/TWRP configuration
  kernel.json         Android 16/17 kernel configuration
  vm_preset.json      Disk sizes, device names and VM defaults
```

Upload history is merged from `~/.gcp_upload_links.log`, the VM's
`~/.upload_links.log` and the GCP `upload-links` metadata entry, so links remain
visible even when the VM is stopped.
