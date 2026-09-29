from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import DEFAULT_CDP_URL, TASK_CELL
from .ui import ChatGPTUI, chatgpt_location


def _load_lane_targets(repo_root: Path) -> list[dict[str, str]]:
    path = repo_root / "state" / "lanes.json"
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    targets: list[dict[str, str]] = []
    for lane in value.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        key = str(lane.get("project_key") or "").strip()
        url = str(lane.get("project_root_url") or "").strip()
        if not key.startswith("g-p-") or not url.startswith("https://chatgpt.com/"):
            raise RuntimeError(f"Invalid CAH lane project identity: {lane.get('lane_id')}")
        targets.append({
            "kind": "lane",
            "name": str(lane.get("display_name") or lane.get("lane_id") or key),
            "project_key": key,
            "project_root_url": url,
        })
    return targets


def reset_targets(repo_root: Path) -> list[dict[str, str]]:
    targets = [{
        "kind": "task_cell",
        "name": str(TASK_CELL["display_name"]),
        "project_key": str(TASK_CELL["project_key"]),
        "project_root_url": str(TASK_CELL["project_root_url"]),
    }]
    targets.extend(_load_lane_targets(repo_root))

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in targets:
        key = item["project_key"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _close_project_conversation_pages(ui: ChatGPTUI, project_key: str) -> int:
    closed = 0
    for page in list(ui.pages()):
        loc = chatgpt_location(page.url)
        if not loc or loc.project_key != project_key or not loc.conversation_id:
            continue
        try:
            page.close()
            closed += 1
        except Exception:
            pass
    return closed


def clear_project(ui: ChatGPTUI, target: dict[str, str], *, verify_only: bool) -> dict[str, Any]:
    project_key = target["project_key"]
    project_root_url = target["project_root_url"]
    root = ui.project_root(project_key, project_root_url, reuse=False)
    try:
        if verify_only:
            composer = {"cleared": False, "prior_length": 0}
            before = ui.list_project_conversations(root, project_key)
            if not before:
                root.wait_for_timeout(500)
                before = ui.list_project_conversations(root, project_key)
            after = before
            deleted_count = 0
        else:
            composer = ui.clear_composer(root)
            before = ui.list_project_conversations(root, project_key)
            drained = ui.drain_project_page(root, project_key)
            deleted_count = int(drained.get("deleted_count") or 0)
            after = ui.list_project_conversations(root, project_key)
            if after:
                raise RuntimeError(
                    f"CAH semantic reset did not empty {target['name']}; remaining={len(after)}"
                )
    finally:
        try:
            root.close()
        except Exception:
            pass

    closed_tabs = 0 if verify_only else _close_project_conversation_pages(ui, project_key)
    return {
        **target,
        "composer_cleared": bool(composer.get("cleared")),
        "composer_prior_length": int(composer.get("prior_length") or 0),
        "before_count": len(before),
        "deleted_count": deleted_count,
        "remaining_count": len(after),
        "closed_conversation_tabs": closed_tabs,
    }


def run(repo_root: Path, cdp_url: str, *, verify_only: bool = False) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    targets = reset_targets(repo_root)
    ui = ChatGPTUI(cdp_url)
    ui.connect()
    try:
        results = [
            clear_project(ui, target, verify_only=verify_only)
            for target in targets
        ]
    finally:
        ui.close()

    remaining = sum(int(item["remaining_count"]) for item in results)
    if remaining:
        raise RuntimeError(f"CAH semantic reset verification failed; remaining={remaining}")

    return {
        "ok": True,
        "verify_only": verify_only,
        "project_count": len(results),
        "deleted_count": sum(int(item["deleted_count"]) for item in results),
        "remaining_count": remaining,
        "projects": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear semantic ChatGPT conversations from dedicated CAH projects."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    result = run(
        Path(args.repo_root),
        str(args.cdp_url),
        verify_only=bool(args.verify_only),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
