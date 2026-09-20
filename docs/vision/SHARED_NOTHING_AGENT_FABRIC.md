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

<!-- CAH_TRUSTED_MULTI_HOST_PREPARATION_V0_BEGIN -->
## Trusted multi-host preparation proposal / 可信多机分布式准备案

**状态：准备案，未实施、未完成多机验证。不是已发布能力、容错承诺或排期承诺。**

本节把“Git clone / pull 复制任务现场，持久检查点支持掉线后局部重做”的讨论收敛为可开发、可验收的最小方案。当前没有两三台独立设备可供故障注入，因此应保留方案和验证入口，不用主观把握代替测试结论。

```json
{
  "proposal_id": "trusted-multihost-v0",
  "status": "PREPARATION_NOT_VALIDATED",
  "source_commit": "44b56736f3da0941e901133d982a4cba3158f756",
  "runtime_changed": false,
  "multi_machine_tested": false,
  "target_node_counts": [2, 3],
  "initial_workload_class": "file_replayable",
  "initial_control_authority": "single_reconciler",
  "acceptance_cases": ["F01", "F02", "F03", "F04", "F05", "F06", "F07", "F08", "F09", "F10"]
}
```

### 1. 目标与可证伪假设

目标：两台、随后三台可信电脑使用同一私有 CAH 运行仓库，共同执行一个有依赖关系的任务；一台执行设备失联时，已接受成果保持有效，未完成的可重放部分由兼容设备接手。

**设计假设 H1：**现有任务身份、Git 交接、检查点、派发代次、fencing（过期执行隔离）与结果验证机制，可以减少多机扩展所需的新机制；无需重写完整任务系统，但仍需新增设备路由、失联判定、所有权转移及产物闭包检查。

**设计假设 H2：**在协调器和权威 Git 远端可用、至少一台兼容设备存活、全部恢复输入可取得、动作可安全重放的条件下，执行节点失联只增加等待与重做成本，不破坏已接受结果，任务最终仍可通过原有验收。

H2 不承诺推理文本逐字一致、二进制构建必然逐字节一致、零损失、严格最小成本或任意外部副作用的 exactly-once。这里的“结果不受影响”指需求与验收条件不降低；确定性 fixture 才要求产物哈希一致。发现检查点缺件、旧执行覆盖结果、重复外部副作用或不可恢复的必需设备依赖，均应否定对应条件下的 H2，而不是修改验收来宣布成功。

### 2. 已有基础与必须补齐的部分

以下判断基于上方 source_commit，它指公开发行库 `Tests4gptplay/chat-agent-harness` 的审阅快照，不要求接收本案的私有运行库包含同一提交，也不证明私有实例版本相同。文件存在和源码检查不是分布式运行验证。

| 已有基础 | 可复用的价值 | 本准备案要求补齐 |
|---|---|---|
| `docs/CONTINUITY.md`、Git 中任务与结果交接 | 新 clone 能取得已发布状态，不依赖前一段聊天 | 输入、脚本、环境约束及产物的完整恢复清单 |
| `harness/parallel.py` | DAG、并行分支、依赖屏障、单 lane 顺序回退 | 执行中失联后的重派；不是只在开始时少配一个 lane |
| `local_bridge/scheduler.py` | 派发身份、语义租约、代次和过期结果隔离 | 独立于失联节点的监测、跨设备接管、执行端校验 |
| `host/capabilities.py` | 已有主机能力探测入口 | 多节点能力投影、兼容性判断、稳定 node_id |
| `.github/workflows/stage0-single-thread.yml` | 自托管 runner 执行入口 | 精确 runner 路由和执行者身份回读 |
| `harness/git_publish.py` | Git 同步、发布失败处理 | 所有权变更时重新读取并验证语义前提，不能盲目 rebase 后重推 |

公开并行案例 `showcases/ue-parallel-viewer/README.md` 明确是两个 Worker 配一个 Windows runner，因此不能把该案例当作多机接管证据。

### 3. 最小部署边界与角色

第一阶段只做同一用户或同一信任域、2 台环境接近的设备，后续扩到 3 台。不做公共节点网络、拜占庭容错、经济结算、通用 work stealing 或自动迁移浏览器登录态。

```text
私有 Git 权威状态 + 已发布产物
              |
    单一协调器 / reconciler
       |                  |
  lane-00 -> node-A   lane-01 -> node-B
       |                  |
  独立 attempt 工作区   独立 attempt 工作区
       +--- 候选结果 -> 验证 -> 接受提交 ---+
```

