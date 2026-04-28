"""
VM Provisioner — runs on the HOST.

Automates the one-time VM setup:
  1. Start VM
  2. Copy action_server.py into the VM
  3. Install Python dependencies inside the VM
  4. Register action_server.py as a Windows startup task (Task Scheduler)
  5. Detect VM IP and write VM_MODE / VM_SERVER_URL into .env
  6. Take the "base" snapshot

Prerequisites (the only manual steps):
  - VMware Workstation Pro installed, vmrun.exe on PATH
  - VM already has Windows + Python 3.10+ installed
  - Feishu is installed and you are logged in
  - VMware Tools is installed in the guest (required for runProgramInGuest / getGuestIPAddress)

Usage:
    python vm/provision.py --vmx "C:/VMs/LarkCUA/LarkCUA.vmx" --user Administrator --password yourpass
"""

import argparse
import subprocess
import time
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"

GUEST_WORK_DIR = "C:\\LarkCUA"
GUEST_SERVER_SCRIPT = f"{GUEST_WORK_DIR}\\action_server.py"
GUEST_PYTHON = "C:\\Users\\kaich\\AppData\\Local\\Programs\\Python\\Python313\\python.exe"
TASK_NAME = "LarkCUAActionServer"
SERVER_PORT = 8765
DEPS = "flask pyautogui mss pillow pyperclip"


# ── vmrun helpers ──────────────────────────────────────────────────────────────

def vmrun(vmx: str, *args: str, vm_password: str = "", capture_output: bool = False) -> str:
    # Correct vmrun syntax: vmrun [flags] <subcommand> <vmx> [extra-args]
    vp = ["-vp", vm_password] if vm_password else []
    subcommand, extra = args[0], args[1:]
    cmd = ["vmrun", "-T", "ws", *vp, subcommand, vmx, *extra]
    print(f"  $ vmrun -T ws {' '.join(args)} <vmx>")
    result = subprocess.run(cmd, check=True, capture_output=capture_output, text=True)
    return result.stdout.strip() if capture_output else ""


def vmrun_guest(vmx: str, user: str, password: str, program: str, *args: str,
                vm_password: str = "", wait: bool = True) -> None:
    """Run a program inside the guest (requires VMware Tools)."""
    vp = ["-vp", vm_password] if vm_password else []
    wait_flag = ["-noWait"] if not wait else []
    cmd = [
        "vmrun", "-T", "ws",
        *vp,
        "-gu", user, "-gp", password,
        "runProgramInGuest", vmx,
        *wait_flag,
        program, *args,
    ]
    print(f"  $ vmrun runProgramInGuest ... {program} {' '.join(args)}")
    subprocess.run(cmd, check=True)


def copy_to_guest(vmx: str, user: str, password: str,
                  host_path: str, guest_path: str,
                  vm_password: str = "") -> None:
    vp = ["-vp", vm_password] if vm_password else []
    cmd = [
        "vmrun", "-T", "ws",
        *vp,
        "-gu", user, "-gp", password,
        "copyFileFromHostToGuest", vmx,
        host_path, guest_path,
    ]
    print(f"  $ vmrun copyFileFromHostToGuest ... {host_path} → {guest_path}")
    subprocess.run(cmd, check=True)


def get_vm_ip(vmx: str, vm_password: str = "") -> str:
    vp = ["-vp", vm_password] if vm_password else []
    cmd = ["vmrun", "-T", "ws", *vp, "getGuestIPAddress", vmx, "-wait"]
    print("  $ vmrun -T ws getGuestIPAddress <vmx> -wait")
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    ip = result.stdout.strip()
    if not re.match(r"\d+\.\d+\.\d+\.\d+", ip):
        raise RuntimeError(f"Could not determine VM IP address, got: {ip!r}")
    return ip


# ── provision steps ────────────────────────────────────────────────────────────

def step_start_vm(vmx: str, vm_password: str = "") -> None:
    print("\n[1/6] Starting VM...")
    vmrun(vmx, "start", vm_password=vm_password)
    print("      Waiting 30s for Windows to boot...")
    time.sleep(30)


