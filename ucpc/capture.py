import base64
from io import BytesIO

import mss
from PIL import Image

from .config import Config


def capture(config: Config) -> str:
    """Capture only on explicit request; keep the image entirely in memory."""
    with mss.MSS() as screen:
        if config.region is not None:
            area = config.region
        else:
            if config.monitor >= len(screen.monitors):
                raise ValueError(f"Монітор {config.monitor} не знайдений")
            area = screen.monitors[config.monitor]
        shot = screen.grab(area)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    img.thumbnail((config.max_image_size, config.max_image_size), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
