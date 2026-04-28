"""
VM Client — runs on the HOST.

Communicates with action_server.py inside the VM via HTTP.
"""

import time
from pathlib import Path

import requests

import config


class VMClient:
    def __init__(self, base_url: str = config.VM_SERVER_URL, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def wait_ready(self, retries: int = 30, interval: float = 2.0) -> None:
        """Block until the action server inside the VM responds to /health."""
        for attempt in range(retries):
            try:
                r = self.session.get(f"{self.base_url}/health", timeout=3)
                if r.ok:
                    return
            except requests.RequestException:
                pass
            if attempt < retries - 1:
                time.sleep(interval)
        raise RuntimeError(f"VM action server not ready after {retries * interval:.0f}s")

    def screenshot(self, save_path: str | Path) -> Path:
        """Fetch a screenshot from the VM and save it locally."""
        r = self.session.get(f"{self.base_url}/screenshot", timeout=self.timeout)
        r.raise_for_status()
        save_path = Path(save_path)
        save_path.write_bytes(r.content)
        return save_path

    def execute(self, code: str) -> None:
        """Send pyautogui code to the VM for execution."""
        r = self.session.post(
            f"{self.base_url}/execute",
            json={"code": code},
            timeout=self.timeout,
        )
        r.raise_for_status()
        result = r.json()
        if not result.get("ok"):
            raise RuntimeError(f"VM exec failed:\n{result.get('error', '(unknown)')}")


# Module-level singleton — created lazily
_client: VMClient | None = None


def get_vm_client() -> VMClient:
    global _client
    if _client is None:
        _client = VMClient()
    return _client