def step_copy_server(vmx: str, user: str, password: str, vm_password: str = "") -> None:
    print("\n[2/6] Creating guest work directory and copying action_server.py...")
    vmrun_guest(vmx, user, password,
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "-Command",
                f"New-Item -ItemType Directory -Force -Path '{GUEST_WORK_DIR}' | Out-Null",
                vm_password=vm_password)
    host_script = str(Path(__file__).parent / "action_server.py")
    copy_to_guest(vmx, user, password, host_script, GUEST_SERVER_SCRIPT,
                  vm_password=vm_password)


def step_install_deps(vmx: str, user: str, password: str, vm_password: str = "") -> None:
    print("\n[3/6] Installing Python dependencies in VM...")
    vmrun_guest(vmx, user, password,
                GUEST_PYTHON, "-m", "pip", "install", "--quiet", *DEPS.split(),
                vm_password=vm_password)


def step_register_startup(vmx: str, user: str, password: str, vm_password: str = "") -> None:
    print("\n[4/6] Registering action server as Windows startup task...")
    ps_cmd = (
        f"$a = New-ScheduledTaskAction -Execute '{GUEST_PYTHON}' -Argument '{GUEST_SERVER_SCRIPT}';"
        f"$t = New-ScheduledTaskTrigger -AtLogOn;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -RunLevel Highest -Force"
    )
    vmrun_guest(vmx, user, password,
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "-Command", ps_cmd,
                vm_password=vm_password)
    vmrun_guest(vmx, user, password,
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "-Command", f"Start-ScheduledTask -TaskName '{TASK_NAME}'",
                vm_password=vm_password, wait=False)
    print("      Waiting 5s for server to start...")
    time.sleep(5)


def step_update_env(vm_ip: str) -> None:
    print(f"\n[5/6] Writing VM_MODE and VM_SERVER_URL to .env (IP={vm_ip})...")
    env_text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""

    def set_var(text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
        line = f"{key}={value}"
        if pattern.search(text):
            return pattern.sub(line, text)
        return text.rstrip("\n") + f"\n{line}\n"

    env_text = set_var(env_text, "VM_MODE", "true")
    env_text = set_var(env_text, "VM_SERVER_URL", f"http://{vm_ip}:{SERVER_PORT}")
    ENV_FILE.write_text(env_text, encoding="utf-8")
    print(f"      VM_SERVER_URL=http://{vm_ip}:{SERVER_PORT}")


def step_take_snapshot(vmx: str, vm_password: str = "") -> None:
    print("\n[6/6] Snapshot must be taken manually (vTPM limitation).")
    print("      In VMware: VM → Snapshot → Take Snapshot → name it 'base' → OK")
    input("      Press Enter after the snapshot is done...")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Provision VMware VM for Lark-CUA")
    parser.add_argument("--vmx", required=True, help="Path to .vmx file")
    parser.add_argument("--user", required=True, help="Guest Windows username")
    parser.add_argument("--password", required=True, help="Guest Windows password")
    parser.add_argument("--vm-password", default="",
                        help="VM encryption password (if VM is encrypted)")
    parser.add_argument("--skip-start", action="store_true",
                        help="Skip starting VM (use if already running)")
    args = parser.parse_args()

    print("=" * 60)
    print("Lark-CUA VM Provisioner")
    print("=" * 60)
    print(f"VMX     : {args.vmx}")
    print(f"Guest user: {args.user}")

    vmp = args.vm_password

    if not args.skip_start:
        step_start_vm(args.vmx, vm_password=vmp)

    step_copy_server(args.vmx, args.user, args.password, vm_password=vmp)
    step_install_deps(args.vmx, args.user, args.password, vm_password=vmp)
    step_register_startup(args.vmx, args.user, args.password, vm_password=vmp)

    vm_ip = get_vm_ip(args.vmx, vm_password=vmp)
    step_update_env(vm_ip)
    step_take_snapshot(args.vmx, vm_password=vmp)

    print("\n" + "=" * 60)
    print("Provisioning complete.")
    print(f"  Action server : http://{vm_ip}:{SERVER_PORT}")
    print("  Next step     : python tests/vm_runner.py --vmx <path> --product gui")
    print("=" * 60)


if __name__ == "__main__":
    main()
