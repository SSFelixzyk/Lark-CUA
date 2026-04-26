import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DOUBAO_API_KEY = os.environ["DOUBAO-API-KEY"]
DOUBAO_ENDPOINT_ID = os.environ["EP-ID"]
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# Screenshot settings
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# Agent settings
MAX_STEPS = 30
STEP_WAIT_MS = 1500   # ms to wait after executing an action before next screenshot
HISTORY_TURNS = 6     # number of past (user+assistant) turns to keep in context
