#!/usr/bin/env python3
from __future__ import annotations

import unittest

from local_bridge.task_cell_ledgers import (
    SLOT_COUNT,
    make_planner_turn_outcome,
    make_planner_turn_slot,
    planner_plan_note_path,
    planner_semantic_memory_path,
    planner_slot_has_write,
    planner_turn_memory_entry_header,
    planner_turn_is_committed,
    planner_turn_slot_path,
    render_child_reply_entry,
    worker_child_reply_path,
)


class TaskCellLedgerTests(unittest.TestCase):
    def test_paths_are_stable_and_task_scoped(self):
        self.assertEqual(
            planner_semantic_memory_path("task-1"),
            "memory/planner/task-1/memory.md",
        )
        self.assertEqual(
            planner_plan_note_path("task-1"),
            "memory/planner/task-1/plan_note.md",
        )
        self.assertEqual(
            worker_child_reply_path("child-1"),
            "memory/worker/child-1/reply.md",
        )
        self.assertEqual(
            planner_turn_slot_path("task-1", "doorbell-1", 5),
            "state/planner_turns/task-1/doorbell-1/worker-slot-5.json",
        )
        self.assertEqual(SLOT_COUNT, 5)
        self.assertIn(
            "Planner G2",
            planner_turn_memory_entry_header(entry_id="doorbell-1", planner_generation=2),
        )

    def test_empty_slot_is_no_work_until_planner_writes_semantics(self):
        slot = make_planner_turn_slot(
            task_id="task-1",
            control_epoch=1,
            doorbell_id="doorbell-1",
            planner_generation=2,
            planner_fence_token="fence-2",
            slot_index=1,
        )
        self.assertFalse(planner_slot_has_write(slot))

        slot["entry_type"] = "DIRECTION"
        slot["semantic"] = {"target": "repair material"}
        self.assertTrue(planner_slot_has_write(slot))

    def test_turn_outcome_is_the_commit_marker(self):
        outcome = make_planner_turn_outcome(
            task_id="task-1",
            control_epoch=1,
            doorbell_id="doorbell-1",
            planner_generation=2,
            planner_fence_token="fence-2",
        )
        self.assertFalse(planner_turn_is_committed(outcome))
        outcome["outcome"] = "CONTINUE"
        self.assertTrue(planner_turn_is_committed(outcome))

    def test_child_reply_entry_keeps_writer_and_type_visible(self):
        rendered = render_child_reply_entry(
            entry_id="entry-1",
            writer_role="planner",
            entry_type="review_direction",
            semantic={"next": "material only"},
        )
        self.assertIn("PLANNER", rendered)
        self.assertIn("REVIEW_DIRECTION", rendered)
        self.assertIn("material only", rendered)


if __name__ == "__main__":
    unittest.main()
