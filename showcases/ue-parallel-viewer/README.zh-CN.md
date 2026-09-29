# Viewer／Cook Showcase：先是成功案例，再是后续问题发掘

[English](README.md) · [证据](evidence.json) · [重跑记录](records/2026-09-28-rerun-record.md) · [Viewer 客户端](viewer_client.py) · [操作说明](AGENT_VIEWER.md) · [下载与范围](DOWNLOADS.md)

这个 Showcase 最适合拆成 **两个独立 Part** 来看。

**Part 1 是一个完整成功案例。**  
一个持续数小时的 managed task，被拆成多条工作线推进，经历真实故障、反复 Worker 换代、执行与验收循环，最后以 Fresh Cook + Native Viewer 的真实结果正式通过。

**Part 2 是成功之后继续往更难问题推进的后续发掘案例。**  
在 Part 1 已经成功的基础上，继续把 Viewer 推向真实 package / direct-preview 问题，结果暴露出 provenance 误判、更深的 Unreal runtime 边界、执行 wrapper 故障、Runner 堵塞，以及 Helper 恢复机制本身的问题。部分侦察成功，后续实现最终主动停止。

所以，**P2 的未完成不应该反过来稀释 P1 的成功。**

---

# Part 1 — 长时间、多工作线、最终成功的完整案例

## 目标

从 fresh baseline 重新建立 camera Cook 和 Viewer 流程，并以 **真实 native Viewer 输出**作为最终验收依据，而不是只看 API 或进程是否成功。

Parent Task 在 **2026-09-28 11:25:48 JST** 正式进入 canonical 状态。

Planner 第一轮就把任务拆成两条可以独立推进的工作线：

| 工作线 | 目标 |
| --- | --- |
| Fresh camera Cook | 从真实 Blender 源重新建立 Unreal import/build/Cook 输出 |
| Fresh Viewer foundation | 独立重建 Viewer 基线，并准备后续联调所需的交互和检查能力 |

之后两条线再汇合到第三条联调线：

| 联调线 | 目标 |
| --- | --- |
| Exact Cook → Fresh Viewer | 用真实 Cook 输出打开目标模型/材质，完成可用 native 视图、交互与最终视觉验收 |

所以这不是“一个模型回合执行一个命令”，而是一个持续很久的 managed task：**多条任务线并行／交替推进，最终在明确 join condition 上汇合。**

## 过程并不顺

Fresh Cook 首先遇到了 Windows 路径长度问题。

系统没有把已经接受的 Blender 源丢掉，也没有整套重来，只把任务自有 UE 工作目录迁到更短的位置，然后继续。

Viewer 这边更典型：机械成功早于视觉成功。

当时已经能做到：

- package 打开；
- API 返回成功；
- 目标 asset 被发现；
- native capture 被请求；

但真实 Lit 画面仍然太暗，无法作为可用 Viewer 验收结果。

这个结果被**直接拒绝**，而不是因为“接口绿了”就宣布成功。

之后继续限定范围地排查：

- stage/background；
- shader-library 行为；
- packaged runtime；
- platform-file routing；
- runtime shader discovery。

多条看起来合理的解释都被真实证据推翻。

真正的循环是：

```text
执行
→ 看真实结果
→ 拒绝假成功
→ 缩小问题范围
→ 修复
→ 再执行
→ 再验收
```

## 成功边界

Phase 1 在 **16:27:18 JST** 正式通过。

从 Parent 开始到最终验收共：

**5 小时 1 分 30 秒**

最终验收包括：

- 从 accepted source fresh 重建出的 Cook；
- external camera PAK 字节保持不变；
- package 成功打开；
- preview 成功加载；
- 目标 camera mesh 正确选中；
- source shader library 正确打开；
- interaction acceptance 通过；
- 4 张 native Lit 视图经过真实像素检查后通过。

外部 camera PAK 保持同一 SHA-256：

```text
e0486f3cbab4048ce7d76260d1e42f2d34177e64152f4c70dd7877825e191305
```

4 张 native capture 的精确大小与 SHA-256 记录在 [evidence.json](evidence.json)。

一个视觉限制也原样保留：最终验收时背景是黑色，不是白色。模型本身清晰可读、below 视角无遮挡，因此 Phase 1 的实际验收条件满足。公开案例没有为了更漂亮而重写事实。

## 为什么 Part 1 可以单独成为成功 Showcase

Part 1 已经构成一个完整闭环：

```text
一次完整任务输入
    |
    +-- 工作线 A：Fresh Cook
    |
    +-- 工作线 B：Fresh Viewer
    |
    +-- 联调线：Exact Cook → Viewer
              |
              +-- 多轮诊断／修复
              |
              v
         实际结果通过验收
```

用户不需要在每一个 bounded failure 后重新手工输入“继续”。

已经验收的工作被保留；失败假设转化成下一轮决策的证据；整个 Parent Task 始终围绕原始 acceptance criteria 推进，直到 native Viewer 真正通过。

**Part 1 到这里结束。它本身就是一个成功案例。**

---

# Part 2 — 成功之后继续推进，开始发掘更深的问题

