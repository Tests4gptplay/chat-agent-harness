# Windows installation

This guide is for the experimental CAH 1.0.4 source distribution. The installer **must not hard-code** the maintainer's username, drives, runner roots or ChatGPT Project URLs. This release ships disabled example bindings and empty task state.

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