- lane 是语义工作通道，runner 是实际工具执行位置，两者不是同一个概念。第一版可明确一一映射，不能仅凭“开了两个线程”推断用了两台机器。
- 每台机器 `git clone` 同一运行仓库，独立配置软件、runner、bridge 和所需浏览器绑定；凭据与绝对主机路径留在本机，不通过 Git 同步。克隆会复制可访问的历史，节点必须有读取该私有仓库的授权。
- 给 runner 配置可信的唯一标签或验证后的 runner group / labels 映射。通用 `self-hosted, Windows, X64` 标签不能保证任务落在指定电脑上。开工前回读实际 node_id、runner 身份和环境指纹。
- 只保留一个权威协调器负责 claim、续租、撤销及终态接受。它必须在本次执行节点故障之外继续存活；可运行在独立服务或独立协调运行环境中。第一版不宣称协调器本身高可用。
- 若把协调器也放在两台电脑之一，必须明确该电脑故障会暂停调度。未来再做带独立所有权 epoch 的协调器接管，不能把执行节点容错等同于任意整机故障容错。
- 已排队或已运行的 Actions job 不假定会自动迁移。设备失联后，协调器要撤销旧 attempt，尽力取消旧 job，再按新身份提交接手 job；即使旧进程无法被终止，也不能再获得权威发布资格。

### 4. Git 能提供什么，以及检查点闭包

Git 提供版本化复制、审计和已提交状态交接；同步触发、失联检测和调度仍由 CAH 完成。不能在活跃工具正在修改的共享工作目录上不断 `git pull` 来充当分布式执行协议。

**检查点成为可恢复事实的条件：**提交已到达权威远端、提交可按固定 SHA 读取、全部依赖对象及产物可取回且哈希匹配、验收状态明确。只有本地 commit、尚未 push 的记录不算已交接。

一个恢复包至少包括：任务/分支节点身份、需求与验收版本、输入和脚本版本、已接受结果引用、下一动作、环境约束、产物 manifest、动作是否可重放。产物 manifest 记录路径或稳定 URI、大小、内容哈希和来源 attempt。

- 小文本及小型稳定产物可直接入 Git；大二进制、频繁变化的缓存宜使用明确配置的对象存储或 LFS，并在 Git 里保存地址与校验信息。仅 clone 到 LFS 指针并不证明实际文件已取得。
- 外部产物先上传并验证可读，再发布引用它的检查点；同一 Git 树内可把结果和 manifest 一起提交。引用不存在对象的状态不能被标记为可恢复。
- 不同步密钥、浏览器 profile、机器锁、安装目录或未经筛选的缓存。无法迁移的能力形成 `WAIT_RESOURCE`，而不是假装只降低效率。
- 使用每个 attempt 独立的工作区与输出命名空间。新机器从已验证 SHA 创建干净的执行副本；保留失败现场供诊断，不清理用户目录，不回退共享主分支。

### 5. 候选数据契约与所有权转移

以下是**建议字段示例**，不是现有 schema 已支持的输入。实施前需逐项映射已有 task / dispatch / lane / host-capability 结构，避免另建一套不一致的身份系统。

```json
{
  "task_id": "TASK",
  "dag_node_id": "STEP",
  "attempt_id": "ATTEMPT",
  "assigned_node_id": "NODE",
  "lane_id": "lane-00",
  "generation": 1,
  "fence_token": "FENCE",
  "expected_control_head": "COMMIT",
  "checkpoint_commit": "CHECKPOINT_COMMIT",
  "input_manifest_ref": "MANIFEST",
  "required_capabilities": ["python"],
  "environment_fingerprint": "ENV_HASH",
  "lease_expires_at": "TIMESTAMP",
  "status": "READY",
  "replay_class": "file_replayable"
}
```

建议将节点状态与任务状态分开：节点 `ONLINE -> SUSPECT -> OFFLINE`；动作 `READY -> CLAIMED -> RUNNING -> RESULT_STAGED -> ACCEPTED`。失联只说明观察者无法联系，不等于旧进程停止。

