# QuickLook Viewer: agent operation guide

Operate the Viewer through its loopback API, including in silent mode. These instructions describe the implemented tool, not an asset-production workflow. Choose your own project and input assets.

## Start without a console or Viewer window

Let `<VIEWER_ROOT>` be the directory containing `LaunchCamera.ps1` and the launch helpers. The local showcase launcher opens its bundled camera initially; use `open_mod` to switch to another compatible input.

```powershell
$ViewerRoot = 'C:\Tools\QuickLook'  # Change to your installation directory.
Start-Process wscript.exe -ArgumentList ('"' + (Join-Path $ViewerRoot 'Start_Camera_Viewer_AI_Silent.vbs') + '"')
```

The VBS entry creates the PowerShell child hidden. Double-clicking a `.bat` compatibility wrapper can itself create a brief console, so prefer `.vbs` or a process launcher with no console. When an automation process already owns a hidden console, it can call:

```powershell
& pwsh -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden `
    -File (Join-Path $ViewerRoot 'LaunchCamera.ps1') -AgentSilent
```

The underlying engine is UE 5.6.1 `-game / DX11 / SM5`, with offscreen rendering and hidden-window handling. Silent mode is not `-NullRHI`: native rendering is needed for screenshots. The current launcher uses one fixed API port. It does not automatically provide isolated per-project instances or a generic input-path launch argument.

## Connect and read state

Base URL: `http://127.0.0.1:17656/v1`.

Poll `GET /health` until it returns `ok: true`; then read `GET /status`. Health proves API availability, not that the intended model is loaded.

`status` reports `fixture_root`, `selected_mesh`, `selected_material`, asset counts and mounted-container counts. **It does not report the current camera transform, last preset or display mode.** Track commands you sent instead of expecting nonexistent response fields.

All examples below run on the machine hosting the Viewer. A returned screenshot path is a local file path, not an HTTP download URL.

## Open input and choose an asset

```http
POST /v1/open_mod
Content-Type: application/json

{"path":"C:\\Assets\\MyFixture","primary_asset":"/Game/Models/MyModel.MyModel"}
```

Supply the actual Unreal object path, not a source filename. `primary_asset` is optional; query `GET /list_meshes` to discover assets, then select one:

```http
POST /v1/select_mesh

{"object_path":"/Game/Models/MyModel.MyModel"}
```

The response must have `ok: true`. Confirm `selected_mesh` in `GET /status`. `open_mod` clears the previous preview/session before opening the new input. The catalogue can include engine assets as well as the loaded model, so a high mesh count does not mean that the fixture contains that many objects.

The demonstrated input is a compatible classic PAK fixture. Detection of `.utoc/.ucas` is not proof of arbitrary dynamic IoStore mounting. Input compatibility is a property of the Viewer/engine build, independent of how the calling project creates its assets.

## Camera control in silent mode

```http
POST /v1/set_camera

{"preset":"front"}
```

| Preset | View |
|---|---|
| `perspective` | Default three-quarter inspection view |
| `front`, `back` | Opposite horizontal views |
| `left`, `right` | Opposite side views |
| `top`, `bottom` | Direct top/bottom inspection |
| `below` | Oblique view from below |

Presets also frame the selected model. To refit its bounds without changing the current preset:

```http
POST /v1/frame_selected

{}
```

The implementation defines “front” using the showcase camera's +Y lens direction. Other assets can have different authoring axes; use a different preset where needed. **Arbitrary yaw/pitch, pan, distance, FOV and numeric camera transforms are not exposed by this API version.** Mouse control supports free orbit in the visible interface, but do not send invented numeric fields to `set_camera`. Unknown preset strings fall back to the default view in the current implementation rather than reliably failing.

## Display and inspection information

```http
POST /v1/set_view_mode

{"mode":"lit"}
```

Supported values are `lit`, `unlit` and `wireframe`. Use lit for the rendered material response, unlit when lighting hides detail, and wireframe for geometric inspection.

Read endpoints:

```text
GET /v1/list_meshes
GET /v1/list_materials
GET /v1/list_textures
GET /v1/inspect_material_slots
GET /v1/inspect_dependencies
```

`POST /select_material` accepts `{"object_path":"/Game/Materials/MyMaterial.MyMaterial"}` and records the selection for inspection. It is not a material-editing command.

## Capture a completed image, not a request receipt

```http
POST /v1/capture_viewport

{"name":"front-lit-unique-run-id.png"}
```

A successful reply contains `viewer_owned_path` and means **screenshot requested**. It does not mean image encoding has finished. The Viewer writes under `Viewer/Saved/QuickLook/Captures`; it accepts a filename, not an arbitrary output directory.

Use a unique filename for every request, wait for the returned PNG to be complete, and then copy/read it. Reusing a fixed name and testing only whether the path exists can accidentally consume an old frame. For each view: set preset → set mode → allow rendering to settle → request capture → wait for the complete PNG. Process one capture at a time.

The included [viewer_client.py](viewer_client.py) handles this sequence and checks PNG completion:

```powershell
py .\viewer_client.py health
py .\viewer_client.py set_camera --json '{"preset":"bottom"}'
py .\viewer_client.py set_view_mode --json '{"mode":"wireframe"}'
py .\viewer_client.py capture --name bottom-wireframe --out .\evidence\bottom-wireframe.png
```

For coverage, collect perspective/front/back/left/right/top/bottom lit views; repeat important angles in unlit or wireframe. The exact number of images should follow the task, not an obligatory checklist.

## Close the input versus stop the process

```http
POST /v1/close_session

{}
```

**This clears the preview/session; it does not stop the Viewer process.** PAK mounts and shader resources are retained until process exit. There is no `/shutdown` endpoint in this build.

For a one-shot launch-and-capture check, `LaunchCamera.ps1 -AgentSilent -VerifyAndClose` owns and terminates the process it starts. For a longer automation session, retain the launched Viewer PID from the fresh `LaunchLogs/last-launch.json`; after clearing the session, terminate that specific owned process through the host's normal process interface. Do not mistake a launcher/VBS PID for the UE process PID. A repeated session can reuse an existing process where appropriate, but this launcher currently rejects an already-active/unknown process in `-AgentSilent` mode.

## Human entry

`Start_Camera_Viewer.vbs` opens a visible Viewer without a persistent console. Hold LMB to orbit, Shift+LMB to pan, wheel to zoom, F to focus and Home to reset. Mouse-up or Esc releases the pointer; Ctrl+Q exits. The white environment has no obstructing floor. Automated inspection should use the API rather than stealing the desktop mouse.
