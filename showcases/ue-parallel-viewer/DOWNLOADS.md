# Downloads and dependencies

[Case](README.md) · [中文案例](README.zh-CN.md) · [Agent operation guide](AGENT_VIEWER.md)

## Available now

- [Agent API client — Python source](viewer_client.py): control a running Viewer, change presets/modes and retrieve a completed PNG. This is a separate HTTP client, **not the Viewer executable**.
- [Native perspective screenshot](images/perspective.png), [low-angle screenshot](images/below.png), [bottom screenshot](images/bottom.png).
- [Structured evidence and timeline](evidence.json).
- [Original Blender camera case and source asset](../camera/README.md).

## Viewer package

The Viewer has been built and used locally, but **its full public download is pending a module-specific licensing decision**. No placeholder download is presented as an available binary. The intended package is a separately built Viewer/source distribution with launch helpers and this agent guide; it is not the entire private CAH workspace.

The current local version requires a matching **Unreal Engine 5.6.1** installation and Windows x64. A source build also needs the compatible C++ build tools. It is Editor-hosted `-game / DX11 / SM5`, not a portable standalone executable. Unreal Engine, Editor binaries, private browser profiles and host logs are not part of the planned public download.

CAH's existing AGPL/commercial licensing files remain unchanged. Because the Viewer module links directly against Unreal, its permissions need to be settled separately before releasing it as a usable engine-linked package. See [Epic's EULA, sections 5 and 6(c–d)](https://www.unrealengine.com/eula/unreal) for the underlying distribution and compatibility restrictions. This is a release-status notice, not a replacement license.

## 中文

当前可下载 AI 接口客户端、原生截图、结构化证据，以及前一案例的 Blender 相机资产。**客户端不是 Viewer 本体。**本地 Viewer 已验证可用，完整公开下载仍待其独立授权确认；不附带 UE 本体，也不改变 CAH 的既有许可证。避免提供一个看似可下载、实际不可用或许可不明的包。
