# Shared-Nothing Distributed Agent Fabric

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Relationship to the multi-lane runtime

This direction extends the same dynamic READY/claim/continuation model beyond one host.

The goal is not simply "more lanes on more computers." Tasks should remain migratable and recoverable even when Workers live in different processes, machines, networks, model providers or failure domains.

```text
                     durable task DAG + READY state
                               |
                     global scheduling decisions
                               |
             +-----------------+-----------------+
             |                 |                 |
         Node / Host A     Node / Host B     Node / Host C
          Worker A1         Worker B1         Worker C1
          hot cache         hot cache         hot cache
             |                 |                 |
             +-------- publish/checkpoint -------+
                               |
                               v
                durable semantic memory / evidence
                continuations / results / refs / decisions
```

Tasks still belong to the DAG, not to the machine that last executed them.

## Shared-nothing does not mean no shared logical state

Nodes should not require shared RAM, one local filesystem, one browser process or direct Worker-to-Worker conversation.

They cooperate through durable contracts and references:

- task identity and dependency state;
- READY/WAIT transitions;
- ownership leases and fencing epochs;
- continuation checkpoints;
- semantic memory/evidence refs;
- artifact addresses and content hashes.

The shared state is logical and durable; its physical storage may be Git-backed control state plus object/content-addressed artifact stores or other replaceable backends.

## Cross-host work stealing and migration

An idle compatible node may claim READY work that originated elsewhere.

```text
Node B finishes current node
 -> publishes result / reusable memory / evidence
 -> releases previous lease/resources
 -> scheduler sees another compatible READY node
 -> Node B claims it with fresh ownership/fence
 -> loads only required continuation/memory refs
 -> executes
```

Likewise, a task suspended on Node A may later resume on Node C.

## Memory hierarchy across hosts

```text
node-local Worker hot context
           |
node-local task cache / scratch
           |
durable semantic memory / evidence
           |
managed/content-addressed artifacts
           |
cold archive
```

Only durable layers participate in recovery semantics.

Before ownership migrates, useful state must escape the local cache. Large artifacts need not live in Git; the canonical state may carry stable refs, hashes and provenance.

## Data locality and resource gravity

Moving compute is often cheaper than moving large data or rebuilding authenticated/tool environments.

Placement should therefore consider:

- large local datasets or artifacts;
- GPU/toolchain locality;
- authenticated browser/tool state;
- licensed or scarce software;
- privacy/trust boundaries;
- network cost and latency.

A nominally idle node is not necessarily eligible for a task.

## Failure and partition semantics

Distributed execution introduces:

- node crashes;
- network partitions;
- delayed stale results;
- duplicated recovery attempts;
- split-brain ownership;
- unavailable artifacts;
- partially visible state.

Generation/fencing and explicit ownership epochs are essential. A late Worker may publish diagnostics, but must not mutate authoritative scheduler state after its lease/fence becomes stale.

## Node identity and capability advertisement

A future node should have a stable authenticated identity and a bounded capability manifest. Scheduler-visible claims should be verifiable where possible rather than trusting arbitrary self-description.

The node identity is a routing/trust primitive, not task identity.

## Trust domains and secrets

Not all nodes should receive the same task packet or memory.

Sensitive inputs, credentials and private semantic memory may constrain placement to a trusted domain. Cross-domain scheduling should expose only the minimum data required for the selected work.

## Relationship to capability scheduling

The distributed fabric supplies heterogeneous capacity. Capability-centric scheduling decides which node is eligible and desirable for each READY task.

## Open questions

- node identity and authentication;
- lease expiry under network partitions;
- artifact addressing and large-object transport;
- capability attestation;
- data locality and trust domains;
- split-brain recovery;
- scheduler centralization versus partitioning;
- which state belongs in durable control versus node-local caches.

<!-- CAH_STATIC_LANE_MULTI_HOST_V0_BEGIN -->
## 最小可实施方案：本机线程过滤、独立 clone 与 Git 检查点接管

本方案替换先前补充的通用分布式准备案，改为一个小范围、可退出的实验：两台插件保留处理同一组线程的能力，各自在本机屏蔽一个线程；每个线程使用独立 clone；通过 `Tests4gptplay/temp` 交接已提交的资源。**worker 职责不变，不负责设备分配、修改其他线程或切换插件配置。** 以本节作为当前实施依据；动态节点注册、通用能力路由等不再列为首轮前置条件。文档前部保留的项目愿景仅是长期方向，不是本轮改造要求。

状态：方案已整理，代码尚未实现，未进行双机或断机实测。本文中的新增配置、命令入口和验收条目均为待实现设计，不是现有功能声明。

