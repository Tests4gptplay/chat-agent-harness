# Public safety stop procedure

CAH does not currently provide a guaranteed hard real-time emergency stop for every process already launched by a task.

For public use, the minimum operator response is:

1. Stop the CAH localhost bridge or its supervisor process.
2. Stop the configured GitHub self-hosted runner (`Runner.Listener.exe` or its service/console instance).
3. Close or disable CAH browser/Project activity if it is still generating wakes.
4. Stop any already-running workload process separately in its owning application or Windows Task Manager when required.
5. Verify the bridge health endpoint is unavailable and the GitHub runner is offline before considering CAH intake stopped.

This procedure stops new CAH scheduling/intake. It does not guarantee preemption of every child process already launched by Windows or a workload tool.

Use CAH only for workloads whose failure mode remains acceptable under that limitation. Destructive or irreversible work should include its own application-level safety boundary.

A future dedicated Brake/Panic/Resume feature may automate these steps, but it is not required for current private operation.
