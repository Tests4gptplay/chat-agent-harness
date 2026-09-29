# Test contract cleanup — resolved

Status: RESOLVED_OBSOLETE_TEST_CONTRACTS. These six previously reported anomalies are not open runtime defects of this reviewed clean snapshot.

The original five assertion failures and one test-side lookup error reproduced before sanitization. Targeted review identified obsolete expectations/mocks, not a need to restore the old runtime mechanisms. This cleanup changes tests and review metadata only; production runtime code is unchanged.

| Retired expectation | Current protection retained |
|---|---|
| response-end alone creates G+1 | The redundant old test is removed. test_planner_child_terminal_liveness verifies no mutation on missing Result; test_worker_watchdog verifies the distinct physical watchdog. |
| expired lease returns the non-renewal reason | The redundant old test is removed. Current healthy/expired lease tests verify renewal without changing dispatch generation or ownership. |
| Worker batch requests are mocked through process() | The scan test mocks process_worker_wake_batch(), verifies the real selection and keeps the <=5 Git-process budget for 0/40/400 completed records. |
| no occurrence of the word successor in a Planner prompt | The actual rotation/binding/retirement/G2 test is retained. Prompt checks require the real task, event and bound output, not a word ban. |
| five-minute budget and plain chat continue | Replaced by ten-minute budget, durable Result turn_signal and semantic_sync expectations. |
| source lookup for the removed Helper observer | Replaced by execution of the real tick method with mocked external boundaries; syscall, Planner and control processing must run before the maintenance interval is due. |

No new skip, xfail or relaxed performance threshold was introduced. Current machine-readable results are in verification.json; historical results remain in Git history, not as current open-failure flags.

The user reports successful Viewer and Cook showcases on the existing deployment. That report supports the deployment context; this cleanup does not rerun those cases or claim a new-machine installation or independent audit.
