# Windows reference installation

This is the currently validated **reference host path**, not a statement that CAH's architecture requires Windows.

The CAH control/runtime model is built around Git, Python, a Chromium-class browser extension, localhost services and an official GitHub self-hosted runner. On Linux or macOS, an installer Agent may adapt host-specific glue — for example `run.cmd` vs `run.sh`, launcher scripts, browser executable discovery, desktop/service startup integration and path conventions — while preserving the same CAH task/state/privacy contracts.

Do not claim an untested platform is already validated merely because the adaptation is straightforward. The Windows path below is the one currently demonstrated end to end; other platforms should be adapted and then verified with the same installation/readback/smoke criteria.

## Platform-adaptation contract for installer Agents

Before following the numbered steps, determine the host platform.

If the host is Windows, use the validated reference path below.

If the host is Linux, macOS, or another supported desktop environment, treat Windows-specific filenames and mechanisms as **reference implementations of a required function**, not as mandatory literals. Map them to the closest native equivalent while keeping the CAH contracts unchanged.

Examples:

| Required function | Windows reference | Other-host adaptation |
| --- | --- | --- |
| self-hosted runner entry point | `run.cmd` | official runner entry point such as `run.sh` |
| CAH launcher | `Start_CAH.bat` / PowerShell | shell script, app launcher, user service, or equivalent |
| browser discovery | `chrome.exe` / Windows install paths | native Chrome/Chromium application/executable discovery |
| background/startup integration | Startup folder / Windows process APIs | user service, login item, LaunchAgent, systemd-user, or equivalent |
| paths | drive-letter / backslash conventions | native filesystem paths |
| process inspection | Windows CIM/process APIs | native process/service inspection |

The installer Agent may make the small source/configuration adjustments required for the target host. This is an open-source installation, not a binary appliance whose Windows glue must remain untouched.

However, **do not silently weaken or rewrite the semantic contracts** while adapting the host layer. These remain invariant:

- live CAH state belongs in a verified PRIVATE operational repository;
- Git remains canonical for durable task/runtime state;
- the official self-hosted runner must be correctly bound to the intended private repository or authorized private scope;
- Task Cell / Worker Project identity must remain exact;
- bridge health and extension topology must be verified;
- runner/bridge/browser integration must actually work on the chosen host;
- the first bounded smoke task must produce a real result before the installation is called ready.

If a platform-specific adaptation is required, the Agent should make the change, explain it briefly, test it, and continue. The user should not be expected to manually translate a Windows command into Linux/macOS syntax.

This guide is for the experimental CAH 1.0.4 source distribution. The installer **must not hard-code** the maintainer's username, drives, runner roots or ChatGPT Project URLs. This release ships disabled example bindings and empty task state.

## Recommended installation mode: AI-guided, human-authorized

CAH is intended to be installed **with an AI Agent driving the setup** rather than by requiring the user to manually interpret every repository, runner, browser and configuration step.

A good installation session should feel like:

```text
Human
  |
  | "Install CAH from this repository"
  v
AI installer / Agent
  |
  +--> read public AGENTS.md + this guide
  +--> inspect current host prerequisites
  +--> prepare private-repository migration
  +--> generate configuration
  +--> build / validate / smoke-test
  |
  +--> pause only when human authority is required
            |
            +--> create/confirm private repo
            +--> install/approve required software
            +--> grant GitHub/ChatGPT permissions
            +--> create ChatGPT Projects
            +--> approve runner registration
            +--> load/approve browser extension
  |
  v
verified private CAH installation
```

The AI should keep the user on the shortest safe path and explain one concrete human action at a time when manual involvement is required. It should not dump the whole guide back at the user as a checklist unless the user explicitly asks for a manual installation.

### What the AI should do

The installer Agent should normally:

- read the public `AGENTS.md` bootstrap gate before performing live setup;
- verify that the eventual operational repository is PRIVATE;
- identify the host OS and inspect Git, Python, the native shell/runtime tools, browser and optional executor availability;
- distinguish already-installed prerequisites from missing ones;
- prepare the public-to-private repository transition;
- wire the operational `origin` to the private repository;
- run `tools/configure_install.py` with user-approved values;
- build the extension;
- validate generated topology/bindings;
- run available source/configuration smoke checks;
- diagnose failures and continue after the user satisfies a missing prerequisite;
- keep credentials, temporary tokens, cookies and private Project identifiers out of public logs and public Git.