```json
{
  "proposal_id": "static-lane-multihost-v0",
  "status": "READY_FOR_IMPLEMENTATION_NOT_VALIDATED",
  "worker_role_changed": false,
  "runtime_changed": false,
  "multi_machine_tested": false,
  "control_plane": "existing_single_authority",
  "data_repo": "Tests4gptplay/temp",
  "stages": ["P0", "P1", "P2", "P3", "P4"],
  "acceptance_cases": ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09"]
}
```

### S1. 目标、职责与首轮范围

- 目标：在尽量不改变原有调度与 worker 语义的前提下，验证两个逻辑线程能由两台设备分别处理，并通过 Git 交接资源；随后验证执行端掉线后的检查点恢复。
- 插件：只增加本机线程接收/处理过滤。两端仍可配置相同完整线程列表，正常实验时白名单互补。
- worker：仍处理线程内任务，不新增设备管理职责。
- runner：执行相同版本代码；不同设备分别注册独立 runner 实例，不复制已注册实例的身份或凭据。
- Git：原运行库继续保存权威任务状态；`temp` 保存资源与候选产物。不要让两个库各自维护一份可独立决定 DONE 的状态。
- 首轮只用可重放的文件任务、相同软件能力和小型文本产物。不以 UI、付款、发信、发布等外部副作用验证自动重做。
- 首轮继续使用一个权威调度/结果接受入口。两端桥接不能未经协调就各自成为另一份权威调度器。执行端容错不等于唯一协调器容错。

### S2. 本机配置与目录布局

| 设备 | 原有线程配置 | 本机允许处理 | 本机屏蔽 |
|---|---|---|---|
| A | lane-00、lane-01 都保留 | lane-00 | lane-01 |
| B | lane-00、lane-01 都保留 | lane-01 | lane-00 |

新增字段建议仅保存在 `chrome.storage.local`，不进入全局拓扑或浏览器跨设备同步配置：

```json
{
  "localLaneFilterEnabled": true,
  "localLaneAllowlist": ["lane-00"]
}
```

B 将白名单改为 `lane-01`。默认 `localLaneFilterEnabled=false`，保持日常单机处理全部原有线程的行为。过滤开启而列表为空时，表示本机停止所有新接单；非法配置应显示错误并停止接单，不能悄悄退回允许全部。

```text
每台机器的 EXPERIMENT_ROOT/
  lane-00/
    resource-cache/             # temp 的独立 clone，需要该线程时创建
    attempts/ATTEMPT_ID/        # 该次执行的独立、固定输入工作区
  lane-01/
    resource-cache/             # 另一个独立 clone，不与 lane-00 共用工作树
    attempts/ATTEMPT_ID/
```

这是“线程一一个文件夹、线程二一个文件夹”。B 接管 lane-00 时，使用 B 自己的 lane-00 目录，不把恢复内容混入 B 的 lane-01 工作区。第三台可以承载已有第三条 lane，或作为预配置但本机暂停接单的备用机；不必为备用机凭空新增逻辑线程。

### S3. 最小改动清单与源码落点

以下接入点均已在公开仓库源码中核对；实施前再次核对实际运行库的目标版本，不假设两个仓库逐字一致。

| 部分 | 具体改动 | 保持不变 |
|---|---|---|
| `extension/popup.js` | 在线程卡片增加“本机暂停/恢复”，另显示本机状态；增加实验总开关 | 原有全局线程 `enabled` 与拓扑保存含义 |
| `extension/background.js` | 公用本机过滤判断，放在新接单之前；所有直接唤醒入口及会启动该 lane 新操作的维护入口复用它 | worker 协议、任务含义、全局线程列表 |
| 本机配置 | 保存上述两个新字段；禁用实验时恢复原默认路径 | 不把 A 的过滤覆盖到 B |
| runner 资源准备/发布 | 按 lane 选择独立目录；输入固定到提交；发布资源后再提交状态引用 | 原命令与验收逻辑 |
| 执行工作流 | 核对双机运行是否仍被全局串行锁限制；必要时改为每 lane 串行、跨 lane 可并行 | 同一 lane 的执行顺序及必要的共享资源锁 |
| 现有协调侧恢复入口 | 先接人工授权接管，再接超时触发；复用并验证代次/fence 校验 | 不让 worker 管理其他线程或改插件开关 |

已观察到的接入点：`pollEnabledLanes()` 按 lane 调用 `safePoll()`；`pollOnce(laneId)` 的 `claim` 请求携带 `lane_id`；`routeWake()` 解析目标 lane。因此本机白名单应在 claim 前生效，并对其他入口加一致校验，不能仅把按钮变灰。对本机屏蔽的新任务不 claim、不 consume、不发送响应开始确认；已经由本机接下的执行允许排空到安全边界，显示“正在暂停”，不要伪装为立即停止。

