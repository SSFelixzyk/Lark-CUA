import base64
import time
from pathlib import Path
from openai import OpenAI
import config

_client = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.DOUBAO_API_KEY,
            base_url=config.DOUBAO_BASE_URL,
        )
    return _client


def encode_image(image_path: str | Path) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def chat(messages: list[dict], max_tokens: int = 500, retries: int = 3) -> str:
    """Send messages to Doubao and return the raw text response."""
    finish = None
    for attempt in range(retries):
        response = get_client().chat.completions.create(
            model=config.DOUBAO_ENDPOINT_ID,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.0,
            frequency_penalty=1,  # reduces repetition, same as UI-TARS deploy example
        )
        content = response.choices[0].message.content
        if content:
            return content
        finish = response.choices[0].finish_reason
        if attempt < retries - 1:
            print(f"  [warn] empty response (finish_reason={finish!r}), retry {attempt + 1}/{retries - 1}...")
            time.sleep(1)
    raise RuntimeError(f"Empty model response after {retries} retries (finish_reason={finish!r})")


def build_user_message(text: str, image_path: str | Path | None = None) -> dict:
    """Build a user message, optionally with an image."""
    if image_path is None:
        return {"role": "user", "content": text}

    b64 = encode_image(image_path)
    return {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": text},
        ],
    }
