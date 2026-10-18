# 删除项的功能与安装配置对照

此表说明原私人配置的功能，不复述原值。机器可读的精确 token → 文件/行号清单在 `placeholders.json`；具体路径由安装 AI 从实际主机发现并经用户认可后填写。

| 配置键 | 原值承担的功能 | 真实消费者 / 安装 AI 应做的事 | 验证 |
|---|---|---|---|
| REPO_ROOT | CAH 源码 checkout / 命令工作目录 | bridge_supervisor 的 TargetRepo、角色文档中的 BAT 调用；与 Start_CAH -RepoRoot、Git remote/main 对齐 | .git 在正确目录；不指向中转目录；fetch/ff-only 不丢改动 |
| BRIDGE_ROOT | 本机消息队列、监督器、能力缓存、日志 | Start_CAH、Reset BAT、local_bridge/server.py、host/capabilities.py、bridge_supervisor.ps1；选仓库外可写根 | /health.root；runtime/bridge-supervisor.json 与启动参数一致 |
| MANAGED_RUNNER_ROOT | 普通作业 Runner 安装 | Start_CAH RunnerRoots；用户授权注册到新 private repo | run.cmd、bin/Runner.Listener.exe；在线及真实作业执行 |
| COLD_RUNNER_ROOT | Cold/Burst Runner 独立安装根 | Start_CAH ColdBurstRoot；由授权流程控制启停 | 与代码/Managed/Shot 路径独立；run.cmd/bin 有效且 READY_OFF 不被误判为 BUSY |
| COLD_RUNNER_NAME | 新私有实例的 Cold/Burst Runner 名称 | Start_CAH 与角色协议中的 Runner 身份 | 由新用户注册并实际核对，不复制维护者旧名 |
| SHOT_RUNNER_ROOT | 独立短任务 Runner 安装 | Start_CAH RunnerRoots；独立目录与凭据；仅 cah-shot 标签 | 不被普通工作流选中；无害短任务实际完成 |
| RUNNER_BASE | Runner 系统和 short-task-cache 的父目录 | Foreground 短任务缓存说明；不是某个 Runner 的 _work | 任务缓存可写且清理只覆盖本任务 |
| BROWSER_ROOT | 原生 Host 状态、绑定、日志与 endpoint | Start_CAH、Reset、playwright_host/config.py、__main__.py、mcp.py | 只有一个 Host；state.json 的 Task Cell/lanes/Foreground 属于新账户 |
| TOOLS_ROOT | Node Playwright MCP/CLI 安装与 runtime.json | install_playwright_tools.ps1、playwright_cli.ps1、mcp.py | 锁定依赖安装成功；MCP/CLI 版本和 endpoint 一致 |
| BROWSER_BASE | 浏览器组件总目录/文档引用 | 按 placeholders.json 中记录的剩余文档引用配置；并非代替 BROWSER_ROOT | 不把一般浏览器用户目录当此目录 |
| PROFILE_ROOT | 专用 Chrome 登录 Profile | host/start_playwright_host.ps1；由用户在独立 PlaywrightChrome Profile 登录 | CDP 9222 对应这个 Profile，不导出 Cookie、不共享普通浏览器 |
| CHROME_PATH | 实际浏览器可执行文件 | 检查已安装 Chrome，再填 host/start_playwright_host.ps1 默认值 | Test-Path + 产品版本；不把某个维护者安装盘符当通用默认 |
| WORK_ROOT | 外部任务/Blender 与 CLI 工作区 | executors/blender_case.py、install_playwright_tools.ps1 workspace | 位于用户批准位置，任务目录隔离，有足够空间 |
| DELIVERABLES_ROOT | 用户接受的输出存放位置 | 工程交付规划，不是可无条件清理缓存 | 输出与原始资产保留；Task 的 project_directory 填真实工程目录 |
| TEMP_ROOT | 可清理暂存区 | 仅用于明确归属的缓存/中间材料 | 不指向源码、Profile、凭据或接受后的交付物 |
| REPOSITORY | 所有角色 wake、调度/拓扑与 evidence Git 引用的目标仓库 | planner_runtime、scheduler、topology、worker、parallel_branch_finalize、branch_script；整套渲染，禁止只改 remote | 搜索旧 token 为零；网页角色确实读写新 owner/repo/main |
| RECORD_REPOSITORY | 可选冷记录归档目标 | state/record-store.json 与 RECORD_ARCHIVE 文档；默认 disabled，不自动创建服务 | 单独授权且实际实现/验证之前保持禁用 |
| TRANSFER_REPOSITORY | 可选跨仓库资源中转/设计示例 | 非基本安装依赖；设计稿仅为示例 | 不自动创建/写入额外仓库 |
| TASK_CELL_KEY | Planner/Helper 执行 Project 的稳定身份及 reset 目标 | playwright_host/config.py、reset_cah_semantics_cli.ps1；安装 AI 用真实已授权地址核对 | 与 Host state.json 的 task_cell 相同；不能是个人普通聊天 Project |
| LANE00_KEY / LANE01_KEY | Worker 各 lane 的 Project 身份 | config.py 与 state/lanes.json 必须一致；只启用实际配置的 lane | Project 唯一，池为空；registered_count/enabled_count 正确 |
| FOREGROUND_URL | 最终结果/用户问题的投递对话 | 首次原生 Host -ForegroundUrl，写本机 state.json；`first_start.ps1` 提供已渲染的入口 | 精确 /c/ 对话可访问，不是 /project；确认结果送达 |

