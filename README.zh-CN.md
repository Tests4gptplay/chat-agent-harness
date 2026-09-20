# Chat Agent Harness（CAH）

**Harness AI with Git.** 用 Git 把聊天中的任务变成可执行、可恢复、有成果记录的工作。

[English](README.md) · [Windows 安装](docs/INSTALL_WINDOWS.md) · [相机案例](showcases/camera/README.md) · [自更新案例](showcases/self-update/README.md)

CAH 连接前台对话、Git 任务状态、本地 Bridge、浏览器扩展和自托管 runner。模型负责判断和编写操作，runner 调用本地工具，Git 保存任务、检查点、执行证据和最终成果。Task Cell 承担任务级规划和监督；Worker 是可替换的执行容量，二者不是同一个生命周期。

简单任务能直接做就直接做，需要执行则优先复用一个 Worker；真正有独立工作值得并行时再使用多线程。Skill Registry、技能检索、基于证据的晋升与积累机制随代码发布，但不包含作者的私人技能库。

![相机最终渲染](showcases/camera/final.png)

相机案例保留五次迭代图片、程序化脚本和清理过本机路径的 `.blend`，同时说明过程中发生的执行失败与恢复。相机上 GAH 77 是改名前留下的原始模型文字，并非发布时遗漏修改的产品名称。

自更新案例记录了 CAH 通过现有 runner 更新自身代码、构建扩展、重启 Bridge、原生重载插件，并实际核验 1.0.4 的过程。没有把局部基准提速冒充整个任务提速；一次更新后委派任务从唤醒提交到结果提交为 98 秒。

**这是实验性版本。请先建立自己的私人运行库，再按安装说明部署，不要将个人电脑 runner 注册到这个公共发布库。** 没有能可靠终止所有子进程的一键紧急制动；通用自适应调度和自动模型选择尚未完成。首次第三方机器的完整部署还未经过独立实地验证。

代码采用 [AGPL](LICENSE)，具体版本选择见 [NOTICE](NOTICE)；另有经作者同意的[商业许可](COMMERCIAL_LICENSE.md)。发布内容不包含作者的对话绑定、账户配置、原始运行日志、私人导入 Skill、无关项目和私有开发提交历史。
