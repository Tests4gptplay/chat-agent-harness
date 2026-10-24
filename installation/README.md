# Installation / AI-led onboarding

CAH uses a Windows host, ChatGPT web conversations, GitHub, a dedicated Chrome profile, the native Playwright Host, a localhost Bridge, and self-hosted Runners.

**Give this guide to an AI with Windows command/file access and browser control.** The AI performs setup and troubleshooting. The user signs in, approves account access and chooses the ChatGPT Projects. Ask for those actions one step at a time, then continue the setup.

## 1. Choose the installation

Use your own private GitHub repository for the running instance: CAH writes task requests, memory and results into that repository. This public repository provides the reusable source, workflows and Skills.

For an existing installation, keep its configured paths, Projects, credentials and task state; use the [upgrade section](#upgrading-an-existing-instance). For a new Windows x64 installation, the example layout is:

| Location | Purpose |
|---|---|
| `C:\CAH\public-source` | Downloaded public source |
| `C:\CAH\repo` | Your configured operational Git checkout |
| `C:\CAH\runners\managed-01` | First Managed Runner |
| `C:\CAH\runners\managed-02` | Second Managed Runner, also labeled `backup` |
| `C:\CAH\runners\shot` | Independent `cah-shot` Runner |
| `C:\CAH\runtime\bridge` | Bridge state and logs |
| `C:\CAH\runtime\browser` | Browser Host state and logs |
| `C:\CAH\tools\playwright` | Locked Node MCP/CLI packages |
| `C:\CAH\work`, `deliverables`, `temp` | Workspaces, finished output and temporary files |
| `%LOCALAPPDATA%\CAH\PlaywrightChrome` | Persistent CAH Chrome login profile |

The base directory can be on another disk. The configuration tool derives the paths from it.

## 2. Prepare tools and account access

Reuse installed tools; install the missing ones from their official distributions:

- Git for Windows and GitHub CLI (`gh`).
- Python with the Windows `py` launcher; Python 3.13 is the reference version.
- Windows PowerShell (`powershell.exe`) and PowerShell 7 (`pwsh`).
- Google Chrome.
- Node.js 20 or newer and npm.
- Python Playwright in the interpreter selected by `py`.

```powershell
git --version
gh --version
py --version
pwsh --version
node --version
npm.cmd --version
py -m pip install playwright
py -c "from playwright.sync_api import sync_playwright; print('Playwright ready')"
gh auth login
gh auth setup-git
```

The native Host connects to installed Chrome through CDP. Its Python package and the Node MCP/CLI packages serve different parts of the browser integration.

Use the deployment user's Git identity. If it is missing, configure `git user.name` and a GitHub-approved email in the operational checkout. The same Windows account running Bridge needs Git read/write access. Runner registration is a separate authorization.

In ChatGPT, enable a Git/GitHub tool connection with read **and write** access to your operational repository. The Foreground, Planner and Worker use it for canonical task records. Check the tools actually available to the web conversations; if the selected connector offers only search/read, enable a writable connection or the documented host Git execution route. The deployment AI can prepare that route using the bundled Short Task workflow. Account login and permission prompts are handled by the user.

## 3. Download the public source

```powershell
New-Item -ItemType Directory -Force C:\CAH | Out-Null
git clone https://github.com/Tests4gptplay/chat-agent-harness.git C:\CAH\public-source
```

Create or select an empty private repository for your instance, for example `YOUR_GITHUB_USER/cah-runtime`. The AI can create it with `gh repo create YOUR_GITHUB_USER/cah-runtime --private` after the user chooses the name.

## 4. Sign in and select Projects

Open a dedicated browser for CAH:

```powershell
$Chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$Profile = Join-Path $env:LOCALAPPDATA 'CAH\PlaywrightChrome'
Start-Process -FilePath $Chrome -ArgumentList @(
    "--user-data-dir=`"$Profile`"",
    '--profile-directory=Default',
    '--remote-debugging-port=9222',
    '--remote-debugging-address=127.0.0.1',
    '--disable-extensions',
    '--no-first-run',
    'https://chatgpt.com/'
)
```

If this CAH profile is already open on port 9222, reuse it. Keep CDP and Bridge on loopback.

Ask the user to sign in and create/select:

| Project | Purpose |
|---|---|
| **CAH Task Cell** | Planner and Helper conversations |
| **CAH Sandbox0** | First Worker lane |
| **CAH Sandbox1** | Optional second Worker lane |

One Sandbox is enough to begin. The number of browser lanes is independent of the three Runner instances.

The AI opens each Project and reads its complete root URL, such as `https://chatgpt.com/g/g-p-...-project-name/project`. It also opens a normal chat for the **Foreground** and records that chat's exact `/c/...` URL. The user need not copy IDs or edit JSON. Use dedicated execution Projects so CAH can manage its own disposable role conversations there.

Planner, Worker and Helper conversations are created by Harness when needed.

## 5. Configure a new copy

Copy [config.example.json](config.example.json) to `C:\CAH\config.local.json` and fill it using the user's repository and the URLs read from the browser:

- `repository`: GitHub `owner/name` of the operational repository.
- `base_directory`: installation base.
- `task_cell_url`: Task Cell Project root URL.
- `lane_urls`: one or more Sandbox Project root URLs.
- `foreground_url`: selected Foreground conversation URL.
- `second_runner_name`: name used when registering Managed 02.

Optional settings: `chrome_path`, `profile_directory`, `record_repository`, `transfer_repository`, and a `paths` object for individual directory overrides. [CONFIGURATION_MAP.md](CONFIGURATION_MAP.md) lists the keys and consumers.

