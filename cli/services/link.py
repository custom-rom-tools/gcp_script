from cli.services.vm import vm_exec


def sync_links_from_vm(vm: dict) -> list[str]:
    res = vm_exec(vm, "cat ~/.upload_links.log 2>/dev/null || true", check=False)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]
