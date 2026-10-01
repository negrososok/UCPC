import argparse
import sys

from dotenv import load_dotenv

from .config import ROOT, load_config


def main():
    for output in (sys.stdout, sys.stderr):
        if output is not None and hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="UCPC background screenshot-to-text assistant")
    parser.add_argument("--login", action="store_true", help="Sign in with ChatGPT in your browser")
    parser.add_argument("--models", action="store_true", help="List your ChatGPT account's models")
    parser.add_argument("--check", action="store_true", help="Check local config; no AI call")
    parser.add_argument("--smoke", action="store_true", help="Start tray and hotkeys, then quit")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("UCPC supports Windows only")
    config = load_config()
    load_dotenv(ROOT / ".env")
    if args.login or args.models:
        from .auth import Auth

        auth = Auth()
        if args.login:
            print("Заверши Continue with ChatGPT у браузері. Використовуватиметься підписка.")
            email = auth.login()
            print("Підключено:", email)
        else:
            for model in auth.models():
                print(f"{model['slug']}: {model.get('display_name', model['slug'])}")
        return 0
    if args.check:
        from .auth import Auth

        if not config.prompt():
            raise ValueError("Системний промпт порожній")
        print("Config, hotkey syntax and prompt: OK")
        print(Auth().info())
        return 0
    from .app import run

    return run(config, args.smoke)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — CLI boundary must show a safe startup error.
        import traceback

        from .auth import state_dir
        from .engine import friendly_error

        message = friendly_error(exc)
        try:
            directory = state_dir()
            directory.mkdir(parents=True, exist_ok=True)
            # Stack locations, never exception arguments or captured content/tokens.
            (directory / "startup-error.log").write_text(
                type(exc).__name__ + "\n" + "".join(traceback.format_tb(exc.__traceback__)),
                encoding="utf-8",
            )
        except OSError:
            pass
        if sys.stdout is not None:
            print(message, file=sys.stderr)
        elif "--smoke" not in sys.argv:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "UCPC", 0x10)
        raise SystemExit(1)
