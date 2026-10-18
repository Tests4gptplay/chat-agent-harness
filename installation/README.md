# CAH 纯净发行版安装说明 / AI-led onboarding

> 本目录是公开发行的未配置源码模板，不是运行实例。先核验目标运行仓库为 private，再配置、启动。保留源机制，不附带个人 Skills、私有 GitHub workflows/toolbox、私有 showcases 或运行历史；已批准的独立公开展示素材另行保留。



**v1.0.5 必读 — 公开源码已脱敏，安装时必须重新规划用户自己的私有绑定：** [隐私脱敏与重新部署/升级验收](UPGRADE_AND_REDEPLOY_V1_0_5.md)。不能恢复原维护者的 GitHub 仓库身份、Project Key、本机路径、浏览器 Profile、Runner/工作流和私人 Skill；新实例需重新配置三个 Runner 角色（Cold/Burst 可保持关闭）并通过真实权限和执行验证。

先读 `AGENTS.md`、本文件、`CONFIGURATION_MAP.md` 和 `OMITTED_COMPONENTS.md`。部署 AI 必须先确认目标操作系统、用户授权、已有软件与已有任务。Windows 是此版本启动脚本的参考平台；不要把文档存在当作全新 Windows/新账户安装已经实测成功。


## v1.0.5 候选版：脱敏配置与重新部署规划（必读）

本公开仓库是经过脱敏的**未配置发行源码**，不是维护者私人实例的可运行快照。发布时刻意移除了账户身份、本机绝对路径、GitHub Runner 注册信息、ChatGPT Project 和对话 ID、浏览器 Profile、凭据、运行状态、活动 Task/Planner/Worker/Helper、日志和证据、私人 Skills 与任务工作流 payload。这些删除并非软件缺陷，也不意味着应当从他人的私人仓库复制回来。

**首次部署必须重新规划，而不是还原原作者的配置**：部署 AI 应先盘点用户自己的操作系统、授权、GitHub **private** 运行仓库、目录/磁盘、可用 Runner、Playwright/Chrome Profile、专用 ChatGPT Projects、Foreground 对话和实际任务所需的 workflows/skills。再根据 `installation/CONFIGURATION_MAP.md` 与 `installation/placeholders.json` 填写新的绑定；仅在私有运行副本中保存敏感值，并逐项核验仓库可见性、权限、Runner 标签/在线状态、Host/Bridge、任务回执及 Helper 故障恢复。缺少工作流时按需新建可审查、最小授权的运行入口，不从维护者私有仓库直接复制个人 payload。

**已部署的私有实例不能直接用公开代码覆盖**：先冻结或正常结束活动任务，备份已有工作、私人配置和运行记录，列出旧新版本差异和迁移/回滚方案；在隔离位置生成新副本并验证，确认后再切换。绝不把新的真实凭据、路径、Project key、聊天链接、Runner token、任务日志提交到公开仓库。

本发行的离线测试只能证明所测源码与合成数据行为；**不能等同于未经实际执行的全新 Windows 主机安装或完整 Helper 恢复验收**。发行说明中应如实列出实际运行测试与尚未验证项目。

## 1. 前置条件与权限

需要 Git、同一个 Python 3.10+ 解释器、PowerShell，以及按 `host/playwright-tools/package-lock.json` 安装的 Node/Playwright 工具。先检查现有软件；专业软件不在基础安装范围，Blender/Unreal 等由用户选择并批准。保留 Python 依赖的环境隔离，运行测试检查实际可用性。

用户负责登录、账户授权、私有仓库创建/批准、Runner 注册和专用 ChatGPT Projects。AI 逐步指导具体操作，但不得要求把密码、Cookie、访问令牌或 Runner 注册令牌贴入聊天、配置模板或 Git。GitHub connector 必须对新私有运行仓库具有实际读写能力；不要把公开发行库或中转仓库当成运行仓库。

