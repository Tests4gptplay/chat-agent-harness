# Iterative Blender visual refinement

Use this procedure for a current authorized Blender task with a bounded iteration budget and reviewable output.

1. Write the current task's visual and structural acceptance criteria, required deliverables, protection scope and maximum completed quality iterations. Give artistic acceptance, geometry validity and downstream application verification separate gates.
2. Author a durable task-local bpy script and explicit current inputs. Record source/version hashes and the intended output directory before execution. Keep it inside the current task workspace and preserve unrelated user materials.
3. Use the existing bounded execution surface. `executors/blender_case.py` supplies `bootstrap`, `run_iteration` and `promote_final` for its supported case artifact contract; use the current Managed request and task-local script when the current task needs a different artifact contract. This guide adds no executor or automatic workflow.
4. Persist the executed script, original render, manifest, relevant stage evidence and actual error/exit result. Keep bulky blends, complete logs, duplicate preview encodings and intermediate renders in the task's managed workspace rather than multiplying copies in framework Git.
5. Inspect a compact review derived from the actual output pixels. State the strongest visual delta, remaining defect and next bounded change. Successful execution is separate from visual acceptance. A failed execution attempt is not a completed quality iteration.
6. Continue only while the acceptance gate materially improves and the budget remains. Two equivalent changes that only move a defect call for a different representation or an explicit blocker, not more of the same adjustment. For branching surfaces, also apply the preserved diagnostic guardrails in `skills/guides/blender-branch-volume-diagnostics.md`.
7. Promote one actually executed and reviewed iteration. Copy the selected render/script/blend as applicable, reread it, and record hashes and selection provenance. Do not report a current deliverable or terminal PASS from an old sample receipt. Record the current task's own result through its exact Harness binding.