### What still requires the human

The human remains the authority for actions that affect accounts, permissions, physical hardware or private resources. Depending on the environment, this may include:

- creating or selecting the private GitHub repository;
- confirming repository visibility;
- installing required desktop applications or accepting their installers;
- approving GitHub App / ChatGPT connector access;
- creating the CAH Task Cell and Worker Projects;
- sharing the exact Project root URLs with the installer;
- obtaining and using a temporary GitHub runner registration token;
- approving browser extension installation/loading;
- responding to OS/browser security prompts;
- deciding which optional executors and local applications the Agent may use.

The Agent should guide these steps, verify their result, and then resume automatically where possible.

### Interactive guidance contract

When a human action is required, the installer Agent should behave like an interactive setup assistant rather than handing the user a static checklist.

For each manual step, it should:

1. explain **why** the action is required;
2. tell the user **where to go** (site/application and settings path);
3. give **one concrete action at a time**;
4. name the control/button/menu to look for;
5. describe the expected screen/result after the action;
6. ask for a short confirmation before proceeding;
7. continue from the same installation state instead of restarting the guide.

Example:

```text
AI:
Open your private GitHub repository.
Go to Settings -> Actions -> Runners.
Click "New self-hosted runner" and choose Windows x64.
Stop when GitHub shows the runner setup commands.

Human:
I'm there.

AI:
Run only the download/setup commands GitHub shows on your machine.
Do not paste the registration token into chat.
Tell me when the runner reports that it is connected.
```

### Screenshot-assisted installation

If the user cannot find a setting, button, permission page, Project URL, extension control, or other installation UI, explicitly invite them to **upload a screenshot of the current screen**.

The Agent should inspect the screenshot and tell the user the next visible action as precisely as possible, for example:

```text
"I can see the repository Settings page. In the left sidebar, click Actions,
then Runners. Send another screenshot if the Runners page looks different."
```

Do not force the user to translate changing web UIs into technical terminology.

If the screenshot does not contain enough context, ask for a wider screenshot or a second screenshot rather than inventing an interface path.

### Secret-handling boundary for screenshots and chat

Screenshots and chat messages used for installation help must not expose credentials.

The Agent must never ask the user to upload or paste:

- passwords;
- browser/session cookies;
- GitHub personal access tokens;
- GitHub runner registration tokens;
- OAuth authorization codes;
- recovery codes;
- private keys;
- API secrets;
- other temporary or long-lived credentials.

If such a value is visible on a page the user wants to screenshot, instruct the user to crop, blur, cover, or otherwise remove the secret before uploading the image.

The Agent may explain **where** the secret should be entered locally and how to confirm that the operation succeeded, but the secret itself should remain between the user and the relevant local/site UI.

Private repository names, local paths, and ChatGPT Project URLs should also be handled minimally: use them only when needed for the user's private installation and never copy them into the public CAH repository, public Issues/Discussions, or public logs.

### UI drift rule

GitHub, ChatGPT, Chrome and Windows interfaces may change.

If the documented menu path no longer matches the user's current UI:

- do not insist that the old path must exist;
- do not invent a new button name from memory;
- ask the user for a screenshot of the current page;
- use the visible UI to guide the next action;
- once the step succeeds, continue the installation from the canonical setup state.

The installation guide defines the required outcome; the AI is responsible for adapting the click-path to the user's current interface.

### Suggested prompt for an installer Agent

A user should be able to begin with something close to:

> Install CAH from this public repository. Follow its public AGENTS.md and installation reference. Detect my host OS first. Preserve CAH's runtime/privacy contracts, but adapt Windows-specific reference commands and launchers to native equivalents if this is not Windows. Keep the live instance in a verified private repository. Inspect what is already installed, do the mechanical setup yourself where possible, and ask me only for hardware preparation, account authorization, private-repository creation, ChatGPT Project creation, or other steps that require my direct approval.

This AI-guided flow is the recommended experience. The numbered sections below remain the authoritative detailed procedure and can also be followed manually.

## 1. Make your own private running repository

