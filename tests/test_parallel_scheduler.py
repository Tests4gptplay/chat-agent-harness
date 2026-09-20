import unittest

from harness.parallel import (
    ParallelSchedulerError,
    complete_node,
    dispatch_ready,
    make_state,
    run_dual_lane_smoke,
)


class ParallelSchedulerTests(unittest.TestCase):
    def topology(self, count=2):
        lanes = [
            {"lane_id": "lane-00", "project_key": "g-p-test00", "enabled": True},
            {"lane_id": "lane-01", "project_key": "g-p-test01", "enabled": True},
        ]
        return {"v": 1, "lanes": lanes[:count]}

    def task(self):
        return {
            "v": 1,
            "task_id": "dual-lane-test",
            "max_parallel": 2,
            "nodes": [
                {"node_id": "a", "depends_on": []},
                {"node_id": "b", "depends_on": []},
                {"node_id": "reduce", "kind": "reducer", "depends_on": ["a", "b"]},
            ],
        }

    def test_dual_lane_barrier_and_reducer(self):
        state = run_dual_lane_smoke()
        self.assertEqual(state["status"], "DONE")
        self.assertEqual(state["completed_order"], ["branch-a", "branch-b", "reduce"])

    def test_independent_dispatches_use_distinct_fences_and_lanes(self):
        state = make_state(self.task(), self.topology())
        issued = dispatch_ready(state)
        self.assertEqual(len(issued), 2)
        self.assertEqual({item["lane_id"] for item in issued}, {"lane-00", "lane-01"})
        self.assertEqual(len({item["fence_token"] for item in issued}), 2)
        self.assertEqual({item["node_id"] for item in issued}, {"a", "b"})

    def test_barrier_does_not_release_early(self):
        state = make_state(self.task(), self.topology())
        issued = {item["node_id"]: item for item in dispatch_ready(state)}
        complete_node(
            state,
            node_id="a",
            dispatch_id=issued["a"]["dispatch_id"],
            fence_token=issued["a"]["fence_token"],
            result_ref="results/a.json",
        )
        reducer = next(node for node in state["nodes"] if node["node_id"] == "reduce")
        self.assertEqual(reducer["status"], "WAIT_DEP")
        self.assertEqual(dispatch_ready(state), [])

    def test_stale_fence_is_rejected(self):
        state = make_state(self.task(), self.topology())
        issued = {item["node_id"]: item for item in dispatch_ready(state)}
        with self.assertRaises(ParallelSchedulerError):
            complete_node(
                state,
                node_id="a",
                dispatch_id=issued["a"]["dispatch_id"],
                fence_token=issued["b"]["fence_token"],
                result_ref="results/a.json",
            )

    def test_single_lane_fallback_keeps_same_graph_valid(self):
        state = make_state(self.task(), self.topology(count=1))
        first = dispatch_ready(state)
        self.assertEqual(len(first), 1)
        first_dispatch = first[0]
        complete_node(
            state,
            node_id=first_dispatch["node_id"],
            dispatch_id=first_dispatch["dispatch_id"],
            fence_token=first_dispatch["fence_token"],
            result_ref="results/first.json",
        )
        second = dispatch_ready(state)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second[0]["node_id"], "reduce")


if __name__ == "__main__":
    unittest.main()