```powershell
py C:\CAH\public-source\installation\configure.py --config C:\CAH\config.local.json --output C:\CAH\repo
Set-Location C:\CAH\repo
git init -b main
git add .
git commit -m "Initialize CAH"
git remote add origin https://github.com/YOUR_GITHUB_USER/cah-runtime.git
git push -u origin main
```

The tool creates a new directory, fills repository/path defaults and configures the same Task Cell and lane topology across the Host, reset command and canonical state. It retains CAH's normal runtime behavior.

Store the local configuration with the installation. Keep login credentials and Runner registration tokens out of Git and chat messages.

## 6. Install the browser tools

Run the configured copy's installer:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\CAH\repo\host\install_playwright_tools.ps1
```

It installs the versions in `host/playwright-tools/package-lock.json` using `npm ci`. The native Host, official Playwright MCP and CLI share the same logged-in Chrome via CDP 9222.

For a separately installed Node distribution, the installer also accepts `-NodePath` and `-NpmCli`. CLI usage is documented in [Tool System](../docs/TOOL_SYSTEM.md).

## 7. Register the three Runners

Open your operational repository's **Settings → Actions → Runners → New self-hosted runner**, select Windows x64, and use its current download/extract commands in each Runner directory. Register each copy to that same repository:

| Directory | Name | Labels |
|---|---|---|
| `runners\managed-01` | `CAH-Managed-01` | Default `self-hosted, Windows, X64` |
| `runners\managed-02` | Configuration's `second_runner_name` | Default labels plus `backup` |
| `runners\shot` | `CAH-ShortTask` | `--no-default-labels --labels cah-shot` |

From each directory run `config.cmd --url https://github.com/YOUR_GITHUB_USER/cah-runtime --name NAME --work _work`, adding `--labels backup` for 02 or `--no-default-labels --labels cah-shot` for Shot. Enter the short-lived registration token at the local prompt.

The launcher starts all three and reuses existing Listeners. Managed 02 provides additional normal capacity and the dedicated `backup` route. CPU, GPU, memory and shared workspaces are coordinated according to the actual workload.

The distribution includes these reusable execution entry points:

- `managed-host-script.yml` and `managed-host-script-cold.yml`: execute task-owned script requests.
- `stage0-single-thread.yml`: structured action/executor route.
- `host-capability-probe.yml` and `action-submit-recovery.yml`: capability discovery and action recovery.
- `foreground-direct-command.yml` and `toolbox-short-local-command.yml`: idle Short Task command slots, filled for the next task.
- `codex-skill-sync.yml`: on-demand import of the installation owner's local Skills.

Enable GitHub Actions in the operational repository. Its workflow permissions must allow the declared task-result writes. Each request's commands and output belong to that installation.

## 8. Start and use CAH

```powershell
C:\CAH\repo\Start_CAH.bat
```

The launcher updates the checkout from `origin/main`, starts/reuses Bridge and the three Runners, then starts/reuses the native Host and dedicated Chrome. The configured Foreground URL is saved in the local Host state.

The AI checks the normal startup results:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:9222/json/version
```

Bridge should report the operational checkout and runtime root; GitHub shows the registered Runners online; the dedicated browser is signed in.

Open the configured Foreground conversation and ask it to read `AGENTS.md` and `docs/task-cell/FOREGROUND.md` from your repository's `main`. Give it a small initial task, such as writing a short text file into the work directory and returning its path. The AI follows the task through the existing Git/Runner/browser path and resolves any concrete setup error. Then hand over the startup shortcut, Foreground chat and local directories.

**Daily use:** run `Start_CAH.bat`, open the Foreground chat and give it your task. The dedicated Chrome window can stay minimized while CAH is working.

## Troubleshooting and stopping

| Symptom | Where to look / action |
|---|---|
| Git update or write fails | Operational remote, branch, Git identity and login under the Bridge account |
| Runner is offline or job stays queued | Correct repository registration, Listener and labels in the table above |
| Browser cannot attach | CAH profile, Chrome path and `127.0.0.1:9222/json/version` |
| Login or Project access fails | User signs in/authorizes; AI rereads the selected Project URL |
| No Foreground delivery | Configured conversation URL and `runtime/browser/state.json` |
| Bridge error | `runtime/bridge/logs/` and `runtime/bridge/runtime/bridge-supervisor.json` |
| Host error | Browser runtime stdout/stderr and `state.json` |
| Workflow failure | The exact GitHub Actions run log and Runner's `_diag` |

Stop the installation's Bridge supervisor before its child, then its Host and the relevant Runner Listeners once owned jobs have ended. Handle external application jobs according to their task. See [SAFETY_STOP.md](../docs/SAFETY_STOP.md). `Reset_CAH_Hot_State.bat` is an explicit conversation/task reset operation; routine installation and startup use `Start_CAH.bat`.

## Upgrading an existing instance

Keep its Git remote, local configuration, browser profile, Runner registrations, active state, private Skills and task work. Download the new public source separately and compare the reusable code and docs. Apply the update with the instance's existing configuration, retaining its state/data directories. Perform the switch when its jobs are at an appropriate stopping point, then use the normal startup entry.

## A prompt to give the deployment AI

> Install this CAH version on my Windows machine using this Installation guide. Reuse existing tools and accounts. Do the command-line work, read Project URLs through the browser, configure my own operational repository, install the locked browser tools and register the three Runner roles. Ask me one step at a time when login or account approval is needed. Start CAH, resolve concrete setup errors, and show me the Foreground chat and daily startup entry.
