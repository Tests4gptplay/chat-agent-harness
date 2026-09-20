#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRAND_ICONS = ROOT.parent / "assets" / "brand" / "icons"
ICON_FILES = [
    "cah-icon-16.png",
    "cah-icon-24.png",
    "cah-icon-32.png",
    "cah-icon-48.png",
    "cah-icon-128.png",
    "cah-icon-transparent.png",
]
FILES = [
    "control_transport.js",
    "lane_registry.js",
    "task_cell_registry.js",
    "ui_recovery_runtime.js",
    "background.js",
    "background_bundle.js",
    "lane_worker_runtime.js",
    "lane_worker_retirement.js",
    "lane_clear_runtime.js",
    "worker_runtime_v2.js",
    "worker_retirement.js",
    "worker_cleanup.js",
    "foreground_monitor.js",
    "history_rate_limit.js",
    "content.js",
    "foreground_content.js",
    "worker_root_content.js",
    "worker_content.js",
    "worker_retirement_content.js",
    "popup.html",
    "popup.js",
    "popup.css",
]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("target", choices=["chromium", "firefox"])
    p.add_argument("--out", default=None)
    a = p.parse_args()
    out = Path(a.out) if a.out else ROOT / "dist" / a.target
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    manifest = ROOT / f"manifest.{a.target}.json"
    json.loads(manifest.read_text(encoding="utf-8"))
    shutil.copy2(manifest, out / "manifest.json")
    for name in FILES: shutil.copy2(ROOT / name, out / name)

    icon_out = out / "icons"
    icon_out.mkdir(exist_ok=True)
    for name in ICON_FILES:
        shutil.copy2(BRAND_ICONS / name, icon_out / name)

    print(out)

if __name__ == "__main__": main()
