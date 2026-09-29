import copy
import os
import tempfile
import unittest
from pathlib import Path

from local_bridge.planner_control import (
    PlannerControlError,
    bind_planner_successor,
    claim_planner_events,
    complete_planner_turn,
    deterministic_planner_fence,
    ensure_orphan_continuation,
    insert_planner_event,
    is_quiet_state,
    make_planner_event,
    migrate_flat_planner_binding,
    needs_orphan_continuation,
    planner_event_id,
    stage_planner_successor,
    state_digest,
    validate_authority,
)
from local_bridge.planner_memory import (
    PlannerMemoryError,
    bounded_growth_accounting,
    checkpoint_planner_current,
    cleanup_manifest_digest,
    collapse_terminal_projection,
    make_planner_current,
    memory_blob_sha,
    plan_cleanup_apply,
    planner_current_path,
    planner_generation_path,
    planner_handoff_path,
    prepare_planner_handoff,
    promote_planner_authority_with_memory,
    record_predecessor_retirement_result,
    record_successor_binding,
    seal_planner_generation,
    select_compact_audit_final,
    terminalize_planner_memory,
    validate_cleanup_manifest,
    validate_planner_memory_write,
    validate_predecessor_retirement_eligibility,
    validate_worker_memory_target,
)


TASK = "planner-hybrid-control-082"
EPOCH = 1
GEN = 1
FENCE = "planner-fence-g1"


def base_control(activity="ACTIVE"):
    return {
        "v": 1,
        "feature": "HYBRID_ACTIVE",
        "enabled": True,
        "task_id": TASK,
        "control_epoch": EPOCH,
        "activity": activity,
        "authority": {
            "control_epoch": EPOCH,
            "planner_generation": GEN,
            "planner_fence_token": FENCE,
            "conversation_id": "planner-old",
            "request_id": "planner-request",
            "challenge": "planner-challenge",
            "takeover_handoff_id": None,
            "project_key": "g-p-task-cell",
        },
        "wait": None,
        "inbox": {"events": {}, "active_doorbell": None},
        "last_decision": None,
        "successor": {
            "state": "NONE",
            "handoff_id": None,
            "from_generation": None,
            "to_generation": None,
            "packet_ref": None,
            "candidate_conversation_id": None,
            "candidate_request_id": None,
            "candidate_challenge": None,
            "pending_fence_token": None,
        },
        "retired_planners": [],
        "final_notice": {"event_id": None, "state": None},
        "semantic_authority_closed": False,
    }


def worker_event(result_sha="abc"):
    return make_planner_event(
        task_id=TASK,
        control_epoch=EPOCH,
        kind="worker_result",
        source_role="worker",
        source_identity={
            "child_task_id": "child-a",
            "backend_cl": "cl/child-a.backend.json",
            "dispatch_id": "dispatch-a",
            "dispatch_generation": 1,
            "fence_token": "fence-a",
            "result_ref": "cases/child-a/result.json",
            "result_blob_sha": result_sha,
        },
        refs=["cases/child-a/result.json"],
    )


def current_memory():
    return make_planner_current(
        task_id=TASK,
        control_epoch=EPOCH,
        planner_generation=1,
        planner_fence_token=FENCE,
        task_contract_ref=f"tasks/{TASK}.json",
        task_contract_revision=3,
        task_contract_blob_sha="task-sha",
        plan_ref=f"tasks/{TASK}.plan.json",
        plan_blob_sha="plan-sha",
        written_at="2026-09-21T00:00:00Z",
    )


def prepared_handoff():
    current = current_memory()
    sealed = seal_planner_generation(
        current,
        conversation_identity={
            "project_key": "g-p-task-cell",
            "conversation_id": "planner-old",
        },
        started_at="2026-09-21T00:00:00Z",
        sealed_at="2026-09-21T00:02:00Z",
        seal_reason="ROLLOVER",
        source_current_ref=planner_current_path(TASK),
        source_current_blob_sha=memory_blob_sha(current),
        successor_handoff_id_or_null="h1",
    )
    handoff = prepare_planner_handoff(
        current,
        sealed,
        handoff_id="h1",
        reason="context_compacted",
        pending_successor_fence_token="planner-fence-g2",
        current_memory_ref=planner_current_path(TASK),
        current_memory_blob_sha=memory_blob_sha(current),
        sealed_generation_ref=planner_generation_path(TASK, 1),
        sealed_generation_blob_sha=memory_blob_sha(sealed),
        canonical_task_cell_ref=f"state/task_cells/{TASK}.json",
        canonical_task_cell_blob_sha="cell-sha",
        predecessor_project_key="g-p-task-cell",
        predecessor_conversation_id="planner-old",
    )
    handoff = record_successor_binding(
        handoff,
        conversation_id="planner-new",
        request_id="planner-new-request",
        challenge="planner-new-challenge",
        project_key="g-p-task-cell",
    )
    return current, sealed, handoff