Part 2 是在 Part 1 已经正式成功之后才开始的。

新的目标更难：能不能把已经通过的 Viewer，从已知可工作的 camera fixture，进一步推向真实 package / direct-preview 场景，并处理更复杂的 provenance 和 IoStore/runtime 行为？

从这里开始，这个 Showcase 更像一个 **problem-discovery case**，而不是“第二个成功演示”。

## Part 2A — 第一个方便的解释被证据推翻

combined provider 最初可以成功导出目标逻辑路径。

直觉上很容易把它解释成：

“目标 MOD 已经被正确读取。”

但 provenance 检查后来发现并不是这样。

同一路径存在多个候选，默认 provider 实际选中了 base-game PatchPak。

于是：

```text
逻辑路径成功加载
!=
已经证明来源就是目标 MOD
```

系统没有保留那个更方便的解释。

后续 archive-specific loading 直接从指定 package member 加载到真实 `USkeletalMesh`，确认：

- **25 个 material slots**
- **26 个 morph targets**
- **1 个 LOD**

这个 reconnaissance 边界在 **18:47:16 JST** 正式通过，距离 Parent 开始 **7 小时 21 分 28 秒**。

所以 Part 2A 的成功，不是“最终 Viewer direct-preview 已经完成”，而是成功发现并修正了一个真正会影响后续实现的来源假设。

## Planner 换代本身也成为实验的一部分

随着 Phase 1 + Phase 2 历史越来越大，Planner G1 最终 handoff。

Planner G2 并不是依赖旧聊天全文重新理解任务，而是从 durable surfaces 继续：

- Task；
- Plan；
- Planner Memory；
- Plan Note；
- Child Reply；
- Result；
- Evidence。

这证明后续调查并没有因为 Planner 对话被替换而重启。

## 外部任务寿命和 Worker 回合寿命分离

G20 提供了一个很清楚的时间样本：

- Worker semantic interval：**3 分 37 秒**
- external Build/Cook：**2 分 20 秒**
- Worker 已经 durable handoff 后，外部任务继续运行：**1 分 10 秒**

Worker 不需要为了“盯着机器跑”一直活着。

只要外部 operation 的身份与前沿已经 durable，successor 就能继续检查同一个 operation。

## Part 2B — 往更深处推以后，开始暴露基础设施问题

native direct-preview 实现继续推进到真实 package-store / cooked-runtime 层，但这时暴露出来的问题已经不只属于 Viewer 本身。

一次 task-local execution wrapper 把主 Runner 堵了大约一小时。

此前准备的 backup capacity 在这里第一次得到真实验证：主 Runner 仍然堵塞时，**连续三代 Worker** 由备用容量承接，Parent Task 没有因为单 Runner 故障直接死亡。

Planner 随后把这个问题识别为 operational incident，并交给 Helper。

第一版 Helper 仍然不够：它能诊断，但没有完整完成 recovery / closure / return-control。

于是这里发生了人工运维和 Harness 自身升级。Helper contract 被加强后，同一个 Parent Task 才从原 semantic frontier 继续。

所以 Part 2 同时做了两件事：

1. 继续调查更难的 Viewer/runtime 问题；
2. 把 CAH 自己 recovery 体系里的薄弱点也暴露出来。

## 后续没有完成，不应该伪装成完成

Part 2B 继续推进了很多 Worker generation，也积累了真实 native runtime 证据，但始终没有跨过最终 direct-preview acceptance boundary。

最后 admitted 的 Worker 是 **G32**。

它出现了 physical response-start，但没有 durable semantic result。用户随后明确停止继续投入。

因此终态应明确写成：

```text
Part 1 / Phase 1   ACCEPTED

Part 2A            ACCEPTED INVESTIGATION
Part 2B            STOPPED INCOMPLETE
Part 2C            NOT REACHED
```

这不是“Part 1 最后失败了”。

而是：

**先完成了一个真实成功基线，再把成功系统继续往更难的方向推，结果发现了新的技术问题和 Harness 问题，最后在一个未完成边界主动停止。**

---

## 公开实物

公开仓库包含 Viewer 控制面的实际 Python 客户端：

[viewer_client.py](viewer_client.py)

以及修正后的 [Viewer 操作说明](AGENT_VIEWER.md)。

2026-09-28 的 4 张 Phase-1 native capture 在 [evidence.json](evidence.json) 中保留精确 byte size 和 SHA-256。

完整 UE-linked Viewer module 不随仓库公开；范围见 [下载与范围](DOWNLOADS.md)。

---

## 两个 Part 分别说明什么

### Part 1

> **CAH 能够让一个持续数小时、包含多条工作线的真实工程任务，经历多轮执行、验证和修复，最终到达真实 acceptance condition。**

### Part 2

> **在已经成功的 baseline 上继续往更难问题推进时，同一套 durable task 模型可以继续发现错误假设、runtime 边界和 Harness 自身的薄弱点，同时不抹掉已经验收的成功结果。**

Part 1 展示的是“把困难任务做成”。

Part 2 展示的是“成功之后继续往前推，开始看见下一层真正的问题”。
