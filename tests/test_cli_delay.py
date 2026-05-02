"""
CLI search indexing latency test.

Sends a unique message via lark-cli, then polls messages-search every few
seconds to measure how long it takes for the message to become searchable.

Usage:
    python tests/test_cli_delay.py
    python tests/test_cli_delay.py --user-id ou_xxx --poll-interval 3 --timeout 120
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

# ── defaults ──────────────────────────────────────────────────────────────────
DEFAULT_USER_ID  = "ou_797936fc32c9fa58efac2a974a2518bf"   # 于凯成
POLL_INTERVAL    = 5    # seconds between search attempts
TIMEOUT          = 120  # give up after this many seconds
SHELL            = True  # Windows: required for npm-installed CLIs


def _run(cmd: str) -> tuple[bool, dict | list | None, str]:
    r = subprocess.run(
        cmd, shell=SHELL, capture_output=True, text=True,
        timeout=20, encoding="utf-8", errors="replace"
    )
    raw = r.stdout.strip() or r.stderr.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return r.returncode == 0, parsed, raw


def _iso(dt: datetime) -> str:
    off = dt.astimezone().strftime("%z")
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S") + off[:3] + ":" + off[3:]


def send_message(user_id: str, text: str) -> bool:
    cmd = f'lark-cli im +messages-send --as user --user-id {user_id} --text "{text}"'
    ok, data, raw = _run(cmd)
    if not ok:
        print(f"[send] FAILED: {raw[:200]}")
        return False
    print(f"[send] OK: {text!r}")
    return True


def poll_until_found(keyword: str, since: datetime, interval: int, timeout: int) -> float | None:
    """
    Poll messages-search until `keyword` appears in messages after `since`.
    Returns elapsed seconds if found, None if timed out.
    """
    start    = time.time()
    iso_since = _iso(since)
    attempt  = 0

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            return None

        attempt += 1
        cmd = (
            f'lark-cli im +messages-search --query "{keyword}" '
            f'--start "{iso_since}" --format json'
        )
        ok, data, raw = _run(cmd)

        if not ok:
            print(f"  [{elapsed:5.1f}s] attempt {attempt}: CLI error — {raw[:80]}")
        else:
            items = (data or {}).get("items", []) if isinstance(data, dict) else []
            if items:
                print(f"  [{elapsed:5.1f}s] attempt {attempt}: FOUND ({len(items)} item(s))")
                return elapsed
            else:
                print(f"  [{elapsed:5.1f}s] attempt {attempt}: not found yet")

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Measure Feishu CLI message search indexing latency")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID,
                        help="Recipient open_id (default: 于凯成)")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL,
                        help=f"Seconds between search attempts (default {POLL_INTERVAL})")
    parser.add_argument("--timeout", type=int, default=TIMEOUT,
                        help=f"Give up after this many seconds (default {TIMEOUT})")
    parser.add_argument("--no-send", action="store_true",
                        help="Skip sending — manually send the keyword shown, then press Enter")
    args = parser.parse_args()

    ts      = datetime.now().strftime("%H%M%S")
    keyword = f"CLI延迟测试_{ts}"

    print("=" * 60)
    print("Feishu CLI search indexing latency test")
    print(f"  keyword      : {keyword!r}")
    print(f"  poll interval: {args.poll_interval}s")
    print(f"  timeout      : {args.timeout}s")
    print("=" * 60)

    if args.no_send:
        print(f"\nPlease manually send a message containing: {keyword!r}")
        input("Press Enter once you've sent it...")
        t0 = datetime.now()
    else:
        t0 = datetime.now()
        if not send_message(args.user_id, keyword):
            sys.exit(1)

    print(f"\n[t0] message sent at {t0.strftime('%H:%M:%S')} — starting poll...\n")

    elapsed = poll_until_found(keyword, t0, args.poll_interval, args.timeout)

    print()
    print("=" * 60)
    if elapsed is None:
        print(f"TIMEOUT: message not found within {args.timeout}s")
    else:
        print(f"FOUND after {elapsed:.1f}s")
        print()
        print(f"Recommendation for verifier retry config:")
        recommended_retries  = max(3, int(elapsed / args.poll_interval) + 2)
        recommended_interval = args.poll_interval
        print(f"  retries  = {recommended_retries}")
        print(f"  interval = {recommended_interval}s  (total wait = {recommended_retries * recommended_interval}s)")
    print("=" * 60)


if __name__ == "__main__":
    main()
