# GCP Build Farm for Android ROMs & Recoveries

Cloud build automation and management CLI for compiling custom Android ROMs and Recoveries for Realme Atoll devices (`sm7125`, `RMX2061`, `RMX2170`, `RMX3081`).

---

## Architecture & Storage Design

* **Compute Instance (`c2d-standard-16`)**:
  * 16 AMD EPYC Milan vCPUs with large L3 cache per core and 3.7 GHz boost, 64 GB Physical RAM, located in `us-central1-a`.
  * Compute-optimized specifically for high-throughput C/C++ Clang, Soong, and Ninja parallel compilation.
  * Compute billing runs only when the VM is active (~$0.726/hr).
* **Boot Disk (`50 GB pd-standard`)**:
  * OS (Ubuntu 22.04 LTS), toolchains (`openjdk-17-jdk`, `repo`, `ninja`, Python).
  * **Persistent CCache (15 GB)** stored at `~/.ccache` directly on the boot disk, preserved across ROM disk re-creations and version upgrades.
* **ROM & Recovery Storage Disks (`pd-balanced SSD`)**:
  * **ROM/Recovery-Specific Disks**: Dedicated storage (`gcp-rom-a16-disk` at `/mnt/a16`, `gcp-rom-a17-disk` at `/mnt/a17`, `gcp-recovery-disk` at `/mnt/recovery`) auto-mounted at boot by `startup.sh`.
  * **General Shared Disk (`gcp-general-disk`, 500 GB)**: Fits GCP free-tier's 500 GB SSD limit by sharing one disk across ROM versions and Recoveries. It is **not** mounted at boot; instead, when starting a build, `gcp_menu.sh` checks for a ROM/recovery-specific disk first and falls back to `gcp-general-disk`, unmounting any other mountpoint if needed and mounting it at the target's mountpoint (`/mnt/a16`, `/mnt/a17`, or `/mnt/recovery`).
  * **Format Disk Feature**: Wipe & reformat to clean ext4 anytime via **Disk Manager -> Format Disk**, `./gcp_menu.sh format-disk`, or pass `--format` when starting a build.
  * Isolated workspace folders cleanly named (`/mnt/a16/evox`, `/mnt/a16/los`, `/mnt/a16/lunaris`, `/mnt/recovery/ofrp`, `/mnt/recovery/twrp`).
  * **Zero Idle Cost**: Delete secondary disks via menu when not building to pause storage billing.
* **Flavor-Isolated Workspace**:
  * Each ROM and recovery compiles in its own isolated subfolder (`evox`, `los`, `lunaris`, `ofrp`, `twrp`), preventing conflicts between parallel or sequential builds.
* **Memory & Swap**:
  * Pure 64 GB Physical RAM mode with swap disabled by default for maximum compile throughput.
  * On-demand swap can be created on any disk via the Swap Manager if ever needed.

---

## Branch Structure

* **`main`**: Common host automation tools only:
  * `gcp_menu.sh`: Interactive CLI menu and CLI flag dispatcher.
  * `setup_host.sh`: Host provisioning and package installation script.
  * `startup.sh`: VM boot daemon that auto-mounts disks and establishes symlinks.
  * `upload.py`: Cloud uploader supporting SourceForge and Gofile.io.
* **`aosp-16`**: Android 16 ROM build scripts (`evolution_*.sh`, `lineage_*.sh`, `lunaris_*.sh`, `clean.py`, `patch.py`).
* **`aosp-17`**: Android 17 ROM build scripts (`evolution_*.sh`, `lineage_*.sh`, `lunaris_*.sh`, `clean.py`, `patch.py`).
* **`recovery`**: Recovery build scripts (`ofrp_sm7125.sh`, `twrp_sm7125.sh`, `recovery_all.sh`, `clean.py`, `patch.py`).

---

## Quick Start

### 1. Interactive Menu
Launch the management menu:
```bash
./gcp_menu.sh
```

### 2. First-Time Provisioning
Run host provisioning to install all required dependencies, compilers, and ccache:
```bash
./gcp_menu.sh setup
```

Upload your SourceForge SSH key (`~/.ssh/crave_sf`) for automated release publishing:
```bash
./gcp_menu.sh upload-key
```

---

## Command-Line Usage

You can launch builds directly with CLI arguments without entering the menu:

### Build ROM
```bash
# Evolution X 16 for Realme 7 Pro (userdebug by default):
./gcp_menu.sh build --rom evox --ver 16 --dev RMX2170

# Evolution X 16 production user build with SourceForge upload:
./gcp_menu.sh build --rom evox --ver 16 --dev RMX2170 --type user --sf

# LineageOS 17 for Realme 6 Pro:
./gcp_menu.sh build --rom los --ver 17 --dev RMX2061 --type userdebug --sf

# Batch build all devices (RMX2061, RMX2170, RMX3081):
./gcp_menu.sh build --rom evox --ver 16 --dev all --sf

# Format the resolved disk clean before starting a build (useful when switching ROMs on gcp-general-disk):
./gcp_menu.sh build --rom los --ver 17 --dev RMX2170 --format --sf
```