## 2. 配置模板，而不是盲目替换盘符

复制 `config.example.json` 到源码以外、仅本机可读的文件。AI 根据 `CONFIGURATION_MAP.md` 逐项填写 `values`。占位符总表和每个真实消费文件在 `placeholders.json`；原始私人路径不写回清理报告。

`REPO_ROOT` 是最终运行 checkout，不是中转目录。运行态、浏览器 Profile、Runner 凭据、临时区应位于仓库之外；Short Task 与 Managed Runner 必须分开目录。路径允许空格，但本模板的保守渲染器拒绝引号、反引号、美元符、百分号、换行和 shell 元字符；遇到这种目录请选一个更简单的新安装路径，不取消校验。

`TASK_CELL_KEY`、`LANE00_KEY`、`LANE01_KEY` 必须来自用户实际授权的专用 Projects。显示名称不是身份。参考名称为 CAH Task Cell、CAH Sandbox0、CAH Sandbox1。通过已登录的专用 Chrome/Playwright 读取完整 Project 地址，核对 `g-p-...` 与 `/project`；不要拿个人普通聊天当可清理的执行池。最小安装只需 Task Cell 与 lane-00 两个不同 Project：`LANE01_KEY` 留空，`enabled_lanes` 为 `["lane-00"]`，配置器不会注册虚假的 lane-01。提供第二条 lane 时必须是真实且不同的 Project。`FOREGROUND_URL` 必须是精确 `/c/...` 对话，不是 Project 根页。

Chrome 使用独立 `PROFILE_ROOT`（建议名称 PlaywrightChrome），而不是用户的日常 Profile。Chrome 路径由 AI 检查实际安装位置，再填 `CHROME_PATH`。只绑定 loopback CDP 9222 和 Bridge 8765；端口目前保留源码约定，不宣称单改一处可以全链路改端口。

在核验新仓库确实 private、Projects 已在授权浏览器确认后，设置配置 JSON 的两个确认布尔值。它们是安装记录，不替代实际账户/网页核验。

```powershell
# 参数由安装 AI 替换为本次真实位置；不在本发行目录里原地配置。
py installation\audit.py
py installation\configure.py --config <LOCAL_CONFIG_JSON> --output <NEW_OPERATIONAL_DIRECTORY>
```

配置器只创建一个新的文件副本，不创建账号、不克隆、不推送、不安装软件、不注册 Runner、不启动或删除浏览器对话。拒绝覆盖已有目录，不接触已有任务。输入清单哈希不一致时停止。复制后 `installation/configured.local.json` 标记的只是模板渲染完成，不是安装验收。

配置值会出现在新实例的代码/文档中，所以新运行仓库必须保持私有。不要把配置后的副本推回未配置发行版或公开库。更换路径/仓库/账户要回到未配置模板另建副本；已有实例迁移需单独核对活动任务、本机 `state.json` 和权限，不能用本配置器覆盖。

## 3. 建立新私有运行仓库

AI 在新目录初始化 Git、连接用户批准的新 private remote，并确认 branch main。不要复制原仓库 `.git`、历史、remote、hooks 或凭据。人工确认 GitHub 仓库可见性。此快照中的 `state/chatgpt.json` 为 IDLE，`state/lanes.json` 无任务池；没有继承 Planner/Worker/Helper 对话或未消费 wakes。

运行 `py installation\preflight.py`，确认未配置占位符已清零。随后检查角色 wake 中的仓库地址、Git remote、Runner 所属仓库、网页 Git 工具授权完全一致。

## 4. 安装浏览器执行工具

使用运行副本的 `host/install_playwright_tools.ps1`，指定本次 `ToolsRoot`、`Workspace`；它复制锁定的 package manifest/lock 并执行 npm ci，记录本机 Node/workspace。Python 的 Playwright 与 Node MCP/CLI 是两套依赖，安装 AI 要核对同一 Python 解释器中能 import playwright，再按项目所需补齐依赖。不要不经核验更新锁文件。

