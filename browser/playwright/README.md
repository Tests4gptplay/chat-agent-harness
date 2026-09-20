# Playwright Chromium browser backend

This directory is an optional browser-launch layer for CAH. It does not replace Git, the localhost bridge, the official GitHub self-hosted runner, or the CAH extension.

## Why bundled Chromium

Playwright's official extension workflow uses a persistent Chromium context plus `--load-extension` / `--disable-extensions-except`. Branded Google Chrome and Microsoft Edge no longer support the same extension side-loading flags for this automation path, so the bundled Chromium build is the preferred backend.

The current CAH extension remains a single Chromium MV3 build. There is no separate "Playwright edition" of the extension.

## Install on Windows

Using the Python already used by CAH:

```powershell
py -m pip install playwright
py -m playwright install chromium
```

Playwright stores downloaded browsers in its normal cache by default. A custom shared browser-binary location can be introduced later with `PLAYWRIGHT_BROWSERS_PATH`; do not move the current CAH runtime merely for tidiness.

## Launch one persistent CAH browser

Choose a dedicated profile directory:

```powershell
py .\browser\playwright\launch_gah.py --profile-dir C:\CAH\Browser\lane-00
```

The launcher:

1. rebuilds `extension/dist/chromium`;
2. launches Playwright bundled Chromium in headed persistent mode;
3. loads the current CAH Chromium extension;
4. keeps ChatGPT cookies/local storage in that dedicated profile;
5. prints the extension id when observed.

On first launch, sign in to ChatGPT and configure the CAH popup once. The persistent profile keeps that browser/extension state on later launches.

## Instance configuration

Configure your own private instance and Project bindings using `docs/INSTALL_WINDOWS.md` before launching. This optional browser backend does not inherit the maintainer's account, browser profile or Project identities. Each persistent profile owns its local browser/extension storage. Do not start two clients against the same active Worker binding.