1. 协调器从权威远端读取 HEAD 和任务前提，选取具有全部必需能力的存活节点。
2. 在同一次控制状态变更中认领动作，分配新 generation / fence 和唯一 attempt；确认远端接受后，执行端才取得开工许可。
3. 执行端校验派发身份、设备身份及检查点闭包，在隔离工作区执行；候选结果只能进入自己的 attempt 命名空间。
4. 协调器收到结果后重新检查当前 owner / generation / fence、输入版本、产物哈希和验收，再接受终态。重复回执应幂等返回既有接受结果。
5. 离线检测由仍在线的观察者依据独立的心跳/租约与超时完成，不能要求失联设备自己写“已掉线”。失联事件至少记录观察者、被观察节点、最后确认时间、判定依据和受影响 attempt。
6. 超时后，在一个受约束的控制变更中撤销旧身份并生成新的接管身份，再重新派发。断网的旧节点恢复后先重新同步；其候选结果可保留诊断，但不得覆盖新权威状态。

**Git 发布约束：**控制状态更新必须以读取到的确切远端版本为前提。采用单写者与受约束的 ref 更新；发生竞争/推送拒绝时，重新读取所有权并重新判定，再生成变更。现有 `git_publish.py` 有 rebase-and-retry 路径，不能未经审核直接用于所有权认领；文本合并成功不等于两个认领在语义上兼容。不得通过 force-push 覆盖竞争者的记录。

执行端的 fence 只能约束真正检查它的发布入口。若节点仍有任意改写权威分支的权限，第一版只能声明“可信、遵守协议的节点”范围内有效；后续通过分支保护或验证入口限制权威写入。fence 不能自动撤销已发生的外部副作用。

### 6. 掉线后的最小回退单元

这里的 rollback 指**丢弃或隔离未被接受的 attempt 输出，并从最后有效检查点重做未完成后缀**，不是倒退整个 Git 历史，也不是恢复远程进程内存。

```text
C0 已接受 -> C1 已接受 -> A 执行 C2 -> A 失联
                                   |
                     撤销旧代次 / 分配新代次
                                   |
                   B 加载 C1 -> 重做 C2 -> 验收
```

- 已接受的 C0/C1 不因聊天或设备改变而重复执行；尚未确认提交的 C2 可重做。
- “提交成功但回执丢失”必须先按 task / attempt / result 身份查询权威记录，不能盲目重做。
- 已上传但未接受的产物属于孤立候选，不自动晋升；先保留用于排查，清理另设保留策略。
- 第一版只自动接管 `file_replayable` 动作。外部服务写入只有在接收方支持稳定幂等键、可查询执行状态或有明确补偿协议时才能扩展。发送、付款、发布、删除等不明结果动作进入 `NEED_RECONCILIATION`，不自动重复。
- 若长工具步骤内部没有有效检查点，重做单元就是整个步骤；不能把心跳频率当成工作恢复粒度。
- 重做成本取决于最后一个**已发布且可恢复**检查点后的工作量，加上故障检测、排队、下载和环境准备。只有检查点发布间隔确实受控时才能给出丢失工作上界；这不是理论最小成本保证。

### 7. 分阶段实施：没有多台机器时先做什么

| 阶段 | 工作 | 允许的结论 |
|---|---|---|
| G0 文档与契约准备 | 保存本案、字段映射、状态不变量、fixture 和验收标准 | 方案已整理；不能声明原型或容错已实现 |
| G1 单机多进程模拟 | 临时 bare Git 远端、2/3 个独立 clone、独立 node 配置/进程和协调器；确定性文件 DAG | 仅说明被测试的协议及 Git 竞争场景通过，不等同真实多机 |
| G2 双机实测 | 两台独立机器和执行故障范围之外的协调器，逐项注入 F01-F09 | 通过的故障条件下可恢复；列明硬件、网络、软件与测量边界 |
| G3 三机与能力路由 | 加第三台设备、能力不对称和无兼容节点场景，执行 F10 | 仅扩展到实测拓扑和能力范围 |

G1 可预先设计并随后实现，不必为了缺少设备停止文档准备；但本次文档交付没有运行 G1-G3。模拟用合成输入和临时目录，禁止自动绑定真实私有运行实例或调用有副作用的生产工具。

首个 fixture：两条独立分支分别生成确定性文本文件，reducer 在两份产物通过 hash 校验后生成最终清单；和单节点串行控制组使用同一输入与验收。不要先用大型应用、复杂 UI 或真实账号动作增加变量。

### 8. 故障注入与验收矩阵

以下均为待执行测试，不是历史结果。