用 `host/start_playwright_host.ps1 -RepoRoot ... -RuntimeRoot ... -ChromePath ... -ProfileDir ... -ForegroundUrl ...` 绑定新 Foreground。首次绑定前先准备 Bridge，不并发启动第二个 Host。`host/playwright_cli.ps1` 用同一 9222 CDP，不创建第二套登录会话。网页登录由用户完成，不导出 Cookie。不要调用会关闭用户浏览器的 browser.close() 来结束只读检查。

## 5. Runner 与缺省工作流

### 与 v1.0.4 相比：部署时新增的验收重点

| v1.0.5 功能变化 | 新私有实例必须检查 |
|---|---|
| 即时 Harness 错误 Helper | 主机错误事件能够触发有界 Helper；证据和精确清理有效，不把报错当修复成功 |
| Worker 正常交接和异常 Watchdog | 区分约 10 分钟健康交接与 30 分钟同代物理恢复；检查持久化 Reply/Result、Generation/Fence 不串线 |
| Git 语义完成 | 末次 Git Result/Outcome 的 `turn_signal` 可由 Harness 读回；不再依赖 `semantic_sync` |
| 三类 Runner | 主 Managed、独立 `cah-shot`，以及默认不启动的 `backup` Cold/Burst；三者凭据、目录、标签重新配置 |
| 运行能力缺省 | 源码不随附私有 workflow/toolbox；每个所需执行入口经授权独立配置并通过真实 Action/回执 |

新用户应先读 [v1.0.5 隐私及重新部署验收](UPGRADE_AND_REDEPLOY_V1_0_5.md)，在 **自己的 PRIVATE 运行仓库**重新建立所有绑定。旧版安装截图、维护者私有目录或 GitHub Release 所附历史实例配置不是可以复制的默认值。

新私有仓库 Settings → Actions → Runners 中取得当前官方注册说明，分别准备 Managed 与 Short Task Runner。令牌只在本机交互使用，不持久化。

Managed 使用普通 self-hosted/Windows/X64 标签；Short Task 使用 `cah-shot` 且不带普通默认标签。每个 Runner 独立根目录、名称、工作区和凭据。`MANAGED_RUNNER_ROOT` 与 `SHOT_RUNNER_ROOT` 要指向包含 run.cmd/bin 的安装根，不是 `_work` 子目录。

**本包没有任何 `.github/workflows` 或 toolbox payload。Runner 在线并不代表可以执行任务。** 安装 AI 必须按 `OMITTED_COMPONENTS.md` 在用户的新运行仓库内，依据当前任务与授权配置所需执行入口；未配置时明确报告 `WORKFLOW_NOT_PROVISIONED`，不能假称完整任务链路可用，不从私人旧库偷拷 payload。

## 6. 启动、重复启动和验收

完成配置、Git 连接、依赖、授权与所需 workflow 后，使用 `Start_CAH.bat`。它调用 `Start_CAH.ps1`，执行前会拒绝未配置模板；之后保留原版的 main 快进、Bridge、Runner、原生 Host/CDP 检查。它不是一键安装器。

AI 核验 Bridge `/health` 的源码目录/分支/runtime root、CDP `/json/version`、专用 Profile 登录、单一 Host 进程、**Primary Managed 与 Shot 两个常驻 Runner** 的真实名称/标签、**独立 Cold/Burst Runner 的配置与默认 READY_OFF 状态**、规范 lane 与本机 Host `state.json` 一致。再次启动应复用而非重复创建。首次 Foreground 使用原生脚本参数持久化；总入口后续复用本机绑定。

