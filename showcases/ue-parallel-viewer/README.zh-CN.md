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

### P1 实际验收截图

下面四张就是 2026-09-28 rerun 中由 Planner 接受的实际 native Lit 截图，不是旧图替代品，也不是生成图。

**Perspective**

![P1 验收 Perspective Lit](images/phase1-perspective-lit.png)

**Front**

![P1 验收 Front Lit](images/phase1-front-lit.png)

**Right**

![P1 验收 Right Lit](images/phase1-right-lit.png)

**Below**

![P1 验收 Below Lit](images/phase1-below-lit.png)

上面展示的就是 [evidence.json](evidence.json) 里记录 byte size 和 SHA-256 的那四个原始文件。

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

# Part 2 — 强外部干扰下的 Recovery 压力测试

Part 2 是在 Part 1 已经正式成功之后才开始的。

它最有价值的结果并不是“又多完成了一个 Viewer 功能”，而是把系统推到了另一个问题上：

> **当 Worker 在正常任务执行过程中，自己生成了足以堵死执行系统的错误代码；或者网页执行面本身长时间运行后失去响应时，CAH 能不能把任务救回来？**

Part 1 已经证明：当失败仍然局限在正常任务和 replanning 范围内时，CAH 可以连续数小时、多工作线稳定推进并最终验收。

Part 2 则开始撞上 **系统级 recovery failure**。

## Part 2A — 普通技术调查仍然运转良好

Part 2A 仍然属于正常 evidence-driven engineering。

combined provider 最初可以导出目标逻辑路径，但 provenance 检查后来证明，默认 same-path 选择命中了 base-game PatchPak，而不是已经证明目标 MOD 来源。

后续 archive-specific loading 直接证明指定 package member 内存在真实 `USkeletalMesh`：

- **25 个 material slots**
- **26 个 morph targets**
- **1 个 LOD**

Part 2A 在 **18:47:16 JST** 正式通过，距离 Parent 开始 **7 小时 21 分 28 秒**。

这一阶段仍然和 P1 类似：假设可以错，Worker 可以换代，只要错误没有摧毁执行基础设施，任务就能继续。

## G9 — Worker 自己写出的错误开始堵死系统

Recovery 故事真正从 G9 开始。

**21:19:20 JST**，G9 到达 Worker response-start。

它在 **21:24:07** 启动一个外部 staged runtime operation，并在 **21:25:42** 完成自己的 durable handoff。

问题出在 Worker 自己生成的 task-local execution wrapper：它同步嵌套启动 Viewer，没有外层 watchdog / timeout，也没有可靠的 finally-style cleanup。

结果不再是简单的“这次实验失败”。

这个外部 run 从：

**21:24:07 → 22:24:45 JST，共 1 小时 00 分 38 秒**

持续堵住主 Runner。

这里的区别非常重要：

```text
普通任务失败
    → Worker 记录失败
    → Planner / successor 再做下一步

G9 这种作死性失败
    → Worker 生成的执行代码堵住共享执行资源
    → 正常 continuation 本身都开始受阻
```

G10 在 **21:26:37** 就已经创建，但直到 **22:13:13** 才真正 response-start，中间存在 **46 分 36 秒** 的 admission / transport 延迟，而且那时 G9B 还没有释放 Runner。

这直接暴露了当时 CAH 的一个薄弱点：

**语义状态能保住，并不等于物理执行通路还能恢复。**

## Hotfix 让任务重新动起来

任务没有被整体重开。

同一个 Parent Task 在运行中经历了一系列 recovery hotfix。

G10 最终在 **22:58:18** 写出 durable result；semantic-sync hot upgrade 随后在 **23:18:11** 把这个已经存在的结果正确收敛到 canonical finalize，而没有重新跑 Worker。

接着 G11 又暴露一轮 lifecycle delay：

**23:31:42 → 00:01:31 JST，共 29 分 49 秒**

这次修的是 Worker retention / binding 路径，同一个 dispatch 最终恢复，而不是创建一个新的语义任务。

从这里开始，P2 已经不只是 Viewer 开发，也是在现场调 CAH 的恢复系统。

## G12 — 同类危险再次出现，但冗余开始发挥作用

后续 corrected hybrid-mode experiment 在 **00:21:01 JST** 启动，又一次卡了约一小时：

**00:21:01 → 01:21:40，共 1 小时 00 分 39 秒**

这次不同的是：backup capacity 已经存在。

主 Runner 被占住期间，连续三代 Worker 仍然能够在备用容量上推进：

- **G13：00:26:13 → 00:31:45**
- **G14：00:32:43 → 00:39:25**
- **G15：00:40:41 → 00:45:43**

这第一次真实证明：Runner 冗余不是“理论上有备用”，而是真的能在主执行资源堵死时维持 Task Cell 的生命。

## Helper 的 Recovery 职能当时仍然太弱

**00:47:30 JST**，Planner 审核继承下来的 blockage，并正确把它识别成 operational incident，而不是再发明一个新的语义实验。

Helper 在 **00:51:26** response-start。

但第一版 Helper 行为仍然偏“诊断”：

