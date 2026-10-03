"""One source of truth for the settings, help and global shortcuts."""

ACTIONS = {
    "help": "Допомога: усі чинні клавіші",
    "capture": "Додати скріншот до запиту",
    "send": "Відправити зібрані скріншоти",
    "verify": "Перевірити результат запуску",
    "copy": "Скопіювати всю поточну відповідь",
    "overlay": "Показати / приховати відповідь",
    "settings": "Показати / приховати налаштування",
    "cancel": "Скасувати генерацію",
    "clear_images": "Очистити зібрані скріншоти",
    "font_smaller": "Зменшити текст",
    "font_larger": "Збільшити текст",
    "center": "Повернути вікно в центр",
    "previous": "Попередня відповідь",
    "next": "Наступна відповідь",
    "overlay_up": "Прокрутити вгору",
    "overlay_down": "Прокрутити вниз",
    "scroll_left": "Прокрутити код ліворуч",
    "scroll_right": "Прокрутити код праворуч",
    "top": "Початок відповіді",
    "bottom": "Кінець відповіді",
    "move_up": "Посунути вікно вгору",
    "move_down": "Посунути вікно вниз",
    "move_left": "Посунути вікно ліворуч",
    "move_right": "Посунути вікно праворуч",
}

PREVIOUS_DEFAULT_HOTKEYS = {
    "capture": "ctrl+alt+shift+f8",
    "overlay": "ctrl+alt+shift+f11",
    "settings": "ctrl+alt+shift+f12",
    "cancel": "ctrl+alt+shift+f9",
    "previous": "ctrl+alt+shift+b",
    "next": "ctrl+alt+shift+n",
    "overlay_up": "ctrl+alt+shift+w",
    "overlay_down": "ctrl+alt+shift+s",
    "scroll_left": "ctrl+alt+shift+q",
    "scroll_right": "ctrl+alt+shift+e",
    "top": "ctrl+alt+shift+home",
    "bottom": "ctrl+alt+shift+end",
    "move_up": "ctrl+alt+win+up",
    "move_down": "ctrl+alt+win+down",
    "move_left": "ctrl+alt+win+left",
    "move_right": "ctrl+alt+win+right",
    "center": "ctrl+alt+shift+f10",
    "font_smaller": "ctrl+alt+shift+f5",
    "font_larger": "ctrl+alt+shift+f6",
}

ALT_DEFAULT_HOTKEYS = {
    "capture": "ctrl+alt+f8",
    "overlay": "ctrl+alt+f11",
    "settings": "ctrl+alt+f12",
    "cancel": "ctrl+alt+f9",
    "previous": "alt+shift+b",
    "next": "alt+shift+n",
    "overlay_up": "alt+shift+pageup",
    "overlay_down": "alt+shift+pagedown",
    "scroll_left": "ctrl+alt+a",
    "scroll_right": "ctrl+alt+d",
    "top": "alt+shift+home",
    "bottom": "alt+shift+end",
    "move_up": "alt+shift+w",
    "move_down": "alt+shift+s",
    "move_left": "alt+shift+q",
    "move_right": "alt+shift+e",
    "center": "ctrl+alt+f10",
    "font_smaller": "ctrl+alt+f5",
    "font_larger": "ctrl+alt+f6",
}

# One shared prefix; avoid Alt mnemonics and Windows' reserved Ctrl+Win shortcuts.
BATCH_DEFAULT_HOTKEYS = {
    "capture": "ctrl+win+f8",
    "overlay": "ctrl+win+f11",
    "settings": "ctrl+win+f12",
    "cancel": "ctrl+win+f9",
    "previous": "ctrl+win+z",
    "next": "ctrl+win+x",
    "overlay_up": "ctrl+win+pageup",
    "overlay_down": "ctrl+win+pagedown",
    "scroll_left": "ctrl+win+a",
    "scroll_right": "ctrl+win+r",
    "top": "ctrl+win+home",
    "bottom": "ctrl+win+end",
    "move_up": "ctrl+win+y",
    "move_down": "ctrl+win+h",
    "move_left": "ctrl+win+g",
    "move_right": "ctrl+win+j",
    "center": "ctrl+win+f10",
    "font_smaller": "ctrl+win+f5",
    "font_larger": "ctrl+win+f6",
    "copy": "ctrl+win+f7",
    "send": "ctrl+win+f2",
    "clear_images": "ctrl+win+f3",
}

KEYBOARD_DEFAULT_HOTKEYS = BATCH_DEFAULT_HOTKEYS | {
    "help": "ctrl+win+f1",
    "send": "ctrl+win+f9",
    "verify": "ctrl+win+f10",
    "cancel": "ctrl+win+f2",
    "center": "ctrl+win+u",
}

DEFAULT_HOTKEYS = KEYBOARD_DEFAULT_HOTKEYS | {"capture": "mouse4", "send": "mouse5"}

OLD_DEFAULT_HOTKEYS = (
    PREVIOUS_DEFAULT_HOTKEYS, ALT_DEFAULT_HOTKEYS, BATCH_DEFAULT_HOTKEYS,
    KEYBOARD_DEFAULT_HOTKEYS,
)


def display_binding(binding: str) -> str:
    return "+".join(
        {"mouse4": "Mouse4", "mouse5": "Mouse5"}.get(
            p.lower(), p.capitalize() if not p.lower().startswith("f") else p.upper()
        ) for p in binding.split("+")
    )
