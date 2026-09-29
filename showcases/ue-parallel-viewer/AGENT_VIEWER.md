# QuickLook Viewer — showcase operation notes

This file documents the Viewer surface exercised by the showcase. The full UE-linked Viewer module is not distributed here; [viewer_client.py](viewer_client.py) is a client for an already-running compatible Viewer.

## Tested API surface

The showcase used a loopback `/v1` interface with operations for:

- health and status;
- opening a compatible input;
- listing meshes, materials, and textures;
- selecting a mesh or material;
- inspecting material slots and dependencies;
- camera presets and framing;
- Lit / Unlit / Wireframe modes;
- native viewport capture;
- closing the current preview session.

API availability alone was never treated as visual acceptance. The 2026-09-28 rerun accepted Phase 1 only after native Lit pixels were captured and inspected.

## Camera and display

Tested camera presets included perspective, front, back, left, right, top, bottom, and below. The visible Viewer also supported mouse orbit/pan, wheel zoom, focus/reset, pointer release, and normal exit controls.

The accepted rerun used a floor-free view. Its actual background was visibly black while the model remained bright and readable; this record intentionally does not repeat the older “white environment” wording.

## Capture semantics

A successful capture request means the Viewer accepted the request, not that the PNG is already complete. The included client waits for a correctly completed PNG before treating the capture as ready.

The accepted rerun produced four native Lit views — front, perspective, right, and below. Their exact sizes and SHA-256 hashes are recorded in [evidence.json](evidence.json).

## Scope

Phase 1 used a compatible classic PAK camera fixture. Phase 2 investigated real IoStore/package-store loading and MOD provenance, but native direct-preview work was intentionally stopped before final acceptance. This showcase therefore does not claim universal game/IoStore compatibility, a standalone Viewer executable, or bundled Unreal Engine.
