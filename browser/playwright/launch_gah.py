#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTENSION = ROOT / "extension" / "dist" / "chromium"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Launch one persistent Playwright bundled-Chromium CAH browser profile."
    )
    p.add_argument("--profile-dir", required=True, help="Dedicated persistent Chromium user-data directory.")
    p.add_argument("--extension-dir", default=str(DEFAULT_EXTENSION), help="Built Chromium extension directory.")
    p.add_argument("--url", default="https://chatgpt.com/", help="Initial page to open.")
    p.add_argument("--no-build", action="store_true", help="Skip rebuilding extension/dist/chromium first.")
    return p.parse_args()


def ensure_extension(extension_dir: Path, no_build: bool) -> None:
    if not no_build:
        subprocess.check_call([sys.executable, str(ROOT / "extension" / "build.py"), "chromium"])
    manifest = extension_dir / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"Chromium extension build missing: {manifest}")


def main() -> int:
    args = parse_args()
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    extension_dir = Path(args.extension_dir).expanduser().resolve()
    ensure_extension(extension_dir, args.no_build)
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright Python package is missing. Install with:\n"
            "  py -m pip install playwright\n"
            "  py -m playwright install chromium",
            file=sys.stderr,
        )
        return 2

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chromium",
            headless=False,
            args=[
                f"--disable-extensions-except={extension_dir}",
                f"--load-extension={extension_dir}",
            ],
        )

        worker = None
        workers = context.service_workers
        if workers:
            worker = workers[0]
        else:
            try:
                worker = context.wait_for_event("serviceworker", timeout=15000)
            except Exception:
                worker = None

        extension_id = None
        if worker and worker.url.startswith("chrome-extension://"):
            extension_id = worker.url.split("/")[2]

        pages = context.pages
        page = pages[0] if pages else context.new_page()
        if page.url in ("about:blank", "chrome://newtab/"):
            page.goto(args.url)

        print(f"profile_dir={profile_dir}")
        print(f"extension_dir={extension_dir}")
        print(f"extension_id={extension_id or '<not-observed-yet>'}")
        print("Browser is running. First launch: sign in to ChatGPT and configure CAH once in this profile.")
        print("Press Ctrl+C here to stop this managed browser.")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
