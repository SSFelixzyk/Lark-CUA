import time
from pathlib import Path
import mss
import mss.tools
import config


def capture(save_path: str | Path | None = None) -> Path:
    """
    Capture full screen and save as PNG.
    In VM_MODE, fetches the screenshot from the VM action server.
    Returns the path to the saved screenshot.
    """
    if save_path is None:
        ts = int(time.time() * 1000)
        save_path = config.SCREENSHOT_DIR / f"shot_{ts}.png"

    save_path = Path(save_path)

    if config.VM_MODE:
        from vm.vm_client import get_vm_client
        get_vm_client().screenshot(save_path)
    else:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            mss.tools.to_png(img.rgb, img.size, output=str(save_path))

    return save_path


def screen_size() -> tuple[int, int]:
    """Return (width, height) of primary monitor."""
    if config.VM_MODE:
        from PIL import Image
        img = Image.open(capture())
        return img.width, img.height
    with mss.mss() as sct:
        m = sct.monitors[1]
        return m["width"], m["height"]
