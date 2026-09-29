from __future__ import annotations

import argparse
from pathlib import Path

from .config import HostConfig
from .runtime import BrowserRuntime
from .ui import chatgpt_location


def main() -> int:
    parser = argparse.ArgumentParser(description="CAH Playwright browser host")
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8765/api")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--runtime-root", default=r"__CAH_BROWSER_ROOT__")
    parser.add_argument("--foreground-url", default="")
    args = parser.parse_args()

    import subprocess, sys
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'installation/preflight.py')], check=True)
    runtime = BrowserRuntime(HostConfig(
        bridge_url=args.bridge_url,
        cdp_url=args.cdp_url,
        runtime_root=Path(args.runtime_root),
    ))

    if args.foreground_url:
        location = chatgpt_location(args.foreground_url)
        if location is None or not location.conversation_id:
            raise SystemExit("Foreground target must be one exact ChatGPT conversation")
        runtime.state.data.setdefault("foreground", {})["conversation_url"] = args.foreground_url
        runtime.state.save()

    runtime.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
