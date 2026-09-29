#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALID_STATUS = {"ACTIVE", "EXPERIMENTAL", "DEPRECATED"}
VALID_PROVIDERS = {"playwright_host", "playwright_mcp", "local_bridge", "host_runner", "git"}
VALID_RESULT_SCOPES = {"ephemeral", "durable"}
VALID_ROLES = {"worker", "planner", "helper", "foreground"}


class ToolRegistryError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ToolRegistryError(f"{path}: expected JSON object")
    return value


class ToolRegistry:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.registry_path = self.root / "harness" / "tools" / "registry.json"
        self.schema_path = self.root / "harness" / "tool.schema.json"
        schema = load_json(self.schema_path)
        try:
            pattern = str(schema["properties"]["tool_id"]["pattern"])
        except (KeyError, TypeError) as exc:
            raise ToolRegistryError("tool schema missing tool_id pattern") from exc
        self.tool_id_re = re.compile(pattern)

    def safe_ref(self, value: str) -> Path:
        rel = Path(str(value))
        if rel.is_absolute() or ".." in rel.parts:
            raise ToolRegistryError(f"unsafe repository-relative ref: {value}")
        out = (self.root / rel).resolve()
        try:
            out.relative_to(self.root)
        except ValueError as exc:
            raise ToolRegistryError(f"ref escapes repository root: {value}") from exc
        if not out.exists():
            raise ToolRegistryError(f"missing implementation ref: {value}")
        return out

    def load_registry(self) -> dict[str, Any]:
        registry = load_json(self.registry_path)
        if registry.get("v") != 1:
            raise ToolRegistryError("tool registry v must be 1")
        tools = registry.get("tools")
        if not isinstance(tools, list):
            raise ToolRegistryError("tool registry tools must be a list")
        return registry

    def validate_tool(self, tool: dict[str, Any]) -> None:
        required = {
            "v", "tool_id", "title", "status", "summary", "provider",
            "mutating", "result_scope", "roles", "args_schema",
            "implementation_refs",
        }
        missing = sorted(required - set(tool))
        if missing:
            raise ToolRegistryError(f"tool missing fields: {missing}")
        if set(tool) - required:
            raise ToolRegistryError(f"tool has unsupported fields: {sorted(set(tool) - required)}")
        if tool.get("v") != 1:
            raise ToolRegistryError("tool v must be 1")

        tool_id = str(tool.get("tool_id") or "")
        if not self.tool_id_re.fullmatch(tool_id):
            raise ToolRegistryError(f"invalid tool_id: {tool_id!r}")
        if str(tool.get("status") or "") not in VALID_STATUS:
            raise ToolRegistryError(f"invalid tool status for {tool_id}")
        if str(tool.get("provider") or "") not in VALID_PROVIDERS:
            raise ToolRegistryError(f"invalid provider for {tool_id}")
        if not isinstance(tool.get("mutating"), bool):
            raise ToolRegistryError(f"mutating must be boolean for {tool_id}")
        if str(tool.get("result_scope") or "") not in VALID_RESULT_SCOPES:
            raise ToolRegistryError(f"invalid result_scope for {tool_id}")
        if not str(tool.get("title") or "").strip() or not str(tool.get("summary") or "").strip():
            raise ToolRegistryError(f"title and summary are required for {tool_id}")

        roles = tool.get("roles")
        if not isinstance(roles, list) or not roles or len(roles) != len(set(roles)):
            raise ToolRegistryError(f"roles must be a unique non-empty list for {tool_id}")
        if any(str(role) not in VALID_ROLES for role in roles):
            raise ToolRegistryError(f"invalid role for {tool_id}")

        args_schema = tool.get("args_schema")
        if not isinstance(args_schema, dict) or args_schema.get("type") != "object":
            raise ToolRegistryError(f"args_schema must be an object schema for {tool_id}")

        refs = tool.get("implementation_refs")
        if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
            raise ToolRegistryError(f"implementation_refs must be a unique non-empty list for {tool_id}")
        for ref in refs:
            self.safe_ref(str(ref))

    def validate_all(self) -> dict[str, Any]:
        registry = self.load_registry()
        seen: set[str] = set()
        active = 0
        for tool in registry["tools"]:
            if not isinstance(tool, dict):
                raise ToolRegistryError("tool entry must be an object")
            self.validate_tool(tool)
            tool_id = str(tool["tool_id"])
            if tool_id in seen:
                raise ToolRegistryError(f"duplicate tool_id: {tool_id}")
            seen.add(tool_id)
            if tool["status"] == "ACTIVE":
                active += 1
        return {"ok": True, "count": len(seen), "active_count": active, "tool_ids": sorted(seen)}

    def get(self, tool_id: str, *, include_inactive: bool = False) -> dict[str, Any] | None:
        self.validate_all()
        for tool in self.load_registry()["tools"]:
            if tool["tool_id"] == tool_id:
                if include_inactive or tool["status"] == "ACTIVE":
                    return tool
                return None
        return None

    def list_for_role(self, role: str) -> list[dict[str, Any]]:
        role = str(role)
        if role not in VALID_ROLES:
            raise ToolRegistryError(f"invalid role: {role}")
        self.validate_all()
        return [
            tool for tool in self.load_registry()["tools"]
            if tool["status"] == "ACTIVE" and role in tool["roles"]
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description="CAH Tool Registry")
    parser.add_argument("--root", default=str(ROOT))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate-all")
    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("--tool", required=True)
    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--role", required=True)
    args = parser.parse_args()

    registry = ToolRegistry(Path(args.root))
    if args.cmd == "validate-all":
        out = registry.validate_all()
    elif args.cmd == "get":
        out = registry.get(args.tool)
        if out is None:
            raise SystemExit(f"tool unavailable: {args.tool}")
    else:
        out = {"role": args.role, "tools": registry.list_for_role(args.role)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
