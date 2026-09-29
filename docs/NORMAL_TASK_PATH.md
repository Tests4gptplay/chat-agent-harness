# Normal task path

For direct bounded work:

```text
User -> Foreground -> bounded execution -> durable result -> Foreground
```

For managed work:

```text
User -> Foreground Task Contract -> Planner -> Worker execution -> Planner acceptance
     -> exact task-owned chat cleanup -> reset / final delivery

Harness/runtime watchdog -> fresh Helper for one concrete operational incident
Helper durable result + done -> receive result, delete that Helper
```

Task Cell semantics are defined in `docs/task-cell/README.md` and the role contracts under `docs/task-cell/`.

Planner and Helper can both finish with `done`: the existing conversation binding selects the role-specific action, so Planner remains while the finished Helper is retired. AI writes semantic content to CAH/Git and emits only the short status; it does not repeat identity headers or perform machine bookkeeping.

Role-visible signal meaning and continuity duties are defined in the role contracts. Runtime lifecycle mechanics remain implementation-owned; matching MD wording is not evidence that live browser execution has passed.
