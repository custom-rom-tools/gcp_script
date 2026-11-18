import json
import time
from pathlib import Path
from cli.services.vm import vm_exec, is_vm_running, run_gcloud
from cli.services.disk import get_attached_disks, mount_disk
import subprocess

CACHE_FILE = Path.home() / ".gcp_farm" / "artifact_cache.json"
CACHE_EXPIRY = 300 # 5 minutes

def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data.get("timestamp", 0) < CACHE_EXPIRY:
            return data.get("artifacts", {})
    except:
        pass
    return None

def save_cache(artifacts: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"timestamp": time.time(), "artifacts": artifacts}
    CACHE_FILE.write_text(json.dumps(data))

def get_all_artifacts(vm: dict, artifact_type: str) -> dict:
    """Finds all available artifacts using cache, falling back to targeted disk scans."""
    cached = load_cache()
    if cached:
        print(">>> [Status] Using cached artifact list...")
        # Return only relevant artifacts from cache
        # Note: This simple filtering assumes the cache key (path) contains the artifact type or path structure
        return {k: v for k, v in cached.items() if (artifact_type == "kernel" and ("boot.img" in k)) or (artifact_type == "rom" and ".zip" in k) or (artifact_type == "recovery" and "recovery.img" in k)}
    
    artifacts = {}
    search_map = {
        "rom": ["*/out/target/product/*"],
        "recovery": ["*/out/target/product/*"],
        "kernel": ["*/out-kernel/*", "*/out/target/product/*"],
        "log": ["*", "*/out/target/product/*"]
    }
    patterns = {
        "rom": "*.zip",
        "recovery": "recovery.img *.zip",
        "kernel": "boot.img",
        "log": "build.log"
    }

    attached = get_attached_disks(vm)
    data_disks = [d for d in attached if not d.get("boot", False)]
    mount_point = f"/mnt/{artifact_type}"
    
    for disk in data_disks:
        disk_name = disk["name"]
        print(f"\n>>> [Status] Checking disk {disk_name}...", end="", flush=True)
        if not mount_disk(vm, disk_name, mount_point):
            print(f" Failed to mount. Trying next disk...", flush=True)
            continue
            
        paths_to_search = search_map.get(artifact_type, ["out/target/product/*"])
        pattern = patterns.get(artifact_type, '*')
        paths_expr = " -o ".join([f"-path '{mount_point}/{p}'" for p in paths_to_search])
        
        if ' ' in pattern:
            find_names = " -o ".join([f"-name '{sub}'" for sub in pattern.split()])
            find_expr = f"\( {paths_expr} \) \( {find_names} \)"
        else:
            find_expr = f"\( {paths_expr} \) -name '{pattern}'"
            
        cmd = f"find {mount_point} -maxdepth 6 {find_expr} 2>/dev/null"
        output = vm_exec(vm, cmd, check=False).stdout.strip()
        
        if output:
            for line in output.splitlines():
                artifacts[line] = disk_name
                
        vm_exec(vm, f"sudo umount {mount_point}", check=False)
        print(" Done.", flush=True)
        
    save_cache(artifacts)
    return artifacts

def download_artifacts(vm: dict, artifact_type: str = "rom") -> bool:
    if not is_vm_running(vm):
        print(">>> [Status] VM is offline.")
        return False
        
    print(f">>> [Status] Scanning for {artifact_type} artifacts...")
    artifacts_map = get_all_artifacts(vm, artifact_type)
    if not artifacts_map:
        print(f">>> [Status] No {artifact_type} artifacts found.")
        return False

    artifacts = list(artifacts_map.keys())
    print(f">>> [Status] Found {len(artifacts)} {artifact_type} artifacts:")
    for i, path in enumerate(artifacts):
        print(f" {i+1}) {path}")
    
    choice = input(f" Select artifact (1-{len(artifacts)}) or q: ")
    if choice.lower() == 'q':
        return False
        
    try:
        idx = int(choice) - 1
        remote = artifacts[idx]
        disk_name = artifacts_map[remote]
    except (ValueError, IndexError):
        print(">>> [Status] Invalid selection.")
        return False

    # Mount the specific disk needed for download
    mount_point = f"/mnt/{artifact_type}"
    print(f">>> [Status] Mounting {disk_name} for download...", end="", flush=True)
    if not mount_disk(vm, disk_name, mount_point):
        print(" Failed to mount.", flush=True)
        return False
    print(" Done.", flush=True)

    local_dir = Path.home() / "Downloads"
    local_dir.mkdir(parents=True, exist_ok=True)
    local = local_dir / Path(remote).name
    
    print(f">>> [Status] Downloading {remote} to {local}...", end="", flush=True)
    
    # Using --progress to show download status
    # Capture stderr to debug why it fails
    args = ["compute", "scp", "--progress", f"{vm['instance']}:{remote}", str(local), f"--zone={vm['zone']}"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
        
    print(f"\n>>> [Debug] Running: {' '.join(args)}")
    res = subprocess.run(args, capture_output=True, text=True)
    
    # Unmount after download
    vm_exec(vm, f"sudo umount {mount_point}", check=False)
    
    if res.returncode == 0:
        print(" Done.", flush=True)
        print(f">>> [Status] Downloaded: {local}")
        return True
    else:
        print(" Failed.", flush=True)
        print(f">>> [Status] Download failed. Error: {res.stderr}")
        return False
