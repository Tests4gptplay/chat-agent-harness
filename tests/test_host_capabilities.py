import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from harness.resource_wait import enter_resource_wait, resume_after_capability
from host.capabilities import (
    CAPABILITY_UE56,
    STATE_AVAILABLE,
    STATE_NEED_HOST_CONFIG,
    cache_path,
    resolve_capability,
    resolve_ue56,
    sanitized_projection,
    validate_projection,
)


def make_fake_ue(root: Path, major: int = 5, minor: int = 6, patch: int = 0) -> None:
    (root / "Engine" / "Build" / "BatchFiles").mkdir(parents=True, exist_ok=True)
    (root / "Engine" / "Binaries" / "Win64").mkdir(parents=True, exist_ok=True)
    (root / "Engine" / "Build" / "Build.version").write_text(
        json.dumps({
            "MajorVersion": major,
            "MinorVersion": minor,
            "PatchVersion": patch,
            "Changelist": 1,
        }),
        encoding="utf-8",
    )
    (root / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat").write_text("@echo off\n", encoding="utf-8")
    (root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe").write_bytes(b"MZ")


class HostCapabilityTests(unittest.TestCase):
    def setUp(self):
        # Fixtures must not discover the machine's real engine installation.
        for provider in ('_launcher_candidates', '_registry_candidates', '_path_candidates', '_filesystem_candidates'):
            mock = patch('host.capabilities.' + provider, return_value=[])
            mock.start()
            self.addCleanup(mock.stop)

    def test_ue56_validation_persists_private_paths_but_projection_is_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ue = base / "generic-install-name"
            local_root = base / "gah-local"
            make_fake_ue(ue, 5, 6, 2)

            projection, private = resolve_ue56(
                local_root=local_root,
                supplied_root=ue,
                env={},
                filesystem_candidates=[],
            )

            self.assertEqual(projection["state"], STATE_AVAILABLE)
            self.assertTrue(projection["available"])
            self.assertEqual(projection["version"], "5.6.2")
            self.assertEqual(projection["source"], "user_config")
            self.assertNotIn(str(ue), json.dumps(projection))

            self.assertIsNotNone(private)
            self.assertEqual(Path(private["root"]), ue.resolve())

            cache = json.loads(cache_path(local_root).read_text(encoding="utf-8"))
            record = cache["capabilities"][CAPABILITY_UE56]
            self.assertEqual(Path(record["root"]), ue.resolve())
            self.assertEqual(record["version"], "5.6.2")

            cached_projection, cached_private = resolve_ue56(
                local_root=local_root,
                env={},
                filesystem_candidates=[],
            )
            self.assertEqual(cached_projection["state"], STATE_AVAILABLE)
            self.assertEqual(cached_projection["source"], "local_cache")
            self.assertEqual(Path(cached_private["root"]), ue.resolve())

    def test_directory_name_never_substitutes_for_build_version(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ue = base / "UE5.6"
            local_root = base / "gah-local"
            make_fake_ue(ue, 5, 5, 9)

            projection, private = resolve_ue56(
                local_root=local_root,
                supplied_root=ue,
                env={},
                filesystem_candidates=[],
            )
            self.assertEqual(projection["state"], STATE_NEED_HOST_CONFIG)
            self.assertFalse(projection["available"])
            self.assertIsNone(private)
            self.assertFalse(cache_path(local_root).exists())

    def test_projection_validator_rejects_absolute_path_leak(self):
        projection = sanitized_projection(
            CAPABILITY_UE56,
            state=STATE_AVAILABLE,
            version="5.6.0",
            source="runtime",
            validation="Build.version",
        )
        projection["reason"] = r"D:\\UE5"
        with self.assertRaises(ValueError):
            validate_projection(projection)

    def test_projection_shape_matches_schema_required_keys(self):
        projection = sanitized_projection(
            CAPABILITY_UE56,
            state=STATE_NEED_HOST_CONFIG,
            version=None,
            source="missing",
            validation="Engine/Build/Build.version",
            prompt="Provide UE root",
            reason="automatic_discovery_exhausted",
        )
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "harness" / "host_capability.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(projection), set(schema["required"]))
        self.assertIn(projection["state"], schema["properties"]["state"]["enum"])
        self.assertIn(projection["source"], schema["properties"]["source"]["enum"])

    def test_unknown_capability_is_sanitized_unavailable(self):
        projection, private = resolve_capability("unknown_tool", persist=False)
        self.assertEqual(projection["state"], "UNAVAILABLE")
        self.assertFalse(projection["available"])
        self.assertEqual(projection["reason"], "unknown_capability")
        self.assertIsNone(private)

    def test_resource_wait_resumes_same_task_on_fresh_generation_and_fence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bg_path = root / "cl" / "task.backend.json"
            projection_path = root / "evidence" / "host-capabilities" / "ue.json"
            bg_path.parent.mkdir(parents=True)
            projection_path.parent.mkdir(parents=True)

            bg = {
                "v": 1,
                "cl_id": "bg-task",
                "task_id": "task-001",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "2026-09-19T00:00:00Z",
                "updated_at": "2026-09-19T00:00:00Z",
                "conditions": [],
                "dispatch": {
                    "dispatch_id": "dispatch-old-0003",
                    "wake_id": "wake-old-0003",
                    "generation": 3,
                    "fence_token": "fence-old-0003",
                    "state": "RUNNING",
                    "requested_at": "2026-09-19T00:00:00Z",
                    "delivered_at": "2026-09-19T00:00:01Z",
                    "acked_at": "2026-09-19T00:00:01Z",
                    "acked_by_worker_ref": "pool-old",
                    "lease_expires_at": None,
                    "continuation_ref": "results/task-001.json",
                    "wait_ref": None,
                    "ack_source": "extension_response_start",
                },
            }
            bg_path.write_text(json.dumps(bg), encoding="utf-8")

            wait = enter_resource_wait(
                bg_path,
                capability_id=CAPABILITY_UE56,
                prompt="Provide UE 5.6 root",
                projection_ref="evidence/host-capabilities/ue.json",
            )
            self.assertEqual(wait["state"], "WAIT_RESOURCE")
            waiting = json.loads(bg_path.read_text(encoding="utf-8"))
            self.assertEqual(waiting["dispatch"]["state"], "WAIT_RESOURCE")
            self.assertEqual(waiting["resource_wait"]["kind"], "NEED_HOST_CONFIG")
            self.assertEqual(waiting["task_id"], "task-001")

            projection = {
                "v": 1,
                "capability_id": CAPABILITY_UE56,
                "state": "AVAILABLE",
                "available": True,
                "version": "5.6.1",
                "source": "local_cache",
                "validation": "Engine/Build/Build.version",
                "observed_at": "2026-09-19T01:00:00Z",
                "prompt": None,
                "reason": None,
            }
            projection_path.write_text(json.dumps(projection), encoding="utf-8")
            resumed = resume_after_capability(bg_path, projection_path)

            self.assertTrue(resumed["scheduled"])
            self.assertEqual(resumed["task_id"], "task-001")
            self.assertEqual(resumed["dispatch_generation"], 4)
            self.assertNotEqual(resumed["dispatch_id"], "dispatch-old-0003")
            self.assertNotEqual(resumed["fence_token"], "fence-old-0003")

            after = json.loads(bg_path.read_text(encoding="utf-8"))
            self.assertEqual(after["task_id"], "task-001")
            self.assertEqual(after["overall"], "READY")
            self.assertEqual(after["dispatch"]["state"], "READY")
            self.assertEqual(after["dispatch"]["generation"], 4)
            self.assertIsNone(after["resource_wait"])
            self.assertIsNone(after["wait_ref"])


if __name__ == "__main__":
    unittest.main()