### Build Recovery
```bash
# OrangeFox for sm7125 (eng by default):
./gcp_menu.sh recovery --flavor ofrp --dev sm7125 --sf

# TWRP for sm7125 with userdebug variant:
./gcp_menu.sh recovery --flavor twrp --dev sm7125 --type userdebug --sf

# Batch build both OrangeFox and TWRP (with optional clean disk format first):
./gcp_menu.sh recovery --flavor all --sf --format
```

### VM, Disk & Session Management
```bash
./gcp_menu.sh status               # Check VM running state, machine type, and attached disks
./gcp_menu.sh format-disk general  # Format gcp-general-disk clean as ext4 (accepts disk name or a16/a17/recovery/general)
./gcp_menu.sh create-disk general  # Create & initialize 500GB gcp-general-disk (unmounted until build)
./gcp_menu.sh top          # Launch interactive live process & CPU monitor (htop / top)
./gcp_menu.sh attach       # Attach to live tmux build session
./gcp_menu.sh logs         # View recent compiler output & error scans
./gcp_menu.sh download-log # Download latest build log (.log) directly to Mac
./gcp_menu.sh stop-build   # Cancel/kill active compilation & tmux session
./gcp_menu.sh start        # Start VM (with automated capacity failover)
./gcp_menu.sh stop         # Stop VM
./gcp_menu.sh type         # Interactive machine type switcher (ranked by price)
./gcp_menu.sh type <type>  # Switch machine type in-place (e.g. t2d-standard-16)
./gcp_menu.sh ssh          # Open SSH shell into VM
./gcp_menu.sh info         # View instance specifications and billing rates
```

---

## Machine Types & Auto-Failover Strategy

Custom ROM compilation (AOSP, Kernel, Clang/LLVM, Soong, Ninja) is heavily CPU-bound and cache-sensitive. We exclusively use Google Cloud's **compute-optimized and dedicated-core architectures**:

| Rank | Machine Type | CPU Architecture | Hourly Cost | Characteristics |
| :---: | :--- | :--- | :---: | :--- |
| **1** | **`c2d-standard-16`** *(Default)* | AMD EPYC Milan (Compute-Opt) | **~$0.726 / hr** | **Fastest AOSP Build**: Max L3 cache per core (32MB per CCX) & 3.7 GHz boost for optimal Clang throughput. |
| **2** | **`t2d-standard-16`** | AMD EPYC Milan (1:1 Cores) | **~$0.675 / hr** | **Cheapest 16-Core**: 16 dedicated physical cores with no SMT/hyperthreading sharing. |
| **3** | **`c2-standard-16`** | Intel Xeon Cascade Lake | **~$0.835 / hr** | **Intel Fallback**: High sustained all-core turbo (3.8 GHz) compute pool. |

### Automated In-Place Capacity Recovery
* If Google Cloud reports a temporary cluster shortage (`ZONE_RESOURCE_POOL_EXHAUSTED`) on `c2d-standard-16`, the startup script automatically updates the VM machine type to `t2d-standard-16` in-place (in 2 seconds) and starts the VM immediately.
* If that pool is also full, it cascades to `c2-standard-16`.
* **Zero Disk Disruption**: Persistent disks, mountpoints, ccache, and configurations are 100% preserved.
* **Manual Switcher**: You can view or change machine types anytime via **Option 26 (Machine Type & Failover Manager)** or `./gcp_menu.sh type`.

---

## Cost Optimization & Disk Lifecycle

1. **Auto-Idle Shutdown Watchdog (30-Minute Protection)**:
   * A persistent systemd watchdog (`idle-shutdown.service`) continuously monitors VM activity every 60 seconds.
   * If **no build/sync processes** (`soong`, `ninja`, `ckati`, `repo`, `upload.py`, etc.) are running and **no interactive SSH sessions** are open for **30 minutes**, the VM automatically powers down (`poweroff`).
   * **Runaway Billing Protection**: Completely eliminates unexpected compute charges (~$0.726/hr) if a build finishes, crashes, fails, or if an SSH session drops.
   * Monitor status in real-time via **Option 18 (Check Build Status & Error Logs)** or **Option 28 (Setup & Tools) -> Option 3**.
2. **Post-Build Auto-Shutdown**:
   * When builds are invoked with auto-shutdown enabled, the VM powers off 30 seconds after successful upload.
3. **ROM Disk Management**:
   * Storage is billed while disks exist in GCP (~$1.65/day for 500 GB).
   * Use **Option 24 (Disk Manager)** -> **Detach & DELETE Disk** to remove the disk when you are done.
   * On your next build, `gcp_menu.sh` automatically recreates, attaches, formats, and mounts a fresh disk with zero manual commands.
4. **Artifact Uploads & Build Logs**:
   * Releases upload directly to SourceForge (`Frs`) and/or Gofile.io.
   * Built ROM zip(s) can be downloaded to Mac (`~/Downloads`) via **Option 14**.
   * Recovery artifacts can be downloaded to Mac (`~/Downloads`) via **Option 15**.
   * Active build logs (`.log`) can be downloaded to Mac (`~/Downloads`) via **Option 16** or `./gcp_menu.sh download-log`.