def cleanup_candidate(
    *,
    cid,
    action,
    resource_kind,
    identity,
    retention,
    storage,
    root_id="root",
    task_owner=TASK,
    gate=None,
):
    return {
        "candidate_id": cid,
        "action": action,
        "resource_kind": resource_kind,
        "path_or_exact_resource_identity": identity,
        "task_owner": task_owner,
        "retention_class": retention,
        "storage_policy_class": storage,
        "managed_root_id_or_task_cell_project_key": root_id,
        "reason": "synthetic-test",
        "dependency_refs": [],
        "expected_blob_sha_or_identity_digest": "",
        "delete_after_gate": gate,
    }


def cleanup_manifest(delete, preserve=None, dependency_checks=None):
    manifest = {
        "schema_version": 1,
        "role": "planner",
        "task_id": TASK,
        "control_epoch": EPOCH,
        "cleanup_generation": 1,
        "manifest_id": "cleanup-1",
        "manifest_digest": "",
        "terminal_decision_ref": "evidence/final-decision.json",
        "terminal_decision_blob_sha": "decision-sha",
        "final_planner_generation": 2,
        "final_planner_fence_token": "planner-fence-g2",
        "final_terminal_memory_ref": planner_generation_path(TASK, 2),
        "final_terminal_memory_blob_sha": "terminal-memory-sha",
        "delete_candidates": list(delete),
        "preserve_candidates": list(preserve or []),
        "protected_refs_snapshot": [],
        "rollback_window_refs": [],
        "dependency_checks": dict(dependency_checks or {}),
        "created_at": "2026-09-21T00:10:00Z",
        "cleanup_status": "PREPARED",
    }
    manifest["manifest_digest"] = cleanup_manifest_digest(manifest)
    return manifest


