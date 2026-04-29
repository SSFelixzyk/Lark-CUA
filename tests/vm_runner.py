"""
VM Benchmark Runner (VMware Workstation Pro edition).

Orchestrates benchmark cases against a VMware VM:
  1. Revert VM to base snapshot
  2. Start VM and wait for action server to be ready
  3. Run benchmark case(s) via the host agent
  4. Collect and print results

Usage:
    python tests/vm_runner.py --vmx "C:/VMs/LarkCUA/LarkCUA.vmx" --snapshot base
    python tests/vm_runner.py --vmx "C:/VMs/LarkCUA/LarkCUA.vmx" --product gui --level L1

Requirements:
    - VMware Workstation Pro installed, vmrun.exe on PATH
      (typically: C:/Program Files (x86)/VMware/VMware Workstation/vmrun.exe)
    - VM has action_server.py running on startup (e.g. Task Scheduler / startup script)
    - VM_MODE=true and VM_SERVER_URL set in .env
"""

import sys
import json
import time
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
import config  # noqa
from vm.vm_client import VMClient
from tests.run_benchmark import load_cases, run_case, print_summary, save_results, parse_case_ids

DEFAULT_VMRUN = "vmrun"          # assumes vmrun.exe is on PATH
DEFAULT_SNAPSHOT = "base"
DEFAULT_BOOT_WAIT = 30           # seconds after startvm before polling /health


def vmrun(vmrun_exe: str, *args: str) -> None:
    cmd = [vmrun_exe, *args]
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def revert_and_start(vmrun_exe: str, vmx: str, snapshot: str, boot_wait: int) -> None:
    print(f"Reverting '{vmx}' to snapshot '{snapshot}'...")
    vmrun(vmrun_exe, "revertToSnapshot", vmx, snapshot)
    print("Starting VM...")
    vmrun(vmrun_exe, "start", vmx)
    print(f"Waiting {boot_wait}s for VM to boot...")
    time.sleep(boot_wait)


def main():
    parser = argparse.ArgumentParser(description="VM Benchmark Runner (VMware)")
    parser.add_argument("--vmx", required=True, help="Path to .vmx file")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="Snapshot name to revert to")
    parser.add_argument("--vmrun", default=DEFAULT_VMRUN, help="Path to vmrun executable")
    parser.add_argument("--boot-wait", type=int, default=DEFAULT_BOOT_WAIT,
                        help="Seconds to wait after VM start before polling health")
    parser.add_argument("--reset", choices=["each", "never"], default="each",
                        help="'each': revert snapshot before every case; 'never': revert once at start")
    parser.add_argument("--product", choices=["im", "docs", "calendar", "base", "vc", "mail", "gui"])
    parser.add_argument("--level", choices=["L1", "L2", "L3"])
    parser.add_argument("--tag")
    parser.add_argument("--case-id", action="append",
                        help="Run specific case id(s), comma-separated or repeated")
    parser.add_argument("--contact", default="<<TEST_CONTACT>>")
    parser.add_argument("--group", default="CUA-Lark课题-6")
    parser.add_argument("--meeting-id", default="<<MEETING_ID>>")
    args = parser.parse_args()

    if not config.VM_MODE:
        print("ERROR: VM_MODE is not enabled. Set VM_MODE=true in your .env file.")
        sys.exit(1)

    case_ids_filter = parse_case_ids(args.case_id)
    cases = load_cases(args.product, args.level, args.tag, case_ids_filter)
    if not cases:
        print("No cases matched the filters.")
        sys.exit(0)

    print(f"Loaded {len(cases)} case(s)")
    for c in cases:
        print(f"  {c['id']:<18} [{c['level']}] {c['title']}")
    print()

    client = VMClient(base_url=config.VM_SERVER_URL)

    # Initial VM start
    revert_and_start(args.vmrun, args.vmx, args.snapshot, args.boot_wait)
    print("Waiting for action server...")
    client.wait_ready()
    print("VM ready.\n")

    results = []
    for i, case in enumerate(cases, 1):
        if args.reset == "each" and i > 1:
            print(f"\nReverting snapshot before case {i}...")
            revert_and_start(args.vmrun, args.vmx, args.snapshot, args.boot_wait)
            client.wait_ready()
            print("VM ready.")

        print(f"\n[{i}/{len(cases)}] {case['id']} — {case['title']}")
        try:
            client.wait_ready(retries=5, interval=2.0)
        except RuntimeError:
            print("  => SKIP  Action server unreachable (VM may be suspended)")
            results.append({
                "id": case["id"], "product": case["product"], "level": case["level"],
                "title": case["title"], "status": "skipped", "total_steps": 0,
                "elapsed_ms": 0, "steps": [],
            })
            continue
        r = run_case(case, args.contact, args.group, args.meeting_id, dry_run=False)
        results.append(r)

        status_str = r["status"].upper()
        print(f"  => {status_str}  steps={r['total_steps']}  time={r['elapsed_ms']/1000:.1f}s")
        if r["status"] == "error" and r["steps"]:
            last_err = r["steps"][-1].get("error")
            if last_err:
                print(f"     reason: {last_err}")

    print_summary(results)
    out = save_results(results)
    print(f"\nResults saved to: {out}")


if __name__ == "__main__":
    main()
