"""Instance backends for the three Proxmox-style VPS types.

* ``lxc``    – system container (Incus/LXD), nesting is ``security.nesting``.
* ``kvm``    – hardware accelerated virtual machine (Incus ``--vm``, needs /dev/kvm).
* ``nonkvm`` – software emulated QEMU (TCG) virtual machine for hosts without
  hardware virtualisation. Managed by ``scripts/omega-nonkvm.sh``.

Everything is name based, so the panel's existing start/stop/destroy helpers keep
working for containers and Incus VMs. Nesting is applied for all three types.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
NONKVM_SCRIPT = BASE_DIR / "scripts" / "omega-nonkvm.sh"

TYPES: dict[str, dict] = {
    "lxc": {
        "label": "LXC container",
        "accel": "container",
        "description": "Lightweight system container. Fastest, shares the host kernel.",
        "nesting_hint": "Allows Docker/podman (nested containers) inside the VPS.",
    },
    "kvm": {
        "label": "KVM virtual machine",
        "accel": "kvm",
        "description": "Full hardware-accelerated VM (QEMU/KVM). Requires /dev/kvm.",
        "nesting_hint": "Exposes VT-x/AMD-V to the guest so it can run its own VMs.",
    },
    "nonkvm": {
        "label": "Non-KVM VM (QEMU TCG)",
        "accel": "tcg",
        "description": "Software-emulated VM for hosts without KVM. Slower, but a real VM.",
        "nesting_hint": "Emulates VMX/SVM where the QEMU build supports it (best effort).",
    },
}

DEFAULT_TYPE = "lxc"


class VpsTypeError(RuntimeError):
    pass


def normalise_type(vps_type: str | None) -> str:
    value = str(vps_type or "").strip().lower()
    if value in {"vm", "virtual-machine", "virtualmachine", "qemu", "kvm-vm"}:
        return "kvm"
    if value in {"non-kvm", "non_kvm", "tcg", "emulated"}:
        return "nonkvm"
    if value in {"container", "ct", "incus", "lxd"}:
        return "lxc"
    return value if value in TYPES else DEFAULT_TYPE


def type_meta(vps_type: str | None) -> dict:
    return TYPES[normalise_type(vps_type)]


def cli() -> str | None:
    return shutil.which("incus") or shutil.which("lxc")


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return proc.returncode, (proc.stdout or "").strip()
    except FileNotFoundError:
        return 127, f"Missing command: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout}s: {' '.join(cmd)}"


def capabilities() -> dict:
    """What this host can actually create right now."""
    binary = cli()
    kvm_device = os.path.exists("/dev/kvm")
    qemu = shutil.which("qemu-system-x86_64") or shutil.which("qemu-system-x86")
    cloud_localds = shutil.which("cloud-localds")
    qemu_img = shutil.which("qemu-img")
    nonkvm_ok = bool(qemu and qemu_img and cloud_localds)
    incus_version = ""
    if binary:
        rc, out = _run([binary, "--version"], timeout=15)
        if rc == 0:
            incus_version = out.splitlines()[0][:120] if out else ""
    return {
        "incus": bool(binary),
        "incus_binary": binary or "",
        "incus_version": incus_version,
        "kvm_device": kvm_device,
        "qemu": qemu or "",
        "qemu_img": qemu_img or "",
        "cloud_localds": cloud_localds or "",
        "lxc": bool(binary),
        "kvm": bool(binary and kvm_device),
        "nonkvm": bool(binary and nonkvm_ok),
        "types": {
            "lxc": bool(binary),
            "kvm": bool(binary and kvm_device),
            "nonkvm": bool(binary and nonkvm_ok),
        },
    }


def require_supported(vps_type: str) -> dict:
    vps_type = normalise_type(vps_type)
    caps = capabilities()
    if not caps["incus"]:
        raise VpsTypeError("Incus/LXD is not installed on this node. Run setup.sh first.")
    if vps_type == "kvm" and not caps["kvm"]:
        raise VpsTypeError(
            "/dev/kvm is not available on this node, so KVM virtual machines cannot start here. "
            "Use the LXC type, the Non-KVM (QEMU TCG) type, or enable nested hardware virtualisation."
        )
    if vps_type == "nonkvm" and not caps["nonkvm"]:
        missing = [name for name, key in (("qemu-system-x86_64", "qemu"),
                                          ("qemu-img", "qemu_img"),
                                          ("cloud-localds", "cloud_localds")) if not caps[key]]
        raise VpsTypeError(
            "Non-KVM VMs need " + ", ".join(missing) +
            ". Install them on the node (apt-get install qemu-system-x86 qemu-utils cloud-image-utils)."
        )
    return caps


def _run_nonkvm(actions: list[str], timeout: int = 900) -> tuple[int, str]:
    if not NONKVM_SCRIPT.exists():
        return 127, f"Missing helper script: {NONKVM_SCRIPT}"
    return _run(["bash", str(NONKVM_SCRIPT), *actions], timeout=timeout)


def parse_output(output: str) -> dict:
    values: dict[str, str] = {}
    for line in (output or "").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key.replace("_", "").isalnum():
                values[key] = value.strip()
    return values


def vm_image_ref(alias: str) -> str:
    """Return a VM-capable image reference.

    The panel's OS aliases (for example ``22.04`` or ``debian/13``) resolve to
    locally cached *container* images, and Incus refuses to launch those with
    ``--vm`` ("Asked for a VM but image is of type container"). For VMs we
    therefore always pull from the ``images:`` remote, which ships VM variants.
    """
    value = str(alias or "").strip()
    if not value:
        return "images:ubuntu/22.04"
    if ":" in value:
        return value
    if re.fullmatch(r"\d{2}\.\d{2}", value):
        return f"images:ubuntu/{value}"
    return f"images:{value}"


# ---------------------------------------------------------------------------
# Host capabilities the panel used to hardcode
#
# The old code always launched with ``-d root,size=20GiB`` and implicitly
# expected a storage pool called ``default``. On hosts where Incus was
# initialised with another pool name (or with the ``dir`` driver, which has no
# per-volume size) every container create failed or sat waiting for an image
# that was never pulled. These helpers discover the real host layout so the
# panel works on any Incus install.
# ---------------------------------------------------------------------------
_IMAGE_ALIAS_CACHE: dict[str, tuple[float, str]] = {}


def _incus(args: list[str], timeout: int = 60) -> tuple[int, str]:
    binary = cli()
    if not binary:
        return 127, "Incus/LXD CLI is not installed on this node."
    return _run([binary, *args], timeout=timeout)


def storage_pool() -> tuple[str, str]:
    """Return ``(pool_name, driver)`` for the pool instances are created on."""
    code, output = _incus(["storage", "list", "--format", "csv", "-c", "nd"], timeout=20)
    pools: list[tuple[str, str]] = []
    if code == 0:
        for line in output.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if not parts or not parts[0] or parts[0].lower() == "name":
                continue
            driver = parts[1].lower() if len(parts) > 1 else ""
            pools.append((parts[0], driver))
    if not pools:
        return "default", ""
    for preferred in ("default", "local", "incus", "pool1", "data"):
        for name, driver in pools:
            if name == preferred:
                return name, driver
    return pools[0]


def pool_supports_size(driver: str) -> bool:
    """``dir``/``cephfs`` pools cannot accept a per-instance disk size."""
    return str(driver or "").lower() not in {"dir", "cephfs", "ceph-object"}


def default_profile_exists() -> bool:
    code, _ = _incus(["profile", "show", "default"], timeout=15)
    return code == 0


def _local_image_aliases() -> set[str]:
    code, output = _incus(["image", "list", "--format", "csv", "-c", "l"], timeout=30)
    if code != 0:
        return set()
    return {line.strip() for line in output.splitlines() if line.strip()}


def container_image_ref(alias: str) -> str:
    """Return a reference Incus can launch for a *container*.

    A locally cached alias is preferred (no download); otherwise the image is
    pulled from the upstream ``images:`` remote so a fresh host is not stuck on
    "Image not found".
    """
    value = str(alias or "").strip() or "22.04"
    if ":" in value:
        return value
    cached = _IMAGE_ALIAS_CACHE.get(value)
    if cached and (time.time() - cached[0]) < 300:
        return cached[1]
    aliases = _local_image_aliases()
    if value in aliases:
        _IMAGE_ALIAS_CACHE[value] = (time.time(), value)
        return value
    if "/" not in value and re.fullmatch(r"\d{2}\.\d{2}", value):
        value = f"ubuntu/{value}"
    ref = f"images:{value}"
    _IMAGE_ALIAS_CACHE[value] = (time.time(), ref)
    return ref


def ensure_image(image_ref: str, timeout: int = 900) -> tuple[bool, str]:
    """Make sure a launchable image exists locally; pull it when missing.

    Returns ``(ok, output)``. Pulling here (instead of inside ``incus launch``)
    lets the panel report real progress to the user and distinguishes a slow
    download from a broken image name.
    """
    binary = cli()
    if not binary:
        return False, "Incus/LXD CLI is not installed on this node."
    ref = str(image_ref or "").strip()
    if not ref:
        return False, "No image reference was given."
    if not ref.startswith("images:") and ":" not in ref:
        # Local alias: nothing to pull, launch resolves it.
        return True, ref
    code, output = _incus(["image", "show", ref], timeout=60)
    if code == 0:
        return True, ref
    code, output = _incus(["image", "copy", ref, "local:"], timeout=timeout)
    if code == 0:
        return True, output[-400:]
    return False, output[-800:]


def status(name: str, vps_type: str = DEFAULT_TYPE) -> dict:
    """Live state of an instance, whatever backend it lives on.

    ``state`` is one of ``running``, ``stopped``, ``not_found`` or ``unknown``
    so callers can render the same badge for LXC, KVM and non-KVM VPSs.
    """
    vps_type = normalise_type(vps_type)
    if vps_type == "nonkvm":
        code, output = _run_nonkvm(["status", "--name", name], timeout=45)
        info = parse_output(output)
        state = str(info.get("OMEGA_STATUS") or "").strip().lower()
        if state in {"running", "stopped", "not_found"}:
            return {"state": state, "ip": info.get("OMEGA_SSH_HOST", "127.0.0.1"),
                    "ssh_port": info.get("OMEGA_SSH_PORT", ""), "raw": output[-600:]}
        lowered = (output or "").lower()
        if "does not exist" in lowered or "not exist" in lowered:
            return {"state": "not_found", "ip": "", "ssh_port": "", "raw": output[-600:]}
        return {"state": "unknown", "ip": "", "ssh_port": "", "raw": output[-600:]}
    code, output = _incus(["info", name], timeout=20)
    if code != 0:
        lowered = (output or "").lower()
        state = "not_found" if ("not found" in lowered or "does not exist" in lowered) else "unknown"
        return {"state": state, "ip": "", "ssh_port": "", "raw": output[-600:]}
    running = "status: running" in output.lower()
    return {"state": "running" if running else "stopped", "ip": instance_ip(name, vps_type),
            "ssh_port": "", "raw": output[-600:]}


def create_instance(name: str, image_alias: str, vps_type: str = DEFAULT_TYPE,
                    nesting: bool = False, vcpus: int = 2, memory_mb: int = 2048,
                    disk_gb: int = 20, image_path: str = "") -> dict:
    """Create an instance and return {'name', 'vps_type', 'nesting', ...}."""
    vps_type = normalise_type(vps_type)
    require_supported(vps_type)
    nesting_value = "true" if nesting else "false"

    if vps_type == "nonkvm":
        args = ["create", "--name", name, "--alias", str(image_alias or "ubuntu-22.04"),
                "--cpu", str(int(vcpus)), "--ram-mb", str(int(memory_mb)),
                "--disk-gb", str(int(disk_gb)), "--nesting", ("on" if nesting else "off")]
        if image_path:
            args += ["--base-image", image_path]
        code, output = _run_nonkvm(args)
        if code != 0:
            raise VpsTypeError(f"Non-KVM VM creation failed: {output[-1200:]}")
        info = parse_output(output)
        ssh_password = info.get("OMEGA_SSH_PASSWORD", "")
        return {"name": name, "vps_type": vps_type, "nesting": bool(nesting),
                "ssh_host": info.get("OMEGA_SSH_HOST", "127.0.0.1"),
                "ssh_port": info.get("OMEGA_SSH_PORT", ""),
                "ssh_user": "root", "ssh_password": ssh_password,
                "raw": output[-2000:]}

    binary = cli()
    if not binary:
        raise VpsTypeError("Incus/LXD CLI is not installed on this node.")
    if not default_profile_exists():
        raise VpsTypeError(
            "Incus is installed but not initialised (the 'default' profile is missing), "
            "or the panel service account cannot access the Incus socket. Run "
            "'sudo incus admin init --auto' and add the service user to the incus-admin "
            "group, then restart the panel."
        )
    pool_name, pool_driver = storage_pool()
    image_ref = vm_image_ref(image_alias) if vps_type == "kvm" else container_image_ref(image_alias)
    ok, image_output = ensure_image(image_ref)
    if not ok:
        raise VpsTypeError(
            f"Could not prepare the image '{image_ref}': {image_output}"
        )
    args = [binary, "launch", image_ref, name]
    if vps_type == "kvm":
        args.append("--vm")
    if pool_name and pool_name != "default":
        args += ["-s", pool_name]
    args += [
        "-c", f"limits.cpu={int(vcpus)}",
        "-c", f"limits.memory={int(memory_mb)}MiB",
        "-c", f"security.nesting={nesting_value}",
    ]
    if vps_type == "lxc":
        # Matches the behaviour of the existing container creation path.
        args += ["-c", "security.privileged=true"]
    if pool_supports_size(pool_driver):
        args += ["-d", f"root,size={int(disk_gb)}GiB"]
    code, output = _run(args, timeout=900)
    lowered = output.lower()
    if code != 0 and "-d" in args and "root,size=" in " ".join(args) and \
            "size" in lowered and ("not support" in lowered or "custom" in lowered):
        # Some storage drivers reject a per-instance root size: retry without it.
        index = args.index("-d")
        args = args[:index] + args[index + 2:]
        code, output = _run(args, timeout=900)
        lowered = output.lower()
    if code != 0 and image_ref != str(image_alias) and ("not found" in lowered or "no such image" in lowered):
        # The translated remote image does not exist; fall back to the original alias.
        _run([binary, "delete", name, "--force"], timeout=60)
        args[2] = str(image_alias)
        code, output = _run(args, timeout=900)
    if code != 0 and "exists" in output.lower():
        # A previous switch can leave the old instance (or a half-created one)
        # behind; remove it and retry the launch once.
        _run([binary, "delete", name, "--force"], timeout=300)
        code, output = _run(args, timeout=900)
    if code != 0:
        # A failed launch can leave a half-created instance behind.
        _run([binary, "delete", name, "--force"], timeout=60)
        raise VpsTypeError(f"Instance creation failed ({vps_type}): {output[-1200:]}")
    # Some images ignore the launch -c flag; make the nesting state explicit.
    set_nesting(name, vps_type, bool(nesting))
    return {"name": name, "vps_type": vps_type, "nesting": bool(nesting), "raw": output[-2000:]}


def set_nesting(name: str, vps_type: str, enabled: bool) -> tuple[bool, str]:
    vps_type = normalise_type(vps_type)
    value = "true" if enabled else "false"
    if vps_type == "nonkvm":
        code, output = _run_nonkvm(["nesting", "--name", name, "--state", ("on" if enabled else "off")])
        return code == 0, output[-800:]
    binary = cli()
    if not binary:
        return False, "Incus/LXD CLI is not installed on this node."
    code, output = _run([binary, "config", "set", name, "security.nesting", value], timeout=60)
    if code != 0:
        return False, output[-800:] or f"Could not set security.nesting={value}"
    rc, current = _run([binary, "config", "get", name, "security.nesting"], timeout=30)
    if rc == 0 and current:
        return current.strip().lower() == value, current.strip()
    return True, value


def get_nesting(name: str, vps_type: str) -> bool:
    vps_type = normalise_type(vps_type)
    if vps_type == "nonkvm":
        code, output = _run_nonkvm(["nesting", "--name", name, "--show"], timeout=30)
        return code == 0 and "on" in output.lower()
    binary = cli()
    if not binary:
        return False
    code, output = _run([binary, "config", "get", name, "security.nesting"], timeout=30)
    return code == 0 and output.strip().lower() == "true"


def exists(name: str, vps_type: str = DEFAULT_TYPE) -> bool:
    if normalise_type(vps_type) == "nonkvm":
        code, _ = _run_nonkvm(["status", "--name", name], timeout=30)
        return code == 0
    binary = cli()
    if not binary:
        return False
    code, _ = _run([binary, "info", name], timeout=30)
    return code == 0


def action(name: str, vps_type: str, operation: str) -> tuple[bool, str]:
    vps_type = normalise_type(vps_type)
    if operation not in {"start", "stop", "restart"}:
        raise ValueError("Unsupported action")
    if vps_type == "nonkvm":
        if operation == "restart":
            # The QEMU helper only knows start/stop; a restart is both.
            stop_code, stop_out = _run_nonkvm(["stop", "--name", name])
            start_code, start_out = _run_nonkvm(["start", "--name", name])
            return start_code == 0, (start_out or stop_out)[-800:]
        code, output = _run_nonkvm([operation, "--name", name])
        return code == 0, output[-800:]
    binary = cli()
    if not binary:
        return False, "Incus/LXD CLI is not installed on this node."
    args = [binary, operation, name]
    if operation in {"stop", "restart"}:
        args.append("--force")
    code, output = _run(args, timeout=300)
    return code == 0, output[-800:]


def remove(name: str, vps_type: str) -> tuple[bool, str]:
    if normalise_type(vps_type) == "nonkvm":
        code, output = _run_nonkvm(["destroy", "--name", name])
        return code == 0, output[-800:]
    binary = cli()
    if not binary:
        return False, "Incus/LXD CLI is not installed on this node."
    code, output = _run([binary, "delete", name, "--force"], timeout=300)
    lowered = output.lower()
    if code != 0 and ("not found" in lowered or "does not exist" in lowered or "no such" in lowered):
        # Already gone (for example after an earlier failed backend switch).
        return True, "Instance was already removed."
    return code == 0, output[-800:]


def kvm_access_state(name: str, vps_type: str = DEFAULT_TYPE) -> bool:
    """True when /dev/kvm is passed into the instance as a unix-char device."""
    if normalise_type(vps_type) == "nonkvm":
        return False
    binary = cli()
    if not binary:
        return False
    code, output = _run([binary, "config", "device", "list", name], timeout=30)
    return code == 0 and "kvm" in output.lower() and "/dev/kvm" in output


def kvm_access(name: str, vps_type: str, enabled: bool) -> tuple[bool, str]:
    """Attach or detach /dev/kvm inside the instance (KVM access for nested VMs)."""
    vps_type = normalise_type(vps_type)
    if vps_type == "nonkvm":
        return False, "KVM access is not available on the non-KVM backend."
    binary = cli()
    if not binary:
        return False, "Incus/LXD CLI is not installed on this node."
    if enabled:
        code, output = _run([binary, "config", "device", "add", name, "kvm",
                             "unix-char", "path=/dev/kvm"], timeout=60)
        if code != 0 and "already exists" not in output.lower():
            return False, output[-800:] or "Could not attach /dev/kvm"
        return True, "KVM access enabled"
    code, output = _run([binary, "config", "device", "remove", name, "kvm"], timeout=60)
    if code != 0 and ("not found" in output.lower() or "does not exist" in output.lower()):
        return True, "KVM access was already disabled"
    return code == 0, (output[-800:] or "KVM access disabled")


def instance_ip(name: str, vps_type: str) -> str:
    if normalise_type(vps_type) == "nonkvm":
        code, output = _run_nonkvm(["ip", "--name", name], timeout=30)
        value = output.strip().splitlines()[-1].strip() if (code == 0 and output.strip()) else ""
        if value in {"", "0.0.0.0", "::", "[::]"}:
            return "127.0.0.1"
        return value
    binary = cli()
    if not binary:
        return ""
    code, output = _run([binary, "list", name, "--format", "csv", "-c", "4"], timeout=30)
    if code != 0:
        return ""
    for token in (output.replace("(", "").replace(")", "")).split():
        if token.count(".") == 3 and all(part.isdigit() for part in token.split(".")):
            return token
    return ""
