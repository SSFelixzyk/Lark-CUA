import time
from pathlib import Path
import mss
import mss.tools
import config


def capture(save_path: str | Path | None = None) -> Path:
    """
    Capture full screen and save as PNG.
    Returns the path to the saved screenshot.
    """
    if save_path is None:
        ts = int(time.time() * 1000)
        save_path = config.SCREENSHOT_DIR / f"shot_{ts}.png"

    save_path = Path(save_path)
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # primary monitor
        img = sct.grab(monitor)
        mss.tools.to_png(img.rgb, img.size, output=str(save_path))

    return save_path


def screen_size() -> tuple[int, int]:
    """Return (width, height) of primary monitor."""
    with mss.mss() as sct:
        m = sct.monitors[1]
        return m["width"], m["height"]
