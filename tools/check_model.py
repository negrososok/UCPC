"""One real subscription inference on a synthetic image; never captures the desktop."""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from ucpc.auth import Auth
from ucpc.config import load_config
from ucpc.engine import Engine
from ucpc.history import Track


async def main():
    job = Engine.__new__(Engine)
    job.config = load_config()
    job.auth = Auth()
    job.on_error = lambda track, message: None
    image = BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")
    track = Track()
    await asyncio.wait_for(job._generate(url, "Reply only: UCPC OK", track), timeout=120)
    text, complete, error = track.snapshot()
    result = {
        "model": track.model_name(),
        "complete": complete,
        "response": text,
        "error": error,
    }
    output = Path(__file__).resolve().parent.parent / "data" / "live-model-check.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if error or not complete or not text.strip():
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
