import json
import math
import os
import shutil
import sys
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .actions import ACTIONS, DEFAULT_HOTKEYS, OLD_DEFAULT_HOTKEYS

ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
LEGACY_FIELDS = {
    "tts_provider",
    "windows_voice",
    "windows_rate",
    "tts_model",
    "voice",
    "tts_instructions",
    "audio_device",
    "seek_seconds",
    "speech_enabled",
}
LEGACY_HOTKEYS = {
    "capture": "ctrl+alt+f8",
    "cancel": "ctrl+alt+f9",
    "overlay": "ctrl+alt+f11",
    "previous": "ctrl+alt+pageup",
    "next": "ctrl+alt+pagedown",
    "overlay_up": "ctrl+alt+shift+up",
    "overlay_down": "ctrl+alt+shift+down",
}


@dataclass(frozen=True)
class Config:
    auth_mode: str = "chatgpt"
    vision_model: str = "gpt-5.6-sol"
    api_vision_model: str = "gpt-4.1-mini"
    system_prompt_file: str = "system_prompt.txt"
    request_prompt: str = "Проаналізуй усі скріншоти разом згідно із системною інструкцією."
    max_output_tokens: int = 1800
    request_timeout: float = 180.0
    generation_timeout: float = 600.0
    verify_answer: bool = False
    reasoning_effort: str = "high"
    monitor: int = 1
    max_image_size: int = 1920
    region: dict | None = None
    notifications: bool = False
    overlay_enabled: bool = True
    theme: str = "light"
    overlay_font_size: int = 18
    overlay_opacity: int = 94
    overlay_width: int = 660
    overlay_height: int = 580
    move_step: int = 24
    scroll_lines: int = 5
    hotkeys: dict[str, str] = field(default_factory=lambda: DEFAULT_HOTKEYS.copy())

    def validate(self) -> None:
        limits = {
            "monitor": (0, 32),
            "max_image_size": (320, 8192),
            "max_output_tokens": (1, 100_000),
            "overlay_font_size": (12, 32),
            "overlay_opacity": (75, 100),
            "overlay_width": (440, 2400),
            "overlay_height": (320, 1800),
            "move_step": (1, 200),
            "scroll_lines": (1, 40),
        }
        for name, (lo, hi) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{name}: потрібне ціле число від {lo} до {hi}")
        for name in ("request_timeout", "generation_timeout"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}: потрібне скінченне додатне число")
        for name in (
            "auth_mode",
            "vision_model",
            "api_vision_model",
            "system_prompt_file",
            "request_prompt",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}: потрібен непорожній рядок")
        if self.auth_mode not in {"chatgpt", "api"}:
            raise ValueError("auth_mode: очікується chatgpt або api")
        if self.theme not in ("light", "dark"):
            raise ValueError("theme: очікується light або dark")
        if self.reasoning_effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("reasoning_effort: очікується low, medium, high, xhigh або max")
        for name in ("notifications", "overlay_enabled", "verify_answer"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name}: потрібне true або false")
        if not isinstance(self.hotkeys, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.hotkeys.items()
        ):
            raise ValueError("hotkeys: потрібна таблиця дій і комбінацій")
        unknown = set(self.hotkeys) - set(ACTIONS)
        if unknown:
            raise ValueError("Невідомі дії хоткеїв: " + ", ".join(sorted(unknown)))
        from .hotkeys import parse_hotkey

        seen = set()
        for binding in self.hotkeys.values():
            if not binding:
                continue
            parsed = parse_hotkey(binding)
            if parsed in seen:
                raise ValueError("Повторений хоткей: " + binding)
            seen.add(parsed)
        if self.region is not None:
            if not isinstance(self.region, dict) or set(self.region) != {
                "left",
                "top",
                "width",
                "height",
            }:
                raise ValueError("region: потрібні left, top, width, height")
            if (
                not all(type(v) is int for v in self.region.values())
                or self.region["width"] <= 0
                or self.region["height"] <= 0
            ):
                raise ValueError("region: потрібні цілі координати і додатні width та height")

    def prompt(self) -> str:
        return (ROOT / self.system_prompt_file).read_text(encoding="utf-8-sig").strip()


def atomic_write(path: Path, text: str) -> None:
    """Same-volume replacement; an interrupted write leaves the original intact."""
    fd, temporary = tempfile.mkstemp(prefix=".ucpc-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_config(config: Config, path: Path | None = None) -> None:
    config.validate()
    data = asdict(config)
    bindings, region = data.pop("hotkeys"), data.pop("region")
    lines = ["# UCPC — текстовий помічник. Налаштування також доступні у вікні програми."]
    for name, value in data.items():
        lines.append(f"{name} = {json.dumps(value, ensure_ascii=False)}")
    if region is not None:
        lines += ["", "[region]"] + [f"{k} = {v}" for k, v in region.items()]
    lines += ["", "[hotkeys]"] + [f"{k} = {json.dumps(v)}" for k, v in bindings.items()]
    atomic_write(path or ROOT / "config.toml", "\n".join(lines) + "\n")


def initialize() -> None:
    for source, target in (
        ("config.example.toml", "config.toml"),
        ("system_prompt.example.txt", "system_prompt.txt"),
        (".env.example", ".env"),
    ):
        if not (ROOT / target).exists():
            shutil.copyfile(ROOT / source, ROOT / target)


def load_config() -> Config:
    initialize()
    data = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
    removed_limit = "history_limit" in data
    added_solver_settings = any(k not in data for k in ("verify_answer", "reasoning_effort"))
    added_generation_timeout = "generation_timeout" not in data
    if added_generation_timeout and data.get("request_timeout") == 60:
        data["request_timeout"] = Config().request_timeout
    data.pop("history_limit", None)
    legacy = bool(set(data) & LEGACY_FIELDS)
    for field_name in LEGACY_FIELDS:
        data.pop(field_name, None)
    if legacy:
        old = data.get("hotkeys", {})
        bindings = DEFAULT_HOTKEYS.copy()
        for action, binding in old.items():
            if action in ACTIONS and binding.lower() != LEGACY_HOTKEYS.get(action):
                bindings[action] = binding
        data["hotkeys"] = bindings
    # Upgrade only the former defaults; keep disabled and custom shortcuts intact.
    old_bindings = data.get("hotkeys", DEFAULT_HOTKEYS)
    from .hotkeys import parse_hotkey

    def is_old_default(action, binding):
        return bool(binding) and any(
            action in defaults and parse_hotkey(binding) == parse_hotkey(defaults[action])
            for defaults in OLD_DEFAULT_HOTKEYS
        )

    custom_keys = {
        parse_hotkey(binding)
        for action, binding in old_bindings.items()
        if binding and not is_old_default(action, binding)
    }
    bindings = {
        action: DEFAULT_HOTKEYS[action]
        if is_old_default(action, binding)
        and parse_hotkey(DEFAULT_HOTKEYS[action]) not in custom_keys
        else binding
        for action, binding in old_bindings.items()
    }
    # Add new actions without taking shortcuts assigned by the user.
    occupied = {parse_hotkey(b) for b in bindings.values() if b}
    for action in ("copy", "send", "clear_images", "verify", "help"):
        if action not in bindings:
            default = DEFAULT_HOTKEYS[action]
            parsed = parse_hotkey(default)
            bindings[action] = default if parsed not in occupied else ""
            if bindings[action]:
                occupied.add(parsed)
    upgraded = bindings != old_bindings
    data["hotkeys"] = bindings
    config = Config(**data)
    config.validate()
    if legacy or upgraded or removed_limit or added_solver_settings or added_generation_timeout:
        save_config(config)
    return config
