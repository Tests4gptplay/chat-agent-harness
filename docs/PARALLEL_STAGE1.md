# Parallel scheduling primitives

This release includes the deterministic task-DAG, dependency, lane and reducer primitives in `harness/parallel.py`. Task state remains in Git; browser windows are execution hosts, not proof that useful work ran in parallel.

## Run the deterministic smoke

```sh
python harness/parallel.py smoke
python -m unittest discover -s tests -p 'test_parallel_scheduler.py'
```

The smoke covers two ready independent branches, a reducer waiting for both results, fresh dispatch fences and rejection of stale results. A one-lane topology executes the graph sequentially. It does not launch live model sessions or prove concurrent application throughput.

## Runtime use

Configure your own registered lanes through the private-installation guide. The release preserves lane-addressed wakes, independent bounded Worker conversation pools and persistent task/result records. Task Cell is a control context, not disposable Worker capacity.

Use a single Worker when there is no independently useful work to split. For useful parallel work, make inputs, output ownership, dependencies and the join explicit. Use the existing task/result contracts in `harness/parallel_task.schema.json` and `docs/SCHEDULER_MODEL.md`.

The included camera showcase records a single-lane application workload. The self-update showcase records both bindings surviving an update, followed by one delegated task. Neither is advertised as a benchmark of general concurrent application throughput.