## 不应还原的删除项

个人 Skills/import 快照：不恢复，Registry 从零开始。私人 workflows/toolbox：不恢复旧 payload，按新任务在新私库配置。历史任务、memory、case、evidence、results、request receipts、conversation IDs、机器 capabilities cache：不迁移到新实例。showcases、旧迁移/交接记录：本轮不附带。作者版权署名不属于用户运行绑定。当前源码许可为 AGPL-3.0-only，早期已分发的 MIT 副本保留其既有授权；见 LICENSE/NOTICE。

## 不属于个人路径的保留项

ProgramFiles/ProgramData 等操作系统标准发现位置、Windows 驱动器枚举、loopback 8765/9222、测试使用的 example.invalid 与 synthetic IDs 是功能/测试材料，不是私人安装记录。保留发现机制，不保留自定义磁盘上的 Blender 搜索根。换平台/端口需全链路修改并重新验收，不仅改某一个示例。


单 lane 安装：LANE01_KEY 允许空值，未提供的 lane 不注册、不参与 reset。可选记录/中转仓库允许空值并保持未启用。合成单元测试路径 `X:\CAH_TEST\UnrealEngine` 不是安装默认值。


## v1.0.5 脱敏后重新规划

本文件只描述用途，不提供维护者的原始个人路径或账户值。三个 Runner 的重新部署、Cold/Burst 的根和名称、Browser/ChatGPT Project 身份、Tool/Workflow 缺省情况与用户私有资产隔离必须在目标 Windows 环境重新规划核验。阅读 [UPGRADE_AND_REDEPLOY_V1_0_5.md](UPGRADE_AND_REDEPLOY_V1_0_5.md)。

## 1.0.5 部署检查：脱敏值不是可还原的默认值

新用户必须重新确定 GitHub 私有运行库、目录布局、Runner/Cold-Burst 容量、专用 ChatGPT Project 与 Foreground 对话、浏览器 Profile、主机能力、日志与归档位置。所有 `__CAH_*`、`CAH_OWNER/*` 及示例标识符必须作为**未配置项**处理；不得使用维护者真实路径或从私人运行证据中还原值。先验证实际权限、连接和安全边界，确认无在途任务被覆盖，再做新的安装/迁移计划和小规模端到端测试。更多见 `installation/README.md`。