| ID | 故障/场景 | 必须观察到的结果 |
|---|---|---|
| F01 | A 在 C1 后、C2 提交前被终止 | B 从 C1 重做；C0/C1 保留；最终 manifest 符合控制组 |
| F02 | 候选结果提交到远端后丢失回执 | 查询已有记录；同一结果只接受一次；不因缺 ACK 盲目重做 |
| F03 | A 断网但继续运行，B 接手后 A 恢复 | 旧 fence 的接受请求被拒绝；旧输出不覆盖新产物 |
| F04 | 两个候选执行者竞争同一 READY 动作 | 同一代次只有一个被接受的 owner；落败者不获开工许可 |
| F05 | 恢复输入缺失、LFS 对象不可取或 hash 不符 | 不执行；保留检查点，进入明确等待/错误状态，不宣称接管成功 |
| F06 | 没有与故障节点兼容的软件/硬件 | `WAIT_RESOURCE`；不降低验收、不把任务错误标 DONE |
| F07 | 权威 Git 远端不可达 | 暂停新认领、接管及终态接受；只隔离可重放的本地候选，恢复后重新校验 |
| F08 | 唯一协调器停止 | 调度暂停且不产生第二个权威 writer；不宣称控制平面高可用 |
| F09 | 动作具有不明结果的外部副作用 | `NEED_RECONCILIATION`；自动重复副作用次数为 0 |
| F10 | 第三台加入；两台竞争；路由目标不可用 | 仅兼容节点获派发；受影响 attempt 有界重派；最终屏障与验收不变 |

每次保存：代码/输入/环境版本、拓扑、触发点、Git 提交链、事件顺序、owner/generation/fence、命令和退出码、产物 hash、验收结果及人工干预。壁钟仅用于辅助计时，事件身份与状态版本用于判断因果。

验收建议：确定性 fixture 的重复接受数、被接受的过期结果数、已接受产物损坏数均为 0；在满足 H2 前提的 F01-F04/F10 运行中全部达到原验收。G1 每个可自动化故障点至少重复 20 次并记录随机种子；G2/G3 每种适用故障至少重复 3 次。小样本通过只是阶段门槛，不是故障概率为零的证明。

在测试前固定心跳间隔、租约/检测超时、扫描间隔、最大重派次数及总超时；首轮可使用 H=10s、L=60s、R=10s、每动作最多 2 次重派、10 分钟 fixture 截止作为可调实验参数，不是产品 SLA。耗尽预算后可诊断地停住，不能无限重试。测量检测延迟、恢复延迟、重做工作量、Git/产物同步量、无故障开销和人工干预次数；先与同一 CAH 单节点控制组比较，不从架构推断一定加速。

### 9. 接入与实施顺序

1. 先将本案作为现有分布式 vision 的准备章节合入；保留状态标签，不改 README 的已实现能力清单。
2. 建立独立模拟 fixture 与不变量检查，明确现有 schema 的字段映射和不支持项。
3. 实现 node registry、精确 runner 路由与开工身份校验；通过“实际机器和指定机器一致”的检查。
4. 实现检查点闭包、attempt 隔离、候选产物发布和幂等接受。
5. 实现独立监测、撤销旧代次与受限重派；审计 Git 发布助手的语义重验边界。
6. 执行 G1 故障矩阵；有设备后继续 G2/G3，保留失败记录。
7. 只有相应阶段通过后，才更新用户文档中的能力声明；后续再决定是否值得加入协调器高可用、动态迁移和大规模调度。

暂不填工期或成功概率。可以有较高设计信心，但必须把“机制可实现”“实现已完成”“协议模拟通过”“真实多机验证通过”分开记录。

### 10. 参考与证据边界

- 本仓库：`docs/CONTINUITY.md`、`docs/SCHEDULER_MODEL.md`、`docs/PARALLEL_STAGE1.md`、`docs/HOST_CAPABILITIES.md`、`harness/git_publish.py`、`local_bridge/scheduler.py`。源码引用固定到本案 source_commit，后续实施需重新核对。
- [Git push 官方文档](https://git-scm.com/docs/git-push)：远端 ref 更新及 fast-forward / 受约束更新的机制。Git 不替应用验证任务所有权。
- [GitHub self-hosted runners 官方文档](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners)：自托管执行环境的管理边界；本案不把 runner 注册视为环境与故障恢复已经验证。

本案是对既有 Git 交接架构的增量准备，不是宣称发明通用分布式系统，也不以其他项目是否采用相同设计判断其价值。价值最终由可恢复性、人工干预和总体成本的测量决定。
<!-- CAH_TRUSTED_MULTI_HOST_PREPARATION_V0_END -->
