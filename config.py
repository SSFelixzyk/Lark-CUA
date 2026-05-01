import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DOUBAO_API_KEY = os.environ.get("DOUBAO-API-KEY") or os.environ["DOUBAO_API_KEY"]
DOUBAO_ENDPOINT_ID = os.environ.get("EP-ID") or os.environ["EP_ID"]
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# Screenshot settings
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# Agent settings
MAX_STEPS = 30
STEP_WAIT_MS = 1500   # ms to wait after executing an action before next screenshot
HISTORY_TURNS = 6     # number of past (user+assistant) turns to keep in context

# VM settings (OSWorld-style remote execution)
# Set VM_MODE=true in .env to enable; set VM_SERVER_URL to the VM's action server address.
VM_MODE = os.environ.get("VM_MODE", "false").lower() == "true"
VM_SERVER_URL = os.environ.get("VM_SERVER_URL", "http://192.168.1.100:8765")

# Feishu open-platform credentials (for report publisher)
FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_REPORT_FOLDER = os.environ.get("FEISHU_REPORT_FOLDER", "")
FEISHU_HOST       = os.environ.get("FEISHU_HOST", "https://feishu.cn")