源码证据：`extension/background.js` blob `022f4d1475c862aac216078358681a2f70c70cf8`；`extension/popup.js` blob `e3411c1066f280a6bb7503827f877c1da9bbb08b`。当前已有 `dispatch_generation` / `fence_token` 字段，只说明存在可复用身份字段，不证明完整跨设备接管已实现。

**runner 与浏览器不强制同机绑定。** 首轮文件任务的输入完全由 Git 提供，可让两台兼容 runner 在同一池执行，调度不必先知道物理机器名；工作目录仍按任务/lane/attempt 隔离。若动作必须在对应浏览器所在机器执行，再增加静态 lane 标签路由，不能由浏览器过滤自动推断 runner 归属。

公开源码中的 `stage0-single-thread.yml`（blob `d20fd6eef99ef276ea1b577da059ecef6f414559`）使用 `concurrency.group=stage0-single-thread` 和通用 `runs-on=[self-hosted,Windows,X64]`。公开副本还设有仅私有仓库执行的条件，本方案不解除该限制。**若实验运行库沿用此链路**，增加第二台不会自行消除全局串行锁：需先解析动作的 lane，再给执行 job 设置 lane 级并发组。一个小的解析 job 可输出经校验的 lane_id；执行 job 引用该输出。实验阶段每 lane 至多一个未结束动作；保留 Git 中待处理动作的恢复扫描，不把每次 push 通知当成持久队列。真正需要跨 lane 串行的资源继续单独加锁。

### S4. temp 资源同步与检查点提交顺序

1. 每个 lane 的资源副本首次 `git clone`；之后在步骤边界 `fetch`，必要时对干净缓存做 `pull --ff-only`。执行中的 attempt 目录不 pull，也不更换输入。
2. 动作输入记录 `source_commit`、`temp_commit`、资源路径和 SHA-256。runner 从这些固定提交物化输入，不把“当前最新 main”当成该动作的输入定义。
3. 产物使用 `tasks/TASK_ID/lanes/LANE_ID/attempts/ATTEMPT_ID/STEP_ID/`，附清单。两个线程不共同覆盖 `latest.json` 或同名输出。
4. 先把资源/候选产物提交并 push 到 `temp`，确认远端提交和内容可取；再在原运行库发布引用该提交的检查点/结果记录。接收侧验证版本、哈希、代次和任务验收后，才标记 ACCEPTED。
5. 下游线程读取已接受的引用，fetch 对应提交并校验输入，随后执行。两库提交不是原子的：资源已上传但状态未接受时，只是可查询的候选；回执丢失先查询，不能盲目重做或直接算完成。
6. 独立 clone 隔离本地文件，但若共同推送 `temp/main`，远端仍可能拒绝非 fast-forward。发布助手只在隔离的发布工作区，对不相交产物提交做有界重试；发现路径冲突或所有权变化则停止并重验，不 force-push，不让自动文本合并决定所有权。
7. `temp` 是私有库。单独配置最小跨库读写授权；原运行库的默认 `GITHUB_TOKEN` 不能被假设具有该私有库权限。凭据不进入 Git、文档、截图或实验日志。

最小检查点清单：`task_id`、`lane_id`、`attempt_id`、`step_id`、`dispatch_generation`、`fence_token`、`source_commit`、`temp_commit`、产物路径/大小/SHA-256、恢复入口、验收结果。状态提交自身的 SHA 由外层记录引用，避免自引用提交哈希。

### S5. 掉线后恢复：只重做未确认部分

```text
C0 已接受 → C1 已接受 → A 执行 C2 时失联
                         ↓
存活观察者记录超时 → 权威入口撤销旧代次、授权新接管
                         ↓
B 允许接管 lane-00 → B 的 lane-00 独立目录恢复 C1
                         ↓
重做 C2 → 资源 push → 状态引用 → 验收后接受
```

- 失联记录由仍在线的观察者写入，不要求掉线机器自己提交。“没响应”先是怀疑，不直接证明旧进程已经停止。
- 第一轮由操作者确认接管；通过后再把同一路径接到超时触发。插件/运行时根据有效接管授权调整本机允许处理的 lane；worker 不操作这个开关。
- 只有确认旧执行身份被替换后，接管执行才能开始。旧机恢复先同步身份；旧代次的完成回执不能进入权威接受入口。资源候选可留存，但不覆盖已接受结果。
- C0/C1 的已接受结果保留。最后一个可恢复检查点之后未发布或未确认的工作可能需要重做；这是**步骤级回滚**，不是回退共享仓库历史，也不保证精确恢复进程内存。
- 接管不一定保持一机一线程：B 可临时处理两个 lane；各自目录仍独立，吞吐可能降低而验收不变。
- 缺输入、哈希不符或没有兼容工具时等待/报错，不带缺失输入继续执行。首轮只对可重放文件动作自动重做。
- 若唯一协调器也在掉线机器上，本方案不保证自动继续。首轮故障试验先隔离执行端故障；协调器高可用另测，不混进本轮成功结论。