class PlannerHybridControlTests(unittest.TestCase):
    def test_complete_planner_turn_consumes_claimed_doorbell(self):
        control = base_control()
        event = worker_event()
        control, _ = insert_planner_event(control, event)
        control, doorbell = claim_planner_events(
            control,
            task_id=TASK,
            control_epoch=EPOCH,
            planner_generation=GEN,
            planner_fence_token=FENCE,
            claimed_at="2026-09-25T00:00:00Z",
        )
        completed = complete_planner_turn(
            control,
            doorbell_id=doorbell["doorbell_id"],
            consumed_at="2026-09-25T00:01:00Z",
        )
        self.assertIsNone(completed["inbox"]["active_doorbell"])
        self.assertEqual(completed["inbox"]["events"][event["event_id"]]["state"], "CONSUMED")

    def test_event_identity_is_stable_and_dedupes(self):
        a = worker_event("same-sha")
        b = worker_event("same-sha")
        self.assertEqual(a["event_id"], b["event_id"])
        control = base_control()
        first, inserted = insert_planner_event(control, a, expected_digest=state_digest(control))
        self.assertTrue(inserted)
        second, inserted = insert_planner_event(first, b)
        self.assertFalse(inserted)
        self.assertEqual(len(second["inbox"]["events"]), 1)
        changed = worker_event("different-sha")
        self.assertNotEqual(a["event_id"], changed["event_id"])

    def test_event_id_ignores_transport_time_and_rejects_bad_source(self):
        source = worker_event()["source_identity"]
        one = planner_event_id(TASK, EPOCH, "worker_result", "worker", source)
        two = planner_event_id(TASK, EPOCH, "worker_result", "worker", copy.deepcopy(source))
        self.assertEqual(one, two)
        bad = copy.deepcopy(source)
        bad.pop("dispatch_id")
        with self.assertRaises(PlannerControlError) as ctx:
            planner_event_id(TASK, EPOCH, "worker_result", "worker", bad)
        self.assertEqual(ctx.exception.code, "PLANNER_EVENT_SOURCE_IDENTITY_MISSING")

    def test_cas_and_stale_fencing_are_non_mutating(self):
        control = base_control()
        before = copy.deepcopy(control)
        with self.assertRaises(PlannerControlError) as ctx:
            insert_planner_event(control, worker_event(), expected_digest="stale")
        self.assertEqual(ctx.exception.code, "PLANNER_CONTROL_CAS_MISMATCH")
        self.assertEqual(control, before)
        with self.assertRaises(PlannerControlError) as ctx:
            validate_authority(
                control,
                task_id=TASK,
                control_epoch=EPOCH,
                planner_generation=99,
                planner_fence_token="wrong",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_STALE_GENERATION_OR_FENCE")

    def test_doorbell_is_deterministic_and_late_event_waits(self):
        control, _ = insert_planner_event(base_control(), worker_event("a"))
        claimed, doorbell = claim_planner_events(
            control,
            task_id=TASK,
            control_epoch=EPOCH,
            planner_generation=1,
            planner_fence_token=FENCE,
            claimed_at="t1",
        )
        self.assertIsNotNone(doorbell)
        late, _ = insert_planner_event(claimed, worker_event("b"))
        self.assertEqual(late["inbox"]["events"][worker_event("b")["event_id"]]["state"], "PENDING")
        with self.assertRaises(PlannerControlError) as ctx:
            claim_planner_events(
                late,
                task_id=TASK,
                control_epoch=EPOCH,
                planner_generation=1,
                planner_fence_token=FENCE,
                claimed_at="t2",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_DOORBELL_ALREADY_ACTIVE")

    def test_quiet_states_and_orphan_continuation(self):
        parked = base_control("PARKED_WAIT_EVENT")
        parked["wait"] = {
            "kind": "WAIT_DEP",
            "selector": {"dependency_key": "#79"},
            "refs": ["issue/79"],
        }
        self.assertFalse(
            needs_orphan_continuation(
                parked, task_terminal=False, actionable_work_remains=True
            )
        )
        orphan = base_control("")
        self.assertTrue(
            needs_orphan_continuation(
                orphan, task_terminal=False, actionable_work_remains=True
            )
        )
        one, inserted = ensure_orphan_continuation(
            orphan, canonical_task_state_blob_sha="cell-sha"
        )
        self.assertTrue(inserted)
        two, inserted = ensure_orphan_continuation(
            one, canonical_task_state_blob_sha="cell-sha"
        )
        self.assertFalse(inserted)
        self.assertEqual(len(two["inbox"]["events"]), 1)
        terminal = base_control("DONE")
        terminal["semantic_authority_closed"] = True
        self.assertTrue(is_quiet_state(terminal))

    def test_flat_migration_seeds_generation_one_without_new_chat(self):
        cell = {
            "task_id": TASK,
            "control_epoch": 1,
            "task_cell_project_key": "g-p-task-cell",
            "status": "BOOTSTRAPPING",
            "roles": {
                "planner": {
                    "conversation_id": "planner-existing",
                    "request_id": "planner-request",
                    "challenge": "planner-challenge",
                    "response_started": True,
                    "semantic_ready": False,
                }
            },
        }
        migrated = migrate_flat_planner_binding(
            cell,
            admission_ok=True,
            task_contract_ref=f"tasks/{TASK}.json",
            task_contract_revision=3,
            task_contract_blob_sha="task-sha",
            plan_ref=f"tasks/{TASK}.plan.json",
            plan_blob_sha="plan-sha",
        )
        planner = migrated["roles"]["planner"]
        self.assertEqual(planner["conversation_id"], "planner-existing")
        self.assertEqual(planner["planner_generation"], 1)
        self.assertFalse(migrated["planner_control"]["migration"]["created_new_conversation"])
        expected = deterministic_planner_fence(
            TASK, 1, "planner-existing", "planner-request", "planner-challenge", 1
        )
        self.assertEqual(planner["planner_fence_token"], expected)

    def test_rotation_fences_outgoing_generation_and_blocks_dual_authority(self):
        staged = stage_planner_successor(
            base_control(),
            handoff_id="h1",
            reason="context_compacted",
            pending_fence_token="planner-fence-g2",
        )
        self.assertEqual(staged["activity"], "ROTATING")
        self.assertFalse(staged["authority"]["semantic_authority"])
        self.assertTrue(staged["authority"]["fenced_for_rotation"])

        with self.assertRaises(PlannerControlError) as ctx:
            validate_authority(
                staged,
                task_id=TASK,
                control_epoch=1,
                planner_generation=1,
                planner_fence_token=FENCE,
            )
        self.assertEqual(ctx.exception.code, "PLANNER_STALE_GENERATION_OR_FENCE")

        with self.assertRaises(PlannerControlError) as ctx:
            stage_planner_successor(
                staged,
                handoff_id="h2",
                reason="context_compacted",
                pending_fence_token="planner-fence-g3",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_SUCCESSOR_ALREADY_EXISTS")

        bound = bind_planner_successor(
            staged,
            handoff_id="h1",
            candidate_conversation_id="planner-new",
            candidate_request_id="req-new",
            candidate_challenge="challenge-new",
            packet_ref=planner_handoff_path(TASK, "h1"),
        )
        with self.assertRaises(PlannerControlError) as ctx:
            validate_authority(
                bound,
                task_id=TASK,
                control_epoch=1,
                planner_generation=2,
                planner_fence_token="planner-fence-g2",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_NOT_YET_ACTIVE")



class PlannerMemoryTests(unittest.TestCase):
    def test_path_role_and_cross_task_guards(self):
        exact = planner_current_path(TASK)
        self.assertEqual(
            validate_planner_memory_write(
                exact, task_id=TASK, writer_role="planner", content_task_id=TASK
            ),
            exact,
        )
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_planner_memory_write(
                exact, task_id=TASK, writer_role="worker", content_task_id=TASK
            )
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_ROLE_MISMATCH")
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_planner_memory_write(
                "memory/planner/other/current.json",
                task_id=TASK,
                writer_role="planner",
                content_task_id=TASK,
            )
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_PATH_SCOPE_VIOLATION")
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_planner_memory_write(
                exact, task_id=TASK, writer_role="planner", content_task_id="other"
            )
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_TASK_MISMATCH")
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_worker_memory_target(exact)
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_ROLE_MISMATCH")

    def test_memory_cas_and_meaningful_checkpoint(self):
        current = current_memory()
        updated = checkpoint_planner_current(
            current,
            {"plan_blob_sha": "plan-sha-2"},
            expected_memory_version=1,
            trigger="PLAN_CHANGED",
            written_at="t2",
        )
        self.assertEqual(updated["memory_version"], 2)
        with self.assertRaises(PlannerMemoryError) as ctx:
            checkpoint_planner_current(
                current,
                {"plan_blob_sha": "plan-sha-3"},
                expected_memory_version=2,
                trigger="PLAN_CHANGED",
                written_at="t3",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_CAS_MISMATCH")
        with self.assertRaises(PlannerMemoryError) as ctx:
            checkpoint_planner_current(
                updated,
                {},
                expected_memory_version=2,
                trigger="DECISION_CHANGED",
                written_at="t4",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_CHECKPOINT_NOT_MEANINGFUL")

    def test_restart_serializable_memory_and_sealed_generation(self):
        current = current_memory()
        serialized = copy.deepcopy(current)
        self.assertEqual(serialized, current)
        sealed = seal_planner_generation(
            current,
            conversation_identity={
                "project_key": "g-p-task-cell",
                "conversation_id": "planner-old",
            },
            started_at="t0",
            sealed_at="t1",
            seal_reason="ROLLOVER",
            source_current_ref=planner_current_path(TASK),
            source_current_blob_sha=memory_blob_sha(current),
            successor_handoff_id_or_null="h1",
        )
        self.assertTrue(sealed["sealed"])
        self.assertEqual(sealed["retention_class"], "HANDOFF_ONLY")
        self.assertEqual(planner_generation_path(TASK, 1).split("/")[-1], "planner-g0001.json")

    def test_harness_binds_replacement_authority_without_takeover_ack(self):
        current, sealed, handoff = prepared_handoff()
        control = base_control("ROTATING")
        control["authority"]["semantic_authority"] = False
        control["authority"]["fenced_for_rotation"] = True
        control["successor"] = {
            "state": "BOUND",
            "handoff_id": "h1",
            "from_generation": 1,
            "to_generation": 2,
            "pending_fence_token": "planner-fence-g2",
            "candidate_conversation_id": "planner-new",
            "candidate_request_id": "planner-new-request",
            "candidate_challenge": "planner-new-challenge",
        }
        bundle = promote_planner_authority_with_memory(
            current,
            handoff,
            control,
            expected_memory_version=current["memory_version"],
            promotion_ref="replacement-route:planner-new",
            promoted_at="t3",
        )
        self.assertTrue(bundle["atomic_bundle_required"])
        self.assertEqual(bundle["current"]["planner_generation"], 2)
        self.assertEqual(bundle["planner_control"]["authority"]["conversation_id"], "planner-new")
        self.assertTrue(bundle["planner_control"]["authority"]["semantic_authority"])
        self.assertFalse(bundle["planner_control"]["authority"]["fenced_for_rotation"])
        self.assertEqual(bundle["planner_control"]["successor"]["state"], "PROMOTED")
        self.assertEqual(bundle["handoff"]["handoff_state"], "PROMOTED")
        self.assertTrue(bundle["handoff"]["promotion"]["predecessor_authority_revoked"])
        self.assertEqual(bundle["handoff"]["promotion"]["promotion_owner"], "HARNESS")

    def test_delete_before_replacement_authority_rejected_and_delayed_delete_cannot_restore(self):
        current, sealed, handoff = prepared_handoff()
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_predecessor_retirement_eligibility(
                handoff,
                base_control(),
                task_cell_project_key="g-p-task-cell",
            )
        self.assertEqual(ctx.exception.code, "PREDECESSOR_DELETE_BEFORE_VERIFIED_TAKEOVER")

        control = base_control("ROTATING")
        control["authority"]["semantic_authority"] = False
        control["authority"]["fenced_for_rotation"] = True
        control["successor"] = {
            "state": "BOUND",
            "handoff_id": "h1",
            "from_generation": 1,
            "to_generation": 2,
            "pending_fence_token": "planner-fence-g2",
            "candidate_conversation_id": "planner-new",
            "candidate_request_id": "planner-new-request",
            "candidate_challenge": "planner-new-challenge",
        }
        bundle = promote_planner_authority_with_memory(
            current,
            handoff,
            control,
            expected_memory_version=current["memory_version"],
            promotion_ref="replacement-route:planner-new",
            promoted_at="t3",
        )
        eligibility = validate_predecessor_retirement_eligibility(
            bundle["handoff"],
            bundle["planner_control"],
            task_cell_project_key="g-p-task-cell",
            protected_conversation_ids=["planner-new"],
        )
        self.assertTrue(eligibility["eligible"])
        retired = record_predecessor_retirement_result(bundle["handoff"], status="RETIRED")
        self.assertEqual(retired["handoff_state"], "RETIRED")
        self.assertEqual(bundle["planner_control"]["authority"]["planner_generation"], 2)
        self.assertEqual(bundle["planner_control"]["authority"]["conversation_id"], "planner-new")

    def test_terminal_memory_is_read_only(self):
        current = current_memory()
        terminal = terminalize_planner_memory(
            current,
            expected_memory_version=1,
            final_refs={"decision_ref": "evidence/final.json"},
            written_at="t5",
        )
        self.assertTrue(terminal["terminal"])
        self.assertTrue(terminal["read_only"])
        self.assertEqual(terminal["retention_class"], "AUDIT_FINAL")
        with self.assertRaises(PlannerMemoryError) as ctx:
            checkpoint_planner_current(
                terminal,
                {"plan_blob_sha": "late-plan-sha"},
                expected_memory_version=2,
                trigger="PLAN_CHANGED",
                written_at="t6",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_MEMORY_READ_ONLY")


class PlannerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "managed"
        self.root.mkdir()
        self.roots = {"root": str(self.root)}

    def test_source_is_never_deleted(self):
        source = self.root / "source.blend"
        source.write_bytes(b"x")
        candidate = cleanup_candidate(
            cid="source",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(source),
            retention="EPHEMERAL",
            storage="SOURCE",
        )
        result = plan_cleanup_apply(cleanup_manifest([candidate]), managed_roots=self.roots)
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["candidate_results"][0]["status"], "PRESERVE_SOURCE")
        self.assertTrue(source.exists())

    def test_cross_task_and_outside_root_fail_closed(self):
        inside = self.root / "scratch.tmp"
        inside.write_bytes(b"x")
        cross = cleanup_candidate(
            cid="cross",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(inside),
            retention="EPHEMERAL",
            storage="SCRATCH",
            task_owner="other",
        )
        manifest = cleanup_manifest([cross])
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_cleanup_manifest(manifest, expected_task_id=TASK)
        self.assertEqual(ctx.exception.code, "CLEANUP_TASK_OWNERSHIP_MISMATCH")

        outside = Path(self.tmp.name) / "outside.tmp"
        outside.write_bytes(b"x")
        candidate = cleanup_candidate(
            cid="outside",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(outside),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        result = plan_cleanup_apply(cleanup_manifest([candidate]), managed_roots=self.roots)
        self.assertEqual(result["status"], "CLEANUP_ERROR")
        self.assertIn("CLEANUP_OUTSIDE_MANAGED_ROOT", result["failure_codes"])
        self.assertTrue(outside.exists())

    def test_symlink_escape_fails_closed_when_supported(self):
        outside = Path(self.tmp.name) / "outside-dir"
        outside.mkdir()
        victim = outside / "victim.tmp"
        victim.write_bytes(b"x")
        link = self.root / "escape"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        candidate = cleanup_candidate(
            cid="escape",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(link / "victim.tmp"),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        result = plan_cleanup_apply(cleanup_manifest([candidate]), managed_roots=self.roots)
        self.assertEqual(result["status"], "CLEANUP_ERROR")
        self.assertIn("CLEANUP_OUTSIDE_MANAGED_ROOT", result["failure_codes"])
        self.assertTrue(victim.exists())

    def test_cleanup_plan_is_idempotent_and_never_deletes(self):
        target = self.root / "scratch.tmp"
        target.write_bytes(b"payload")
        candidate = cleanup_candidate(
            cid="scratch",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(target),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        manifest = cleanup_manifest([candidate])
        first = plan_cleanup_apply(manifest, managed_roots=self.roots)
        second = plan_cleanup_apply(
            manifest,
            managed_roots=self.roots,
            started_manifest_digest=manifest["manifest_digest"],
        )
        self.assertEqual(first, second)
        self.assertEqual(first["candidate_results"][0]["status"], "WOULD_DELETE")
        self.assertTrue(first["dry_run_only"])
        self.assertTrue(target.exists())

    def test_changed_manifest_after_start_is_rejected(self):
        target = self.root / "scratch.tmp"
        target.write_bytes(b"x")
        candidate = cleanup_candidate(
            cid="scratch",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(target),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        manifest = cleanup_manifest([candidate])
        started = manifest["manifest_digest"]
        changed = copy.deepcopy(manifest)
        changed["cleanup_status"] = "APPLYING"
        changed["manifest_digest"] = cleanup_manifest_digest(changed)
        with self.assertRaises(PlannerMemoryError) as ctx:
            plan_cleanup_apply(
                changed,
                managed_roots=self.roots,
                started_manifest_digest=started,
            )
        self.assertEqual(ctx.exception.code, "CLEANUP_MANIFEST_CHANGED")

    def test_protected_and_rollback_candidates_are_preserved(self):
        protected = self.root / "lkg.log"
        protected.write_bytes(b"x")
        a = cleanup_candidate(
            cid="protected",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(protected),
            retention="EPHEMERAL",
            storage="EVIDENCE",
        )
        b = cleanup_candidate(
            cid="rollback",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(self.root / "rollback.bin"),
            retention="ROLLBACK_WINDOW",
            storage="EVIDENCE",
        )
        manifest = cleanup_manifest([a, b])
        manifest["protected_refs_snapshot"] = [str(protected.resolve())]
        manifest["manifest_digest"] = cleanup_manifest_digest(manifest)
        result = plan_cleanup_apply(manifest, managed_roots=self.roots)
        statuses = {x["candidate_id"]: x["status"] for x in result["candidate_results"]}
        self.assertEqual(statuses["protected"], "PRESERVE_PROTECTED")
        self.assertEqual(statuses["rollback"], "DEFER_ROLLBACK")
        self.assertTrue(protected.exists())

    def test_handoff_only_without_verified_gate_is_deferred(self):
        target = self.root / "handoff.tmp"
        target.write_bytes(b"x")
        candidate = cleanup_candidate(
            cid="handoff",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(target),
            retention="HANDOFF_ONLY",
            storage="EVIDENCE",
        )
        result = plan_cleanup_apply(cleanup_manifest([candidate]), managed_roots=self.roots)
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["candidate_results"][0]["status"], "DEFER_GATE")
        self.assertEqual(
            result["candidate_results"][0]["gate"],
            "verified_successor_takeover_or_final_delivery",
        )
        self.assertTrue(target.exists())

    def test_unclassified_cleanup_candidate_fails_closed(self):
        target = self.root / "unknown.tmp"
        target.write_bytes(b"x")
        candidate = cleanup_candidate(
            cid="unknown",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(target),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        candidate["retention_class"] = ""
        manifest = cleanup_manifest([candidate])
        with self.assertRaises(PlannerMemoryError) as ctx:
            validate_cleanup_manifest(manifest, expected_task_id=TASK)
        self.assertEqual(ctx.exception.code, "CLEANUP_UNCLASSIFIED")
        self.assertTrue(target.exists())

    def test_cleanup_identity_digest_mismatch_fails_closed(self):
        target = self.root / "hashed.tmp"
        target.write_bytes(b"actual")
        candidate = cleanup_candidate(
            cid="hashed",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(target),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        candidate["expected_blob_sha_or_identity_digest"] = "0" * 64
        manifest = cleanup_manifest([candidate])
        result = plan_cleanup_apply(manifest, managed_roots=self.roots)
        self.assertEqual(result["status"], "CLEANUP_ERROR")
        self.assertIn("CLEANUP_IDENTITY_DIGEST_MISMATCH", result["failure_codes"])
        self.assertTrue(target.exists())

    def test_already_absent_exact_target_is_idempotent_success(self):
        missing = self.root / "gone.tmp"
        candidate = cleanup_candidate(
            cid="gone",
            action="DELETE",
            resource_kind="MANAGED_PATH",
            identity=str(missing),
            retention="EPHEMERAL",
            storage="SCRATCH",
        )
        result = plan_cleanup_apply(cleanup_manifest([candidate]), managed_roots=self.roots)
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["already_absent_count"], 1)
        self.assertEqual(result["candidate_results"][0]["status"], "ALREADY_ABSENT")

    def test_terminal_projection_collapses_mutable_runtime(self):
        control = base_control("ACTIVE")
        control["wait"] = {"kind": "WAIT_RESULT", "selector": {"x": 1}}
        control["inbox"]["events"]["e"] = {"state": "CONSUMED"}
        control["successor"]["state"] = "TAKEOVER_VERIFIED"
        collapsed = collapse_terminal_projection(
            control,
            terminal_refs={"decision": "evidence/final.json", "cleanup": "evidence/cleanup.json"},
        )
        self.assertEqual(collapsed["activity"], "DONE")
        self.assertTrue(collapsed["semantic_authority_closed"])
        self.assertIsNone(collapsed["wait"])
        self.assertEqual(collapsed["inbox"], {"events": {}, "active_doorbell": None})
        self.assertEqual(collapsed["successor"]["state"], "NONE")
        self.assertNotIn("runtime", collapsed)
        self.assertNotIn("cleanup", collapsed)

    def test_compact_audit_and_bounded_growth(self):
        selected = select_compact_audit_final(
            [
                {"ref": "final-task", "retention_class": "AUDIT_FINAL", "kind": "COMPACT_JSON"},
                {"ref": "rollback", "retention_class": "ROLLBACK_WINDOW", "rollback_live": True, "kind": "EVIDENCE"},
                {"ref": "chat", "retention_class": "AUDIT_FINAL", "kind": "RAW_CHAT"},
                {"ref": "log", "retention_class": "AUDIT_FINAL", "kind": "RAW_LOG"},
            ],
            max_audit_final_items=4,
        )
        self.assertEqual([x["ref"] for x in selected["audit_final"]], ["final-task"])
        self.assertEqual([x["ref"] for x in selected["rollback_window"]], ["rollback"])
        self.assertEqual(set(selected["excluded_raw_continuity"]), {"chat", "log"})

        rows = [
            {
                "audit_final_objects": 5,
                "audit_final_bytes": 1000,
                "rollback_objects": 1,
                "rollback_bytes": 200,
                "raw_history_objects": 0,
            }
            for _ in range(20)
        ]
        growth = bounded_growth_accounting(rows, max_audit_final_objects_per_task=6)
        self.assertTrue(growth["bounded"])
        self.assertEqual(growth["audit_final_objects"], 100)
        bad = copy.deepcopy(rows)
        bad[0]["raw_history_objects"] = 1
        self.assertFalse(
            bounded_growth_accounting(bad, max_audit_final_objects_per_task=6)["bounded"]
        )


if __name__ == "__main__":
    unittest.main()
