#!/usr/bin/env python3
"""Create a configured CAH checkout from the public source."""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
from pathlib import Path
import re
import shutil
import tokenize

ROOT = Path(__file__).resolve().parents[1]


def project(url: str, name: str) -> dict:
    match = re.search(r"/g/(g-p-[A-Za-z0-9]+)", url)
    if not match:
        raise ValueError(f"{name}: enter the Project root URL opened in Chrome")
    return {"display_name": name, "project_key": match.group(1),
            "project_root_url": url.rstrip("/")}


def replace_text(text: str, values: dict[str, str]) -> str:
    for marker, value in values.items():
        text = text.replace(marker, value)
    return text


def render_python(text: str, values: dict[str, str]) -> str:
    """Replace configuration inside Python string literals with proper escaping."""
    result = []
    for item in tokenize.generate_tokens(io.StringIO(text).readline):
        if item.type == tokenize.STRING and any(k in item.string for k in values):
            literal = ast.literal_eval(item.string)
            if isinstance(literal, str):
                item = item._replace(string=repr(replace_text(literal, values)))
        elif any(k in item.string for k in values):
            # Formatted prompt strings contain repository/name markers.
            escaped = {k: v.replace("\\", "/").replace("'", "\\'").replace('"', '\\"')
                       for k, v in values.items()}
            item = item._replace(string=replace_text(item.string, escaped))
        result.append(item)
    return tokenize.untokenize(result)


def configure(config_path: Path, output: Path) -> Path:
    cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
    output = output.resolve()
    # copytree creates a new directory and preserves existing installations.
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Choose an output directory beside the public source")
    base = Path(cfg["base_directory"])
    cell = project(cfg["task_cell_url"], "CAH Task Cell")
    lanes = []
    for n, url in enumerate(cfg["lane_urls"]):
        lanes.append({"lane_id": f"lane-{n:02d}", **project(url, f"CAH Sandbox{n}"),
                      "enabled": True, "status": "IDLE", "task_pools": {}})
    if not lanes:
        raise ValueError("Add at least one Sandbox Project URL to lane_urls")
    paths = {
        "REPO_ROOT": output, "BRIDGE_ROOT": base/"runtime"/"bridge",
        "MANAGED_RUNNER_ROOT": base/"runners"/"managed-01",
        "SECOND_RUNNER_ROOT": base/"runners"/"managed-02",
        "SHOT_RUNNER_ROOT": base/"runners"/"shot",
        "BROWSER_ROOT": base/"runtime"/"browser", "TOOLS_ROOT": base/"tools"/"playwright",
        "WORK_ROOT": base/"work", "DELIVERABLES_ROOT": base/"deliverables",
        "RUNNER_BASE": base/"runners", "BROWSER_BASE": base/"runtime",
        "TEMP_ROOT": base/"temp",
    }
    paths.update({k: Path(v) for k, v in cfg.get("paths", {}).items()})
    values = {f"__CAH_{key}__": str(value) for key, value in paths.items()}
    values.update({
        "__CAH_REPOSITORY__": cfg["repository"],
        "__CAH_SECOND_RUNNER_NAME__": cfg.get("second_runner_name", "CAH-Managed-02"),
        "__CAH_RECORD_REPOSITORY__": cfg.get("record_repository") or cfg["repository"],
        "__CAH_TRANSFER_REPOSITORY__": cfg.get("transfer_repository") or cfg["repository"],
        "g-p-CAHTASKCELL": cell["project_key"],
        "g-p-CAHLANE00": lanes[0]["project_key"],
    })
    if len(lanes) > 1:
        values["g-p-CAHLANE01"] = lanes[1]["project_key"]
    shutil.copytree(ROOT, output, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", "config.local.json"))
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".py", ".ps1", ".bat", ".md", ".json", ".js", ".yml", ".yaml"
        }:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if not any(k in text for k in values):
            continue
        if path.suffix == ".py":
            text = render_python(text, values)
        elif path.suffix == ".json":
            text = replace_text(text, {k: json.dumps(v, ensure_ascii=False)[1:-1]
                                       for k, v in values.items()})
        elif path.suffix == ".ps1":
            text = replace_text(text, {k: v.replace("'", "''") for k, v in values.items()})
        else:
            text = replace_text(text, values)
        path.write_text(text, encoding="utf-8", newline="\n")
    config_py = output/"playwright_host/config.py"
    text = config_py.read_text(encoding="utf-8")
    a, b = text.index("TASK_CELL ="), text.index("@dataclass")
    bootstrap = [{k: v for k, v in lane.items() if k not in {"status", "task_pools"}}
                 for lane in lanes]
    text = text[:a] + f"TASK_CELL = {cell!r}\nBOOTSTRAP_LANES = {bootstrap!r}\n\n\n" + text[b:]
    config_py.write_text(text, encoding="utf-8")
    topology = {"v": 1, "topology_version": 1, "updated_at": None,
                "registered_count": len(lanes), "enabled_count": len(lanes), "lanes": lanes}
    (output/"state/lanes.json").write_text(
        json.dumps(topology, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    enabled = ",".join(lane["lane_id"] for lane in lanes)
    for rel in ("Start_CAH.ps1", "host/bridge_supervisor.ps1"):
        path = output/rel
        text = path.read_text(encoding="utf-8").replace(
            "[string]$LocalWorkerLanes = 'lane-00,lane-01'",
            f"[string]$LocalWorkerLanes = '{enabled}'", 1)
        path.write_text(text, encoding="utf-8")
    def ps_quote(value): return "'" + str(value).replace("'", "''") + "'"
    profile = cfg.get("profile_directory") or str(
        Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"CAH"/"PlaywrightChrome")
    chrome = cfg.get("chrome_path", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    path = output/"host/start_playwright_host.ps1"
    text = path.read_text(encoding="utf-8")
    text = text.replace('[string]$ChromePath = \'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\'',
                        "[string]$ChromePath = " + ps_quote(chrome))
    text = text.replace('[string]$ProfileDir = "$env:LOCALAPPDATA\\CAH\\PlaywrightChrome"',
                        "[string]$ProfileDir = " + ps_quote(profile))
    text = text.replace("[string]$ForegroundUrl = ''",
                        "[string]$ForegroundUrl = " + ps_quote(cfg["foreground_url"]))
    path.write_text(text, encoding="utf-8")
    path = output/"Start_CAH.ps1"
    text = path.read_text(encoding="utf-8").replace(
        "$profile = Join-Path $env:LOCALAPPDATA 'CAH\\PlaywrightChrome'",
        "$profile = " + ps_quote(profile))
    path.write_text(text, encoding="utf-8")
    # The exact Task Cell URL is shared by startup and explicit semantic reset.
    path = output/"host/reset_cah_semantics_cli.ps1"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"project_root_url='https://chatgpt.com/g/[^']+'",
                  lambda _: "project_root_url=" + ps_quote(cell["project_root_url"]), text, count=1)
    path.write_text(text, encoding="utf-8")
    print(f"Configured CAH: {output}")
    print(f"Repository: {cfg['repository']}; enabled lanes: {enabled}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    configure(args.config, args.output)


if __name__ == "__main__":
    main()
