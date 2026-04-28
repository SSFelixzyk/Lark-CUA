"""
Action Server — runs INSIDE the VM.

Exposes a minimal HTTP API so the host agent can:
  - capture screenshots
  - execute pyautogui actions

Start with:
    python vm/action_server.py

Dependencies (install in VM):
    pip install flask pyautogui mss pillow pyperclip
"""

import io
import traceback

import mss
import mss.tools
import pyautogui
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)
pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True

HOST = "0.0.0.0"
PORT = 8765


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/screenshot")
def screenshot():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        png_bytes = mss.tools.to_png(img.rgb, img.size)
    return send_file(io.BytesIO(png_bytes), mimetype="image/png")


@app.post("/execute")
def execute():
    data = request.get_json(force=True)
    code = data.get("code", "")
    if not code:
        return jsonify({"ok": False, "error": "empty code"}), 400
    try:
        exec(code, {})  # noqa: S102
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": traceback.format_exc()}), 500


if __name__ == "__main__":
    print(f"Action server listening on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT)