Create an **empty private GitHub repository** for your CAH instance. Copy this distribution into it; do not use a public fork as your live state store. With Git installed, these commands use examples that you must replace:

```powershell
git clone https://github.com/Tests4gptplay/chat-agent-harness.git cah-instance
cd cah-instance
git remote set-url origin https://github.com/YOUR-ACCOUNT/YOUR-PRIVATE-REPO.git
git push -u origin main
```

Check that the destination is private before pushing live configuration. Keep this public repository as a separate optional `upstream`, never as the destination of task-state writes. Authorize your ChatGPT GitHub connection for your private repository.

## 2. Prepare the host

Install Git, Python 3.10 or later (`py --version`), PowerShell 7 (`pwsh --version`) and Chrome. Node.js is needed for development tests, not ordinary bridge use. Optional executors need their actual applications: for the camera example, Blender on PATH or `GAH_BLENDER_EXE` pointing to its executable.

In **your private repository**, open **Settings → Actions → Runners → New self-hosted runner → Windows x64**. Follow GitHub's generated download and registration commands in a separate runner directory. The registration token is temporary and must not go into Git, screenshots or a chat. Retain the `self-hosted`, `Windows`, `X64` labels. Run the runner interactively in your logged-in desktop session for browser work, rather than assuming a background service can access your desktop.

Never register your computer's runner on the public CAH distribution. Runtime workflows include a private-repository job condition; do not remove it for a public deployment. Hosted public CI only tests source.

## 3. Create your own ChatGPT Projects and configure once

Create three Projects: **CAH Task Cell**, **CAH Sandbox0**, and **CAH Sandbox1**. This release retains the tested two-lane onboarding topology; that is capacity, not a requirement to use two Workers for every task. Copy each Project's actual root URL ending in `/project`.

Run the following from your private checkout with **your own values**:

```powershell
py tools/configure_install.py `
  --repo YOUR-ACCOUNT/YOUR-PRIVATE-REPO `
  --runner-root 'C:\your-runner\actions-runner' `
  --task-cell 'PASTE-YOUR-TASK-CELL-PROJECT-URL' `
  --lane 'PASTE-YOUR-LANE0-PROJECT-URL' `
  --lane 'PASTE-YOUR-LANE1-PROJECT-URL'
```

The URLs above are intentionally invalid examples, **not strings to paste unchanged**. Use the URL copied from the browser, for example its normal form `https://chatgpt.com/g/g-p-<id>-<slug>/project`. The installer rejects incorrect forms. It binds installation placeholders in the private source copy, writes canonical lane state and a host-local ignored `cah.local.json`, then builds the extension. It does not install or register the GitHub runner for you.

Review and commit the generated installation bindings **only in your private repository**:

```powershell
git add AGENTS.md extension harness local_bridge executors state tests docs browser examples .github
git commit -m "Configure private CAH installation"
git push
```

`cah.local.json` stays ignored: it contains your local paths. The one-time placeholder binding is an initial deployment step; later self-updates follow your private repository's `origin/main`, not this public upstream. Incorporate upstream changes through a reviewed merge and rebuild when needed.

## 4. Load the extension and start

Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select `<your-checkout>\extension\dist\chromium` — the directory directly containing `manifest.json`.

Open the three Projects, pin **CAH Wake Bridge**, and check the displayed Task Cell and lane URLs. Then double-click **Start_CAH.bat**. It starts or reuses the existing runner and bridge, and reuses an open Chrome process rather than creating another set of windows. The launcher writes a nearby `Start_CAH.log` and keeps failures visible.

Check `http://127.0.0.1:8765/health` locally and the extension's topology/status panel. Your own two lanes should be registered with the correct Project keys; wait for their bootstrap/takeover status before submitting a real workload. Do not treat a green build as proof that your local browser/runner loop has connected.

## 5. First real task and maintenance

Ask the foreground agent to read `AGENTS.md` in your private repository, then perform one small bounded task using one Worker. It must return an artifact and its matching terminal result. Ordinary writing that needs no local execution should remain in the foreground.

The optional `showcases/camera/reproduce.py` regenerates the procedural camera outside the agent loop. The self-update contracts are in `docs/WAKE_BRIDGE.md` and the `host/host_update.ps1` entry point. Read `docs/OPERATIONS.md` before unattended work.
