# Host capability and configuration runtime

CAH treats host software, runtimes, SDKs and other machine-specific dependencies as reusable **host capabilities**. Workloads should consume capabilities rather than repeatedly guessing installation paths.

## Why this exists

A missing path is not the same thing as a failed workload.

Examples:

- UE 5.6 is installed but not discoverable from PATH;
- Blender exists in a non-default location;
- an SDK is present but its root has not been configured;
- a tool is genuinely unavailable.

CAH must distinguish these cases from domain-computation failure.

## Two-layer model

### Host-local authority

Machine-specific absolute paths are stored only in the CAH local runtime/config root.

Default local cache:

`<GAH_LOCAL_ROOT>/runtime/host-capabilities.local.json`

The local cache may contain:

- verified installation root;
- executable paths;
- provider-specific helper paths;
- validated version;
- discovery source;
- validation timestamp.

This file is **not canonical Git state** and must not be committed.

### Sanitized projection

Git/Workers may consume only sanitized capability projections defined by:

`harness/host_capability.schema.json`

A projection contains status/version/source/validation metadata but no absolute machine path.

Example:

~~~json
{
  "v": 1,
  "capability_id": "unreal_engine_5_6",
  "state": "AVAILABLE",
  "available": true,
  "version": "5.6.1",
  "source": "local_cache",
  "validation": "Engine/Build/Build.version",
  "observed_at": "2026-09-19T00:00:00+00:00",
  "prompt": null,
  "reason": null
}
~~~

## Capability states

`AVAILABLE`
: The provider validated a usable capability.

`NEED_HOST_CONFIG`
: The capability may exist but automatic discovery exhausted its candidates. Human/local configuration can resolve it.

`UNAVAILABLE`
: The requested capability id is unknown or cannot be represented by the current provider set.

Do not convert `NEED_HOST_CONFIG` into terminal workload `ERROR`.

## Scheduler mapping

For a semantic task that needs host configuration:

~~~text
RUNNING / WAIT_RESULT
        ↓
provider returns NEED_HOST_CONFIG
        ↓
WAIT_RESOURCE
resource_wait.kind = NEED_HOST_CONFIG
resource_wait.capability_id = ...
resource_wait.prompt = ...
        ↓
Worker reasoning capacity is released
        ↓
capability becomes AVAILABLE
        ↓
fresh dispatch generation + fresh fence
        ↓
READY -> Worker continuation
~~~

The task id, checkpoint and logical workload remain the same.

`harness/resource_wait.py` owns the generic resource-wait transition and recovery.

## Standard executor

`executors/host_capability.py` is the normal deterministic preflight for a workload that requires a registered capability.

It writes a sanitized projection artifact and returns:

- `PASS` when the capability is AVAILABLE;
- `BLOCKED` with `fault_boundary=NEED_HOST_CONFIG` when configuration is required;
- `ERROR` only for resolver/executor failures.

Stage 0 maps the typed config block to `WAIT_RESOURCE` rather than terminal failure.

## Host probe / resume workflow

`.github/workflows/host-capability-probe.yml` consumes request files under:

`requests/host-capability-probe/*.json`

The request contains capability/task/CL routing identity only. Do not put absolute installation paths in the Git request.

The self-hosted runner:

1. resolves the capability using local cache/config/discovery;
2. writes a sanitized projection under `evidence/host-capabilities/`;
3. if a matching backend CL is waiting on that capability and the projection is AVAILABLE, advances it to a fresh READY dispatch;
4. emits one fenced wake to the original lane.

## Configuring a path locally

When automatic discovery cannot find a capability, configure it on the execution host without committing the path.

For UE 5.6:

~~~powershell
py host\capabilities.py unreal_engine_5_6 --candidate-root "X:\path\to\UE"
~~~

The provider validates the candidate before persisting it in the host-local cache. A bad path or wrong engine version is not accepted.

After local configuration, re-run the host-capability probe/resume request for the waiting task.

## UE 5.6 provider

Provider id:

`unreal_engine_5_6`

Discovery order:

1. explicit supplied/local configuration and environment;
2. existing verified local cache;
3. Epic Launcher `LauncherInstalled.dat`;
4. Unreal/Epic registry entries;
5. PATH/command discovery;
6. bounded filesystem fallback.

Directory names are hints only.

Every candidate must contain:

- `Engine/Build/Build.version`;
- `Engine/Binaries/Win64/UnrealEditor.exe`;
- `Engine/Build/BatchFiles/RunUAT.bat`.

The provider accepts the root only when `Build.version` reports:

- `MajorVersion == 5`
- `MinorVersion == 6`

A folder named `UE5.6` containing 5.5 is rejected.

Configure your own engine root; machine-specific paths are not universal defaults.

## Workload rule

When a provider exists, workload scripts must not reimplement tool discovery.

Instead they should:

1. consume the verified host-local capability;
2. use sanitized projection/evidence for scheduler decisions;
3. request `NEED_HOST_CONFIG` when unresolved;
4. resume the same logical workload after configuration.

Per-workload discovery is allowed only as a temporary compatibility bridge while migrating an existing case, and must not become the new canonical mechanism.

## Privacy / export rule

- Never commit the host-local capability cache.
- Never put absolute machine paths in public/sanitized capability projection.
- Public export may retain providers, schemas, tests and sanitized evidence.
- Private path values must remain local even when the repository itself is private.

## Current implementation

Core files:

- `host/capabilities.py` — provider registry, local cache and UE 5.6 provider;
- `harness/host_capability.schema.json` — sanitized projection contract;
- `executors/host_capability.py` — deterministic capability preflight;
- `harness/resource_wait.py` — WAIT_RESOURCE enter/resume transitions;
- `.github/workflows/host-capability-probe.yml` — self-hosted probe and recovery;
- `tests/test_host_capabilities.py` — provider/cache/resource-wait regression tests.
