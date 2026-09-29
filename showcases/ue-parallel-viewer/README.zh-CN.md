# Viewer／Cook 重跑案例：一个任务跨越多轮 AI 对话继续存活

[English](README.md) · [证据](evidence.json) · [重跑记录](records/2026-09-28-rerun-record.md) · [Viewer 客户端](viewer_client.py) · [操作说明](AGENT_VIEWER.md) · [下载与范围](DOWNLOADS.md)

这是 2026 年 9 月 28 日开始、持续到次日的 CAH Viewer/Cook 重跑案例的公开脱敏版本。

最终状态明确分成四段：

- **Phase 1：Fresh Cook + Fresh Viewer 联调 —— ACCEPTED**
- **Phase 2A：真实包／依赖关系侦察 —— ACCEPTED**
- **Phase 2B：原生 direct-preview 实现 —— STOPPED INCOMPLETE**
- **Phase 2C：最终 direct-preview 视觉验收 —— NOT REACHED**

Phase 2B 最后推进到 Worker **G32**。G32 已经出现 response-start，但没有形成 durable semantic result，用户随后明确终止继续投入。因此这里不会把未完成状态包装成成功。

## 这个案例真正验证的是什么

重点不只是“能不能 Cook 一个相机”或者“能不能让 Viewer 出图”。

更重要的问题是：

> **一个真实工程任务，能不能在 Planner / Worker 对话被替换、外部任务跨越模型回合、技术假设被推翻、Runner 出现堵塞、甚至 Harness 自身需要修复时，仍然保留已经验证的执行前沿并继续？**

这次运行里，真正持久的是 Git 中的任务状态，而不是某一个一直不死的聊天窗口。

```text
一次完整任务输入
→ 规划
→ 执行
→ 检查
→ 失败
→ 定位原因
→ 修改策略
→ 再执行
→ 验收
```

## Phase 1：不是 API 绿了就算完成

Parent Task 在 **11:25:48 JST** 正式开始。

Planner 首轮把 Fresh Cook 和 Fresh Viewer foundation 拆开推进。Cook 遇到 Windows 路径长度问题后，没有推倒重来，而是只把任务自有 UE 工作区迁到短路径后继续。

Viewer 更典型：包和 API 已经能工作时，真实 native Lit 画面仍然太暗，系统没有把“机械成功”当成最终成功，而是继续排查 shader、runtime、platform-file 和 packaged runtime 路径。

Phase 1 最终在 **16:27:18 JST** 正式验收，距离 Parent 开始 **5 小时 1 分 30 秒**。

验收事实包括：

- 外部 camera PAK 哈希保持不变；
- package 成功打开；
- preview 成功加载；
- 目标 camera mesh 正确选中；
- 交互测试通过；
- 4 张 native Lit 截图经过真实像素检查后才通过。

这些截图的精确大小和 SHA-256 都记录在 [evidence.json](evidence.json)。

一个限制也被保留下来：最终验收时背景实际是黑色，不是白色。模型本身清晰可读、下方视角无遮挡，因此 Phase 1 仍然通过；公开案例不会把这个事实改写掉。

## Phase 2A：证据推翻了更方便的解释

Phase 2 开始处理真实 package / dependency 问题。

最初 combined provider 能成功导出逻辑目标路径，看起来像是已经加载了目标 MOD。但 provenance 检查后来证明，默认 same-path 选择实际上命中了 base-game PatchPak。

于是“能按逻辑路径导出”不再被视为来源证明。

后续 archive-specific probe 直接从目标包成员加载到真实 `USkeletalMesh`，确认：

- **25 个 material slots**
- **26 个 morph targets**
- **1 个 LOD**

Phase 2A 在 **18:47:16 JST** 正式通过，距离 Parent 开始 **7 小时 21 分 28 秒**。

## 任务状态没有随着 Planner／Worker 换代丢失

Planner G1 在上下文积累过大后进行 handoff。Planner G2 不是靠旧聊天全文重新理解任务，而是从 Task、Plan、Memory、Plan Note、Child Reply、Result 和 evidence 继续。

Worker 也是同样逻辑：下一代从 durable frontier 继续，而不是因为换了聊天就重做已经验收的工作。

G20 是一个很清楚的时间例子：

- Worker semantic interval：**3 分 37 秒**
- 独立 external Build/Cook：**2 分 20 秒**
- Worker 已经 durable handoff 后，外部任务仍继续：**1 分 10 秒**

这说明“模型回合寿命”和“本机任务寿命”是可以分开的。

## Runner 故障与 Helper 恢复

后续一次 task-local execution wrapper 把主 Runner 堵了大约一小时。

之前准备的 backup capacity 在这里第一次真正发挥作用：主 Runner 仍被占用时，**连续三代 Worker** 继续由备用容量承接，让 Parent Task 没有因为单个 Runner 堵塞而直接终止。

Planner 随后把问题识别为 operational incident 并交给 Helper。

第一版 Helper 恢复语义还不够完整，因此这里发生了人工运维与 Harness 自身升级。升级后的 Helper 才完成精确恢复、durable incident/result 写入和控制权归还，后续 Worker 从原来的语义前沿继续。

所以这不是一个“全程无人干预、完美自动化”的宣传案例。

## Phase 2B：有进展，但没有假装完成

Phase 2B 继续进入 native package-store、cooked runtime 和 direct-preview 实现。

它产生了不少真实中间证据，但最终没有跨过 direct-preview 的完整验收线。

最后一代是 **G32**。出现 response-start 后，没有 durable semantic result。用户明确选择停止继续消耗时间。

因此正确的终态只有：

```text
P2-B = STOPPED_INCOMPLETE
```

## 公开实物

公开仓库包含实际使用的 Viewer Python 客户端：

[viewer_client.py](viewer_client.py)

以及经过修正的 [Viewer 操作说明](AGENT_VIEWER.md)。

完整 UE-linked Viewer 模块没有随仓库公开；具体边界见 [下载与范围](DOWNLOADS.md)。

## 结论

这个案例不证明 CAH 能无监督解决任意工程问题，也不证明任意 UE / 游戏 / IoStore 都能直接兼容。

它证明的是更窄、但更实际的一点：

> **一个真实工程任务可以活得比任何单个 AI 对话更久。只要已经验证的执行前沿被持久化到 Git，Planner、Worker、外部任务和恢复角色就可以跨越多轮替换继续向原始验收条件推进，而不需要用户每一步都重新说“继续”。**

Viewer 很重要。

时间线更重要。
