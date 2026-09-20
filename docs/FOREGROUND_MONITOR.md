# Foreground supervision

The Foreground owns the user's confirmed task and delivers its result. Backend Workers perform explicitly dispatched work; the Task Cell provides coordination when useful.

`extension/foreground_monitor.js` can display the configured foreground task's progress and terminal state. A foreground CL can reference one or more backend results. Neither a transport acknowledgment nor a displayed status is a substitute for reading the result artifact.

A small task does not need another model approval turn. Use one useful Worker result and deliver it. Substantial coordinated tasks use their declared branch, reduction and acceptance conditions. The foreground does not take ownership of backend lanes merely to observe them.

Keep current summaries small, retain evidence by reference and preserve paused work. See `docs/NORMAL_TASK_PATH.md`, `docs/SCHEDULER_MODEL.md` and `harness/cl.schema.json` for implemented task and dispatch contracts.
