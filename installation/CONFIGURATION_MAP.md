# Configuration map

[configure.py](configure.py) takes [config.example.json](config.example.json) and creates a configured copy.

| Setting | Used by |
|---|---|
| `repository` | Scheduler/wake repository identity, Planner/Worker/Helper prompts, execution workflows |
| `base_directory` | Default Runner, Bridge, browser, tool, work, temporary and output directories |
| `task_cell_url` | `playwright_host/config.py` and explicit semantic-reset targets |
| `lane_urls` | `BOOTSTRAP_LANES`, `state/lanes.json`, launcher's and supervisor's local lanes |
| `foreground_url` | Native Host first-start parameter, then local Host state |
| `second_runner_name` | Managed 02's registered identity and related guidance |
| `chrome_path` | Optional Chrome executable override |
| `profile_directory` | Optional dedicated Chrome profile override in Host and launcher |
| `record_repository`, `transfer_repository` | Optional cold-record and transfer repository documentation; default to the instance repository |
| `paths` | Optional overrides listed below |

Supported `paths` keys: `BRIDGE_ROOT`, `MANAGED_RUNNER_ROOT`, `SECOND_RUNNER_ROOT`, `SHOT_RUNNER_ROOT`, `BROWSER_ROOT`, `TOOLS_ROOT`, `WORK_ROOT`, `DELIVERABLES_ROOT`, `RUNNER_BASE`, `BROWSER_BASE`, `TEMP_ROOT`. `REPO_ROOT` comes from `--output`.

Ports follow the current runtime defaults: Bridge 8765 and Chrome CDP 9222, both on loopback.

The internal `git-agent-harness` project identifier and `GAH_*` wire markers are protocol names. The configurable `repository` field selects the GitHub destination.

For an existing installation, keep its current bindings and records while updating code. The configuration command is for creating a new copy.