验收分开记录：离线源代码测试；依赖/进程启动；真实 Git 读写；最小 managed 任务；独立 Short Task；需要时的换代/Helper/清理。每项记录实际结果和证据，不把上一项 PASS 当下一项 PASS。角色把语义进度写入绑定 Reply / Planner Memory，并将包含 `turn_signal` 的 Result / Outcome 作为最后一次 Git 写入；Harness 读取并核验该结果后推进，不再要求 `semantic_sync` 或一词聊天结束作为完成凭证。

## 7. 日志、停止与迁移

日志消费位置逐项列于 CONFIGURATION_MAP：Browser runtime 的 state.json/host stdout/stderr/browser-endpoint.json；Bridge 的 logs/runtime；Runner 各自 `_diag`。这些均为新实例私有状态，不能提交到模板。

停止只针对已核验属于本实例的 PID/根目录：先 Bridge supervisor 后 child，再原生 Host、对应 Runner。外部软件进程另按任务归属处理；不要全局杀 Python/Chrome。`Reset_CAH_Hot_State.bat` 是显式破坏性维护：会清空专用 Projects 的 CAH 执行对话并重置 Git 热态，不是安装或排错首选。使用前读 `docs/SAFETY_STOP.md`、Foreground 的完整 reset 合约，核对目标、备份、活动工作以及 Git 将被 reset 的本地修改。

## 8. 给安装 AI 的交付要求

输出本机配置记录（仅存私有部署位置）、权限核验、安装/依赖版本、清理过的测试结果和未完成项。不要在公共 Issue/截图中暴露私有 URL、绝对个人路径、原始运行日志或凭据。此源码快照没有声称第三方新机器已经安装成功。


## 配置与协议身份的区别

`REPOSITORY` 决定实际 Git 读写目标。源码中的通用 `git-agent-harness` project_id 和 `GAH_*` v1 wire markers 是兼容协议名称，不是维护者账户配置，不要为了改仓库而盲目改动。可选的 RECORD_REPOSITORY、TRANSFER_REPOSITORY 可留空，不会配置或创建额外服务。REF/URL 的 Project key 必须通过实际网页验证；配置器只负责安全渲染。



如果启动时发现 loopback 8765 上是另一实例的 Bridge，纯净版会拒绝启动，且不会自动停止该进程。安装 AI 先确认实例归属与用户意图；不把抢占端口当作迁移。配置器会将本次 enabled_lanes 同步到 Start_CAH 与 Bridge supervisor 的默认 lane 参数。


## Test contract status

The six obsolete test expectations have been retired; they are not current open runtime defects. See [../audit/TEST_CONTRACT_CLEANUP.md](../audit/TEST_CONTRACT_CLEANUP.md) for the resolved record and `../audit/verification.json` for actual current validation results. No production mechanism was rolled back and no new skip/xfail was added. Offline validation and fresh-machine acceptance remain distinct.


## 从旧公开版本升级

旧公开 main 已通过 `archive/pre-clean-20260929` 保存。本版采用完整文件树替换，不叠加保留旧 Extension、workflow、showcase 或旧安装脚本。已配置的私有实例不能仅覆盖这些文件后直接启动：应先保留活动任务、私有工作流和本机绑定，再按本指南从未配置模板建立新副本。旧 Release 下载包仍为历史版本，不代表当前 main；参见 [发行说明](../RELEASE_NOTES.md)。


## v1.0.5 新增配置与恢复验证

三种 Runner 使用 MANAGED_RUNNER_ROOT、SHOT_RUNNER_ROOT、COLD_RUNNER_ROOT 和 COLD_RUNNER_NAME 重新规划；Cold/Burst 身份和根目录必须由新用户独立选择并验证，默认 READY_OFF。验证 Helper 即时 Harness error 恢复、Worker generation 交接、正常 Result 回执、异常 watchdog、Runner 占用与准确清理需在已完成用户自己的私有 workflows/Tool 配置后进行。公开版故意不附带私人 workflows，不能把仅有文档/源码当作全链路安装完成。详见 [专门部署/隐私指引](UPGRADE_AND_REDEPLOY_V1_0_5.md)。