它能看出发生了什么，却不能完整完成：

```text
必要 mutation
→ canonical closure
→ durable incident/result
→ return control
```

于是这里发生了第二次人工运维干预。

运行中的 Helper contract 被加强成更主动的 recovery owner。恢复后的 Helper 完成了精确清理、durable incident/result 写入和控制权归还。

原 Planner 在 **02:00:32** 再次 response-start，后续 Worker 从原来的 semantic frontier 继续。

这正是 P2 最有价值的地方之一：它不是把系统问题藏起来，而是把 Helper 在真实事故下的能力缺口直接暴露出来。

## Recovery 之后，系统重新恢复了真实工作能力

Hotfix 之后不是只“状态变绿”。

后面的真实语义和本机任务又继续跑起来了。

G20 是一个很清楚的样本：

- Worker response-start：**02:45:31**
- independent Build/Cook 启动：**02:47:58**
- Worker durable handoff：**02:49:08**
- 外部任务 terminal success：**02:50:18**

Worker semantic interval 是 **3 分 37 秒**。

外部 Build/Cook 共 **2 分 20 秒**，其中 **1 分 10 秒发生在 Worker 已经 handoff 之后**。

这说明前面的 hotfix 至少恢复了足够的 lifecycle 连续性，使任务真的重新进入了正常执行状态。

## G32 — 第二种 Recovery Failure：网页本身长时间运行后不再产出结果

后面又撞上了另一类完全不同的问题。

G32 在 **2026-09-29 05:14:51 JST** 被请求。

**05:15:11**，运行时已经观察到 exact response-start / ACK。

也就是说，从 CAH 的视角看，这个 Worker 已经“开始响应”。

但这次没有像 G9 那样出现一个还在运行的外部任务。

真正卡住的是 **ChatGPT 的物理网页对话本身**。

在 **07:36:47 JST** 对 exact G32 页面做直接检查时——距离 response-start 已经超过 **2 小时 21 分**——观察到：

- exact G32 页面仍然存在；
- **assistant message 数量 = 0**；
- 页面仍显示 **Stop** 控件，表现为仍在 busy / running；
- Worker Reply 仍为空；
- Result 仍为空。

到 **07:41:16 JST**，canonical backend 仍然是 `RUNNING`。

因此 G32 暴露的是第二种 recovery failure：

```text
G9：
Worker 写错执行代码
→ 外部执行 / Runner 被堵死

G32：
单个 ChatGPT 网页运行过久
→ 页面一直 busy / 无响应
→ response-start 已存在
→ 但永远没有可消费的 semantic output
```

这里之后用户明确停止了 P2-B。

这个 stop 没有丢掉已经验收的成果：P1 和 P2A 都继续保持 accepted。停止的是一个尚未解决的 recovery 边界。

## P2 真正证明了什么

所以 P2 不应该描述成：

“Viewer 做到 G32 还是没做完。”

更准确的是：

**P2 是一次 Recovery Stress Test。**

它发现了当前 CAH 至少两个很具体的恢复边界：

1. Worker 自己生成的外部执行可以反过来堵住共享执行基础设施；
2. ChatGPT 网页这个物理执行面本身，也可能在长时间运行后卡死在“已经 response-start、但没有 semantic output”的状态。

第一类问题在运行中已经通过 backup Runner、semantic-sync recovery、lifecycle 修复、Helper contract 加强等方式得到了一部分缓解。

第二类问题说明：**物理网页会话本身也必须被当成可丢弃、可重建的资源，而不能因为 response-start 已经发生就假设最终一定会有输出。**

## 当前正在加强的方向

这次 Showcase 直接推动了现在的 Recovery 工作。

当前正在继续调试和加强的包括：

- Worker / Planner physical conversation watchdog；
- physical failure 时做 **same-generation retry**，而不是错误地增加 semantic generation；
- 对死掉的页面做确定性的 stop / delete / reinject original wake；
- 更清晰地区分 semantic failure 和 transport / physical failure；
- 把 Helper 从“会诊断事故”继续强化成真正可以执行 bounded recovery mutation、完成 durable closure、再把控制权交还正确角色的 semantic recovery owner；
- 继续强化冗余执行资源，避免一个外部任务堵塞就冻结整个 Parent Task。

这些能力**还在调试和验证中**，不能把 P2 写成“现在已经全部解决”。

目前更准确的结论是：

> **P1 已经充分证明了：在没有 G9 这种系统级外部强干扰时，CAH 可以稳定地长时间、多工作线运行并完成实际验收。P2 暴露出的下一阶段核心问题，不是普通任务规划能力，而是系统在被外部强干扰、执行资源被堵塞、或者网页执行面自身失效之后的恢复力。**

这已经成为 CAH 当前最重要的工程方向之一。

## 终态

```text
Part 1 / Phase 1   ACCEPTED

Part 2A            ACCEPTED INVESTIGATION
Part 2B            STOPPED AT RECOVERY LIMIT
Part 2C            NOT REACHED
```

P2 的停止不削弱 P1 的成功结论。

它只是把下一条真正需要修的系统边界暴露出来了。

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
