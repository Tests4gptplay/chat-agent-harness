# Downloads and dependencies

[Case](README.md) · [中文案例](README.zh-CN.md) · [Agent operation guide](AGENT_VIEWER.md)

## Available now

| Item | Contents |
|---|---|
| [Agent API client](viewer_client.py) | Python client for a running Viewer: camera presets, display modes and completed PNG retrieval. **Not the Viewer itself.** |
| [Perspective](images/perspective.png) · [Below](images/below.png) · [Bottom](images/bottom.png) | Original native viewport images from the accepted run. |
| [Evidence and timeline](evidence.json) | Sanitized roles, execution intervals, results and content hashes. |
| [Blender camera](../camera/README.md) | Original camera case, procedural scripts and asset download. |

## Viewer package status

**The full Viewer download is not yet published.** The local implementation has been built and tested; public distribution of its directly UE-linked module still needs the copyright holder's module-specific licensing decision. The HTTP client above must not be mistaken for a runnable Viewer download.

CAH's existing AGPL/commercial license files are unchanged. No separate Viewer license is granted by this page. See [Epic's Unreal Engine EULA, sections 5 and 6(c–d)](https://www.unrealengine.com/eula/unreal) for the engine-code, tool-distribution and license-compatibility provisions.

## Unreal Engine: install separately

**Unreal Engine is not bundled or mirrored here.** Use [Epic's official installation guide](https://dev.epicgames.com/documentation/unreal-engine/install-unreal-engine) to install the engine through the Epic Games Launcher. Select the matching **5.6 line with the 5.6.1 hotfix** used by this case; later engine versions have not been validated for this Viewer build.

The tested host is Windows x64. The current Viewer uses the installed Editor runtime with `-game / DX11 / SM5`; it is not a self-contained executable. Building its source also requires the matching C++ toolchain. The [Agent guide](AGENT_VIEWER.md) explains silent launch, camera presets, screenshots and session/process shutdown without imposing an asset-production workflow.

## 中文

案例、截图、AI 操作说明与 Python 接口客户端已公开；**Viewer 本体下载尚未发布，客户端不能替代它**。本地已验证的 Viewer 直接依赖 UE，其单独分发许可仍需作者决定；CAH 原有许可证不变。

UE 本体不随包提供，请通过上面的 Epic 官方安装入口安装匹配的 UE 5.6.1。当前版本在 Windows x64 上使用已安装的 Editor 运行，不是免安装程序，也没有验证其他 UE 版本。公开材料不包含私人路径、浏览器资料、原始主机日志或内部开发计划。
