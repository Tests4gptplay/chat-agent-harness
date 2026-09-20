import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.wake import deterministic_wake_id, make_wake, marker, validate_wake  # noqa: E402
from local_bridge.server import WakeStore  # noqa: E402


class WakeTests(unittest.TestCase):
    def test_make_validate_marker(self):
        wake = make_wake("3d-agent-lab", repo="example-owner/cah-workload", run_id=123)
        validate_wake(wake)
        self.assertEqual(wake["v"], 1)
        self.assertEqual(wake["state"], "NEED_AGENT")
        self.assertTrue(marker(wake).startswith("GAH_WAKE v=1 id="))

    def test_deterministic_wake_id(self):
        kwargs = {
            "state": "NEED_AGENT",
            "repo": "example-owner/cah-private",
            "run_id": "123",
            "result_ref": "github://example/result",
        }
        first = deterministic_wake_id("git-agent-harness", **kwargs)
        second = deterministic_wake_id("git-agent-harness", **kwargs)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            deterministic_wake_id("git-agent-harness", **{**kwargs, "result_ref": "github://example/other"}),
        )


    def test_scheduler_wake_identity(self):
        wake = make_wake(
            "git-agent-harness",
            wake_id="wake-scheduler-0001",
            repo="example-owner/cah-private",
            lane_id="lane-00",
            worker_project_key="g-p-examplelane00",
            kind="task_continue",
            task_id="task-001",
            backend_cl="cl/task-001.backend.json",
            dispatch_id="dispatch-0001",
            dispatch_generation=2,
            fence_token="fence-token-0001",
        )
        validate_wake(wake)
        self.assertEqual(wake["dispatch_id"], "dispatch-0001")
        self.assertEqual(wake["dispatch_generation"], 2)
        self.assertEqual(wake["backend_cl"], "cl/task-001.backend.json")
        with self.assertRaises(ValueError):
            make_wake("git-agent-harness", wake_id="wake:unsafe:0001")

    def test_dispatch_status_suppresses_accepted_and_stale_wakes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])
            (work / "cl").mkdir()
            backend = {
                "v": 1,
                "cl_id": "bg-task-001",
                "task_id": "task-001",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "2026-09-18T00:00:00Z",
                "updated_at": "2026-09-18T00:00:00Z",
                "dispatch": {
                    "dispatch_id": "dispatch-0001",
                    "wake_id": "wake-scheduler-0001",
                    "generation": 2,
                    "fence_token": "fence-token-0001",
                    "state": "RUNNING",
                    "requested_at": "2026-09-18T00:00:00Z",
                    "acked_at": "2026-09-18T00:00:01Z",
                    "acked_by_worker_ref": "pool-worker-0001"
                },
                "conditions": []
            }
            (work / "cl" / "task-001.backend.json").write_text(json.dumps(backend), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "cl/task-001.backend.json"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed dispatch"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            request = {
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "task_id": "task-001",
                "backend_cl": "cl/task-001.backend.json",
                "dispatch_id": "dispatch-0001",
                "dispatch_generation": 2,
                "fence_token": "fence-token-0001",
            }
            accepted = store.dispatch_status(request)
            self.assertTrue(accepted["ok"])
            self.assertTrue(accepted["matched"])
            self.assertTrue(accepted["suppress"])
            self.assertEqual(accepted["reason"], "already_accepted")
            self.assertEqual(accepted["state"], "RUNNING")

            stale = store.dispatch_status({**request, "dispatch_generation": 1})
            self.assertTrue(stale["ok"])
            self.assertFalse(stale["matched"])
            self.assertTrue(stale["suppress"])
            self.assertEqual(stale["reason"], "stale_dispatch")


    def test_dispatch_accept_moves_exact_ready_dispatch_to_running(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])
            (work / "cl").mkdir()
            (work / "tasks").mkdir()
            (work / "state").mkdir()
            task = {
                "v": 1,
                "task_id": "task-runtime-ack",
                "kind": "parallel_semantic_branch",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-runtimeack",
                "backend_cl": "cl/task-runtime-ack.backend.json",
            }
            lanes = {
                "v": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "project_key": "g-p-runtimeack",
                    "last_pool_takeover_id": "pool-runtime-worker-0001",
                }],
            }
            state = {
                "v": 1,
                "agent": "chatgpt",
                "last_pool_takeover_id": "pool-runtime-worker-0001",
            }
            backend = {
                "v": 1,
                "cl_id": "bg-runtime-ack",
                "task_id": "task-runtime-ack",
                "scope": "backend_execution",
                "overall": "READY",
                "created_at": "2026-09-18T00:00:00Z",
                "updated_at": "2026-09-18T00:00:00Z",
                "dispatch": {
                    "dispatch_id": "dispatch-runtime-0001",
                    "wake_id": "wake-runtime-0001",
                    "generation": 1,
                    "fence_token": "fence-runtime-0001",
                    "state": "READY",
                    "requested_at": "2026-09-18T00:00:00Z",
                    "delivered_at": None,
                    "acked_at": None,
                    "acked_by_worker_ref": None,
                    "lease_expires_at": None,
                    "continuation_ref": "results/task-runtime-ack.json",
                    "wait_ref": None,
                    "ack_source": None,
                },
                "conditions": [
                    {"id":"claimed","label":"Claim","state":"WAIT","detail":None,"evidence_ref":None}
                ],
            }
            (work / "cl" / "task-runtime-ack.backend.json").write_text(json.dumps(backend), encoding="utf-8")
            (work / "tasks" / "task-runtime-ack.json").write_text(json.dumps(task), encoding="utf-8")
            (work / "state" / "lanes.json").write_text(json.dumps(lanes), encoding="utf-8")
            (work / "state" / "chatgpt.json").write_text(json.dumps(state), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "cl/task-runtime-ack.backend.json", "tasks/task-runtime-ack.json", "state/lanes.json", "state/chatgpt.json"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed runtime dispatch"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            req = {
                "client_id": "client-runtime-001",
                "project_id": "git-agent-harness",
                "task_id": "task-runtime-ack",
                "backend_cl": "cl/task-runtime-ack.backend.json",
                "dispatch_id": "dispatch-runtime-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-runtime-0001",
                "worker_ref": "pool-runtime-worker-0001",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-runtimeack",
            }
            accepted = store.dispatch_accept(req)
            self.assertTrue(accepted["ok"])
            self.assertTrue(accepted["accepted"])
            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw = subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:cl/task-runtime-ack.backend.json"],
                text=True, encoding="utf-8"
            )
            got = json.loads(raw)
            self.assertEqual(got["dispatch"]["state"], "RUNNING")
            self.assertEqual(got["dispatch"]["ack_source"], "extension_response_start")
            self.assertEqual(got["dispatch"]["acked_by_worker_ref"], "pool-runtime-worker-0001")
            self.assertTrue(got["dispatch"]["lease_expires_at"])

            stale = store.dispatch_accept({**req, "fence_token": "fence-runtime-stale"})
            self.assertFalse(stale["ok"])
            self.assertEqual(stale["error"], "DISPATCH_STALE")

    def test_visual_attachment_wake_and_allowlisted_artifact_read(self):
        ref = "cases/task-001/camera/review_assets/iter_01/contact_sheet.jpg"
        wake = make_wake(
            "git-agent-harness",
            wake_id="wake-attachment-0001",
            lane_id="lane-00",
            worker_project_key="g-p-examplelane00",
            attachment_ref=ref,
        )
        validate_wake(wake)
        self.assertEqual(wake["attachment_ref"], ref)
        with self.assertRaises(ValueError):
            make_wake(
                "git-agent-harness",
                wake_id="wake-attachment-0002",
                attachment_ref="../outside.jpg",
            )

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

            image = work / ref
            image.parent.mkdir(parents=True)
            canonical_payload = b"\xff\xd8\xffGAH-CANONICAL-JPEG"
            image.write_bytes(canonical_payload)
            subprocess.check_call(["git", "-C", str(work), "add", ref])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed canonical attachment"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Contradict the local working tree after push. artifact_read must still
            # return the freshly fetched canonical Git blob, not this stale/local value.
            image.write_bytes(b"\xff\xd8\xffLOCAL-STALE")
            store = WakeStore(base / "spool", repo_root=work)
            got = store.artifact_read({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "artifact_ref": ref,
            })
            self.assertTrue(got["ok"])
            self.assertEqual(got["mime_type"], "image/jpeg")
            self.assertEqual(got["source"], "canonical_git_fetch_head")
            self.assertEqual(base64.b64decode(got["base64"]), canonical_payload)
            with self.assertRaises(ValueError):
                store.artifact_read({
                    "client_id": "client-test-001",
                    "project_id": "git-agent-harness",
                    "artifact_ref": "README.md",
                })

    def test_lane_filtered_claim_avoids_head_of_line_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            store = WakeStore(Path(td))
            older_lane0 = make_wake(
                "git-agent-harness",
                wake_id="wake-lane00-hol-0001",
                lane_id="lane-00",
                worker_project_key="g-p-test00",
            )
            older_lane0["created_at"] = "2026-09-19T00:00:00Z"
            newer_lane1 = make_wake(
                "git-agent-harness",
                wake_id="wake-lane01-hol-0001",
                lane_id="lane-01",
                worker_project_key="g-p-test01",
            )
            newer_lane1["created_at"] = "2026-09-19T00:00:01Z"
            store.emit(older_lane0)
            store.emit(newer_lane1)

            lane1 = store.claim({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "lane_id": "lane-01",
            })
            self.assertEqual(lane1["wake"]["wake_id"], "wake-lane01-hol-0001")
            self.assertEqual(lane1["wake"]["lane_id"], "lane-01")
            store.release({"client_id": "client-test-001", "message_id": lane1["message_id"]})

            lane0 = store.claim({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "lane_id": "lane-00",
            })
            self.assertEqual(lane0["wake"]["wake_id"], "wake-lane00-hol-0001")
            self.assertEqual(lane0["wake"]["lane_id"], "lane-00")

            with self.assertRaises(ValueError):
                store.claim({
                    "client_id": "client-test-001",
                    "project_id": "git-agent-harness",
                    "lane_id": "lane-bad",
                })

    def test_bad_state(self):
        with self.assertRaises(ValueError):
            make_wake("x", state="RUNNING")

    def test_local_event_spool(self):
        with tempfile.TemporaryDirectory() as td:
            store = WakeStore(Path(td))
            result = store.event({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "event": "conversation.bound",
                "level": "info",
                "data": {"tab_id": 123, "path": "/c/example"},
            })
            self.assertTrue(result["ok"])
            latest = json.loads((Path(td) / "extension-events" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["event"], "conversation.bound")
            self.assertEqual(latest["data"]["tab_id"], 123)
            self.assertTrue((Path(td) / "extension-events" / "events.jsonl").exists())

            store.event({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "event": "wake.poll_error",
                "level": "error",
                "data": {"message": "test error"},
            })
            error_latest = json.loads((Path(td) / "errors" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(error_latest["event"], "wake.poll_error")

            with self.assertRaises(ValueError):
                store.event({
                    "client_id": "client-test-001",
                    "event": "unsafe.event",
                    "data": {"secret": "must-not-be-written"},
                })

    def test_worker_takeover_status_uses_exact_canonical_git_id(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

            exact = "pool-12345678-abcd"
            state_dir = work / "state"
            state_dir.mkdir()
            state_file = state_dir / "chatgpt.json"
            state_file.write_text(json.dumps({
                "v": 1,
                "agent": "chatgpt",
                "updated": "2026-09-18",
                "last_pool_takeover_id": exact,
                "worker_rollover_request": {
                    "handoff_id": exact,
                    "reason": "context_compacted",
                    "requested_at": "2026-09-18T00:00:00Z",
                },
                "foreground_task": {
                    "task_id": "fg-test-001",
                    "status": "RUNNING",
                    "started_at": "2026-09-18T00:00:00Z",
                    "updated_at": "2026-09-18T00:05:00Z",
                    "result_ref": None,
                    "summary": "not exposed by the local projection",
                },
            }), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "state/chatgpt.json"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed takeover state"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Leave a contradictory uncommitted working-tree value. The bridge must
            # read the fetched canonical Git commit, not this local file contents.
            state_file.write_text(json.dumps({
                "v": 1,
                "agent": "chatgpt",
                "updated": "2026-09-18",
                "last_pool_takeover_id": "pool-local-stale",
            }), encoding="utf-8")

            store = WakeStore(base / "spool", repo_root=work)
            request = {
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "handoff_id": exact,
            }
            matched = store.worker_takeover_status(request)
            self.assertTrue(matched["ok"])
            self.assertTrue(matched["matched"])
            self.assertEqual(matched["last_pool_takeover_id"], exact)
            self.assertTrue(matched["rollover_requested"])
            self.assertEqual(matched["rollover_request_handoff_id"], exact)
            self.assertEqual(matched["rollover_reason"], "context_compacted")

            foreground = store.foreground_task_status({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
            })
            self.assertTrue(foreground["ok"])
            self.assertEqual(foreground["foreground_task"]["task_id"], "fg-test-001")
            self.assertEqual(foreground["foreground_task"]["status"], "RUNNING")
            self.assertEqual(foreground["foreground_task"]["started_at"], "2026-09-18T00:00:00Z")
            self.assertIsNone(foreground["foreground_task"]["result_ref"])
            self.assertNotIn("summary", foreground["foreground_task"])
            self.assertIsNotNone(foreground["state_at"])

            request["handoff_id"] = "pool-87654321-abcd"
            unmatched = store.worker_takeover_status(request)
            self.assertTrue(unmatched["ok"])
            self.assertFalse(unmatched["matched"])
            self.assertEqual(unmatched["last_pool_takeover_id"], exact)
            self.assertFalse(unmatched["rollover_requested"])
            self.assertEqual(unmatched["rollover_request_handoff_id"], exact)


    def test_lane00_takeover_falls_back_to_top_level_rollover(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])
            (work / "state").mkdir()
            handoff = "pool-aaaaaaaa-bbbb-cccc"
            (work / "state" / "chatgpt.json").write_text(json.dumps({
                "v": 1,
                "agent": "chatgpt",
                "updated": "2026-09-18",
                "last_pool_takeover_id": handoff,
                "handoff_packet_ref": "state/handoff-test.json",
                "worker_rollover_request": {
                    "handoff_id": handoff,
                    "reason": "context_compacted",
                    "requested_at": "2026-09-18T12:00:00Z"
                }
            }), encoding="utf-8")
            (work / "state" / "lanes.json").write_text(json.dumps({
                "v": 1,
                "topology_version": 2,
                "updated_at": "2026-09-18T12:00:00Z",
                "source_request_id": "topo-test-0001",
                "registered_count": 1,
                "enabled_count": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "display_name": "CAH Sandbox0",
                    "project_key": "g-p-examplelane00",
                    "project_root_url": "https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project",
                    "enabled": True,
                    "status": "IDLE",
                    "last_pool_takeover_id": handoff,
                    "worker_rollover_request": None
                }]
            }), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "state"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed lane rollover fallback"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            got = store.worker_takeover_status({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "handoff_id": handoff,
                "lane_id": "lane-00",
                "worker_project_key": "g-p-examplelane00",
            })
            self.assertTrue(got["ok"])
            self.assertTrue(got["matched"])
            self.assertTrue(got["rollover_requested"])
            self.assertEqual(got["rollover_reason"], "context_compacted")
            self.assertEqual(got["handoff_packet_ref"], "state/handoff-test.json")
            self.assertTrue(got["lane_scoped"])

    def test_lane00_takeover_accepts_exact_newer_top_level_over_stale_lane_mirror(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])
            (work / "state").mkdir()
            old = "pool-old-old-old"
            new = "pool-new-new-new"
            packet = "state/handoffs/task-new.json"
            (work / "state" / "chatgpt.json").write_text(json.dumps({
                "v": 1,
                "agent": "chatgpt",
                "updated": "2026-09-18",
                "last_pool_takeover_id": new,
                "handoff_packet_ref": packet,
                "worker_rollover_request": {
                    "v": 1,
                    "reason": "context_compacted",
                    "status": "PENDING",
                    "lane_id": "lane-00",
                    "outgoing_pool_id": new,
                    "task_id": "task-001",
                    "handoff_packet_ref": packet,
                },
            }), encoding="utf-8")
            (work / "state" / "lanes.json").write_text(json.dumps({
                "v": 1,
                "topology_version": 2,
                "updated_at": "2026-09-18T00:00:00Z",
                "source_request_id": "topo-test",
                "registered_count": 1,
                "enabled_count": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "display_name": "CAH Sandbox0",
                    "project_key": "g-p-examplelane00",
                    "project_root_url": "https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project",
                    "enabled": True,
                    "status": "HANDOFF",
                    "last_pool_takeover_id": old,
                    "worker_rollover_request": {
                        "v": 1,
                        "reason": "context_compacted",
                        "outgoing_pool_id": old,
                        "handoff_packet_ref": "state/handoffs/old.json",
                    },
                }],
            }), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "state"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed stale lane mirror"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            got = store.worker_takeover_status({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "handoff_id": new,
                "lane_id": "lane-00",
                "worker_project_key": "g-p-examplelane00",
            })
            self.assertTrue(got["ok"])
            self.assertTrue(got["matched"])
            self.assertEqual(got["last_pool_takeover_id"], new)
            self.assertTrue(got["rollover_requested"])
            self.assertEqual(got["rollover_request_handoff_id"], new)
            self.assertEqual(got["handoff_packet_ref"], packet)

    def test_state_schema_covers_canonical_top_level_keys(self):
        schema = json.loads((ROOT / "harness" / "state.schema.json").read_text(encoding="utf-8"))
        state = json.loads((ROOT / "state" / "chatgpt.json").read_text(encoding="utf-8"))
        self.assertFalse(schema.get("additionalProperties", True))
        unknown = sorted(set(state) - set(schema.get("properties", {})))
        self.assertEqual(unknown, [], f"canonical state keys missing from strict schema: {unknown}")

    def test_capability_probe_is_sanitized_and_stable_shape(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "capabilities.json"
            env = dict(__import__("os").environ)
            env["GAH_INTERACTIVE_DESKTOP"] = "false"
            subprocess.check_call([
                sys.executable,
                str(ROOT / "executors" / "probe_capabilities.py"),
                "--node-id",
                "ci-node",
                "--out",
                str(out),
            ], env=env)
            value = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(value["v"], 1)
            self.assertEqual(value["node_id"], "ci-node")
            self.assertFalse(value["interactive_desktop"])
            self.assertEqual(set(value["tools"]), {"python", "git", "blender", "unreal_editor"})
            self.assertTrue(value["tools"]["python"]["available"])
            self.assertTrue(value["tools"]["git"]["available"])
            encoded = json.dumps(value).lower()
            self.assertNotIn("hostname", encoded)
            self.assertNotIn("username", encoded)
            self.assertNotIn("executable_path", encoded)

    def test_worker_lifecycle_does_not_activate_tabs(self):
        for rel in (
            "extension/worker_runtime_v2.js",
            "extension/worker_retirement.js",
            "extension/worker_cleanup.js",
        ):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("active: true", source, f"{rel} must not steal browser focus")
            self.assertNotIn("{ active: true }", source, f"{rel} must not restore/steal browser focus")

    def test_build_manifests(self):
        with tempfile.TemporaryDirectory() as td:
            for target in ("chromium", "firefox"):
                out = Path(td) / target
                subprocess.check_call([sys.executable, str(ROOT / "extension" / "build.py"), target, "--out", str(out)])
                manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["manifest_version"], 3)
                self.assertTrue((out / "background.js").exists())
                self.assertTrue((out / "foreground_monitor.js").exists())
                self.assertTrue((out / "history_rate_limit.js").exists())
                self.assertTrue((out / "foreground_content.js").exists())
                self.assertTrue((out / "worker_root_content.js").exists())
                self.assertFalse((out / "pool_background.js").exists())
                self.assertFalse((out / "worker_runtime.js").exists())
                self.assertFalse((out / "pool_content.js").exists())
                self.assertFalse(any("script.google" in value for value in manifest.get("host_permissions", [])))
                if target == "chromium":
                    self.assertEqual(manifest["background"]["service_worker"], "background_bundle.js")
                    self.assertTrue((out / "background_bundle.js").exists())


    def test_extension_semantic_recovery_contract_is_present(self):
        content_js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
        background_js = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        lane_runtime_js = (ROOT / "extension" / "lane_worker_runtime.js").read_text(encoding="utf-8")

        self.assertIn("SYSCALL_RESCAN_MS", content_js)
        self.assertIn("gah-worker-response-ended", content_js)
        self.assertIn("worker.syscall_submit_rejected", content_js)
        self.assertIn("seenSyscallKeys", content_js)
        self.assertIn("inFlightSyscallKeys", content_js)
        self.assertIn("permanentSyscallError", content_js)
        self.assertIn("scan_in_flight", content_js)
        self.assertIn("setInterval(() =>", content_js)
        self.assertIn("dispatch_context: latestDispatchContext()", content_js)

        self.assertIn("handleWorkerResponseEnded", background_js)
        self.assertIn("semanticLivenessSweep", background_js)
        self.assertIn("op: 'dispatch_liveness'", background_js)
        self.assertIn("worker.syscall_action_submit_error", background_js)

        self.assertIn("handoff_packet_ref: handoffPacketRef || null", lane_runtime_js)
        self.assertIn("op: 'worker_handoff_complete'", lane_runtime_js)
        self.assertIn("worker.semantic_handoff_completed", lane_runtime_js)


    def test_history_access_rate_limit_modal_recovery_contract(self):
        content_js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
        helper_js = (ROOT / "extension" / "history_rate_limit.js").read_text(encoding="utf-8")
        build_py = (ROOT / "extension" / "build.py").read_text(encoding="utf-8")

        self.assertIn("isHistoryAccessRateLimitText", helper_js)
        self.assertIn("访问对话记录", helper_js)
        self.assertIn("MAX_DISMISS_ATTEMPTS = 2", helper_js)
        self.assertIn("ui.history_rate_limit_dismissed", content_js)
        self.assertIn("ui.history_rate_limit_ambiguous", content_js)
        self.assertIn("dismissHistoryAccessRateLimitModalIfPresent", content_js)
        self.assertLess(
            content_js.index("await dismissHistoryAccessRateLimitModalIfPresent();"),
            content_js.index("let composer = findComposer();"),
        )
        self.assertIn("const preSendRecovery = await dismissHistoryAccessRateLimitModalIfPresent();", content_js)
        self.assertLess(
            content_js.index("const preSendRecovery = await dismissHistoryAccessRateLimitModalIfPresent();"),
            content_js.index("findSendButton().click();"),
        )
        self.assertIn('"history_rate_limit.js"', build_py)
        self.assertIn("cah-ui-recovery-preflight", content_js)

        for rel in ("manifest.chromium.json", "manifest.firefox.json"):
            manifest = json.loads((ROOT / "extension" / rel).read_text(encoding="utf-8"))
            scripts = manifest["content_scripts"][0]["js"]
            self.assertIn("history_rate_limit.js", scripts)
            self.assertLess(scripts.index("history_rate_limit.js"), scripts.index("content.js"))
            self.assertEqual(manifest["version"], "1.0.4")


if __name__ == "__main__":
    unittest.main()