### S6. 实施顺序与阶段门槛

| 阶段 | 实施内容 | 进入下一阶段的条件 |
|---|---|---|
| P0 准备 | 记录日常版本/配置；使用单独浏览器配置和实验目录；两台独立注册 runner；配置 temp 权限；生成单机基准 | 两台能取得相同固定输入；日常运行未被改写 |
| P1 本机过滤 | 实现默认关闭的实验开关和本机暂停；覆盖接单及相关自动入口；测试切换时排空 | A 只处理 lane-00，B 只处理 lane-01；过滤关闭与原单机行为一致 |
| P2 双机协作 | 实现每 lane 独立 clone、固定版本输入、产物清单与状态引用；处理所用执行链路的并发限制 | 日志证明任务分别落在两个执行设备；资源交接正确；最终产物匹配单机基准 |
| P3 掉线接管 | 先人工授权接管，再增加超时触发；验证旧代次拒绝和丢回执查询 | S04-S07 通过；人工与自动接管的记录明确区分 |
| P4 恢复日常 | 排空实验，停第二实例，取消第一台过滤，恢复记录的稳定版本和配置 | 第一台重新接管两个 lane；实验 runner 不再领取日常任务 |

P2 成功即可说明双机执行与 Git 资源交接在该 fixture 上成立，不要求先完成 P3。P3 成功才能报告对应故障场景的恢复能力。两阶段都不直接推导加速倍数或“任何机器掉线都无影响”。

### S7. 首轮验收矩阵

以下为待执行测试。fixture：两个线程分别从固定整数输入生成确定性文本产物，汇总阶段校验两者哈希并生成排序清单；与同一输入的单机串行执行比较。先不使用非确定性模型文本作为字节一致性的判据。

| ID | 场景 | 通过标准 |
|---|---|---|
| S01 | 双端都配置两个线程，白名单互补 | 每个新任务只由允许该 lane 的插件处理；被屏蔽端新增 claim/consume/发送次数为 0 |
| S02 | 过滤关闭、空名单、执行中暂停 | 关闭时保持原行为；空名单停止新接单；运行中排空而不重复执行 |
| S03 | 两个独立 clone 同时产生并交接资源 | 执行日志包含两个实际 HOST 身份；无跨目录污染；最终产物与单机基准一致；有界发布重试不丢产物 |
| S04 | C1 已接受，C2 push 前终止执行端 | 接管从 C1 开始；C0/C1 不重复；最终结果符合基准 |
| S05 | 资源/结果已发布，回执丢失 | 查询并复用已接受记录；同一结果不重复接受 |
| S06 | A 断网后 B 接管，A 随后恢复 | 旧代次接受数为 0；新接受结果不被覆盖 |
| S07 | 输入缺失、哈希错误或 Git 暂不可达 | 不推进为 DONE；保留可恢复检查点及明确错误/等待原因 |
| S08 | 退出实验，恢复单机日常 | 第二端不再处理实验/日常新任务；第一端恢复两个 lane；配置和稳定构建可核对 |
| S09 | 第三台作为备用接管同一 lane | 复用同一授权和独立 clone 流程；不要求 worker 或调度新增一套职责 |

P1 的配置/入口用例全部通过；P2 正常流程及退出流程至少重复 3 次；P3 每个适用故障点至少重复 3 次，保留失败与人工介入。重复次数是实验门槛，不代表证明故障概率为零。记录输入/代码提交、HOST、lane、attempt、代次、资源提交、哈希、事件顺序、退出码、重做步骤、检测/恢复耗时和人工操作。

### S8. 快速退出、交付物与证据边界

退出顺序：停止派发实验新任务 → 等待或显式中止并记录在途 attempt → 停用第二台实验插件及实验 runner → 先恢复稳定构建/配置，再让第一台取消本机过滤接管两个线程 → 跑 S08。若原实验改了 runner 标签或工作流，同时恢复预先记录的日常配置，确认没有残留任务被另一台领取。

首次实现应保持实验开关默认关闭；使用独立浏览器配置、实验目录及版本化构建，避免为了验证而覆盖日常环境。退出后保留 temp 的提交与日志用于复盘；不自动删除实验目录，不用 force-push 清历史。目录清理另行确认。

实施交付物应包括：插件补丁、按 lane 准备/发布资源的脚本、必要的工作流小改动、最小 fixture、接管/退出操作步骤以及逐项验收记录。本文仅交付实施方案，不包含上述运行时实现。

本方案不包含插件部署或跨设备接管的现成回滚脚本。文档结构和补丁的本地校验，不能代替 S01-S09 的运行时测试。
<!-- CAH_STATIC_LANE_MULTI_HOST_V0_END -->
