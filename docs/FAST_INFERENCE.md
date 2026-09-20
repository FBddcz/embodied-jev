# Jev 快速推理与开源实现

Jev 机械臂演示的速度，来自状态整理、有限候选选择、合并请求和连续运动控制。模型通常在动作之间做决策，物理引擎和画面则按各自的频率更新。

以下核对于 **2026-09-20**。TypeSafe Jev 通过 API 提供服务；这里参考的机器人集成项目是开源的，Jev 权重并未公开。

## 官方接口

Jev 接收 `state + questions`，返回 `answers`。程序给出候选，模型输出选择和概率，省去长篇文本生成。

| 能力 | 文档说明 | 机械臂中的用途 |
| --- | --- | --- |
| `Choice` | 从给定候选中选择，返回各项概率 | 选择阶段、移动方向、夹爪命令 |
| `Noul` | 返回是/否问题的概率 | 判断语义条件，精确接触仍由代码检测 |
| `Score` | 在描述的等级间评分 | 表达偏好，精确距离仍由代码计算 |
| 多问题请求 | 同一状态读入一次，各问题并行评估 | 一次询问 XYZ 与夹爪等独立问题 |

官方将训练方法称为 RLCD，目标包括结构化决策和概率校准。公开接口没有给出足以确认网络结构、参数规模或推理内核的资料。概率校准描述多次预测的统计行为，也不保证单次动作正确。

来源：[System One](https://docs.typesafe.ai/concepts/system-one)、[API](https://docs.typesafe.ai/api)、[模型与状态复用](https://docs.typesafe.ai/models)、[训练目标](https://docs.typesafe.ai/introduction/machine-learning-primer)。

## 开源机械臂项目怎么用

### 先选意图，再合并动作问题

[openroboto-ai/jev-robot-control](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_policy.py) 先请求 `intent`，再把选中的意图写入状态，第二次请求同时询问 **X、Y、Z、gripper**。两阶段有依赖，仍需串行；合并四个动作通道省去了逐轴请求。

它的 Jev 分支调用 OpenRouter 实验性 decisions 接口，模型为 `typesafe/jev-1.13`。普通语言模型走另一个分支。使用这组地址时需要 OpenRouter 的相应权限，不能填进 TypeSafe 官方入口。

### 代码计算、预演，模型选择

[FazalAAli/jev-robotics-demo](https://github.com/FazalAAli/jev-robotics-demo/blob/531de61a75f386b0847f5f6f809a11424a75c29b/jev_agent.py) 先计算距离与方向，提出小动作，在 MuJoCo 副本中预演；剔除不可达、碰倒物体或掉落的动作后，将预测结果写进候选描述。只有一个合法候选时直接执行。

到达路点后，它还会合并“下一目标”“是否完成”和可选的“抓取/松开”问题。源码分别累计模型等待、预演和执行耗时，便于判断哪部分最慢。这里的状态提取、候选生成、碰撞检查和运动控制均由代码承担。

### 画面频率与模型频率分开

openroboto 的 [物理循环](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_env.py) 步长为 `0.002 s`，约每 16 步输出一帧，即每仿真秒 500 步、31.25 帧。[运行脚本](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_run.py) 则在每轮依次请求意图、动作，再执行。整局实际用时还包含模型等待，不能从帧率推算推理速度。

行知也采用这种分工。比较时需要分别看模型耗时、调用次数、仿真时间和整局实际用时。

## 三栏演示是怎样播放的

openroboto 的 “Same task. Different decisions.” 页面同时展示 Jev 1.13、GPT-6 Astra、GPT-4.1 mini，并标注 **RECORDED EXECUTION**。核对时仓库 `main` 为上文固定的 `7a4ed8b`。

[三栏服务器](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_triple_app.py) 读取已有 JSON、图片和 MP4；[前端](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_triple.html) 按同一仿真时间轴播放。点击 Play 不会发起模型请求，播放也跳过了 API 等待。页面上的 wall time 来自完整运行记录，包含这些等待。

其[结果说明](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/docs/RESULTS.md)列出：

| 控制器 | 仿真时间 | 实际用时，含 API 等待 | 该次结果 |
| --- | --- | --- | --- |
| Jev 1.13 | 36.16 秒 | 181.847 秒 | 放置成功 |
| GPT-6 Astra | 33.92 秒 | 707.274 秒 | 放置成功 |
| GPT-4.1 mini | 51.20 秒 | 704.253 秒 | 达到 160 轮上限 |

[数据清单](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_triple_data.py)显示，Jev 与 GPT-6 来自 `20260919-200239-715974-0`，mini 来自较早的 `20260919-193012-198478-0`。加载器检查相同初始观察、物理源码哈希、seed 0 和 160 轮上限，并记录文件来源及哈希。动作选项和指令相同，但参数及超时不同：mini 使用 35 秒 HTTP 超时，较新的配对使用 90 秒。每个模型仅一段 seed-0 记录，还不足以比较通用成功率。

仓库也能运行真实实验：[双模型配对脚本](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_pair.py) 用两个工作线程并发调用，[复现命令](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/reproduce.py) 的 `--controller all` 则依次运行三组。它们与只读回放是不同入口。

行知借鉴了并排展示，但将实时运行和回放分开标注。还需注意，原演示的 Jev 概率来自接口，两种 GPT 的数值来自模型生成的 JSON，含义并不相同。以上结论来自公开源码和记录，本机没有重跑该项目的模型实验或轨迹验证器。

## MiniCPM 如何直接给候选打分

本地路径参考 [SemIf direct.py](https://github.com/TheoLeeCJ/SemIf/blob/ca3ba65f142967030ecb453346e94d6f476a69df/src/semif_phase1/direct.py)：

```text
状态 + 问题 + A/B/C 候选
          ↓ 一次前向计算
读取最后位置对应 A/B/C 的 logits
          ↓ 候选之间做 softmax
得到评分并选择
```

行知只投影候选字母对应的输出权重，减少完整词表的输出计算，并检查字母在完整提示词边界处确实是单个 token。整个输入仍要经过模型，长状态和重复历史仍会增加耗时。

这种方式得到候选之间的相对评分，尚未在我们的机械臂任务上校准。它使用 MiniCPM 权重，训练方法和服务端实现都与官方 Jev 不同。界面展示候选评分与选择，不展示隐藏思考过程。

## M2 上的实测与输入优化

早期基准为本项目提交 [`72beb40`](https://github.com/FBddcz/embodied-jev/tree/72beb407d17fa1c0d072ad472ddc258717a1a628)，提示版本 `phase-conditions-v2`。当时每轮最多调用两次模型，发送完整观察与最近三次完整结果，使用 `use_cache=False`，没有跨问题前缀复用。

M2 / 16 GB、Torch 2.6.0、Transformers 4.57.6 的 FP16 开发实验中，加载加一次预热约 **18.75–29.35 秒**，阈值为 0 时单次决策约 **1.13–5.57 秒**。后台负载不固定，这些数字仅描述当时运行。完整数据见 [验证记录](VALIDATION.md) 和 [测量汇总](results/minicpm-fp16-2026-09-20.json)。

当前预设技能实现包含两项优化：

- 输入改为紧凑几何、接触关系和最近两次动作结果；v4 加入阶段实际规划的位移与夹爪命令。
- HTTP 适配器使用 `httpx.Client` 复用连接，并记录失败请求的调用与延迟。

紧凑输入实验仍为 **0/3 成功**，模型会重复接近而没有完成抓取。减少输入或提前停止后的较短用时，不能作为控制质量或整体加速结论。HTTP 连接复用可减少握手开销，但服务端计算、排队与网络仍需实测，见 [HTTPX 说明](https://www.python-httpx.org/advanced/clients/)。

## 接下来值得测试的方向

### 状态与候选描述

精确几何计算交给代码，模型输入保留位置、相对位移、对齐关系、夹爪和接触事实，并说明单位与阈值。历史重点记录“执行了什么、发生了什么变化”。测试候选顺序变化，避免仅因排在前面而被选中。

这也符合 [Jev 1.13 已知局限](https://docs.typesafe.ai/model-jaggedness/jev-1.13) 的建议：由代码完成算术，过滤无关状态，减少复杂间接推理。

### 合并同一状态下的独立问题

动作依赖阶段选择时，仍需先选阶段；也可以为各阶段预先准备动作问题，收到结果后采用选中阶段的答案。官方称后一种方式为 [speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)，应同时比较额外 token、预演成本和延迟。

官方各题独立评估，问题名不会进入模型，含义需要写在 `instructions` 和候选描述中。普通聊天 API 返回 JSON 不代表它采用同样的并行评估机制。

### 前缀缓存与量化

[SemIf shared.py](https://github.com/TheoLeeCJ/SemIf/blob/ca3ba65f142967030ecb453346e94d6f476a69df/src/semif_phase1/shared.py) 先计算同一状态的 KV 前缀，再复制缓存、并行处理问题后缀，同时检查完整 token 前缀、后缀位置和 padding。机器人移动后状态已改变，只能复用确认未变的前缀；缓存还会占额外内存。单独打开 `use_cache` 而不复用返回值不会带来跨请求收益。

MiniCPM 官方提供 [MLX](https://huggingface.co/openbmb/MiniCPM5-2B-MLX) 和 [GGUF](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF) 量化权重。接入后需要与 FP16 比较候选概率、动作选择、延迟和整局成功率，速度数字应来自同一模型与设备。

## 速度测试怎么记录

先在固定状态上短测，再运行完整任务。建议记录：

- 代码、提示词、实际模型版本、权重修订、精度、设备和推理库版本。
- 完整状态、问题、候选、输入 token 数，以及单阶段/两阶段/合并请求设置。
- 冷启动、单次选择、整轮决策、预演、执行和整局用时；报告样本数、中位数、P95，保留失败与超时。
- GPU 同步和计时边界；MLX 需显式求值，API 端到端时间包含网络。
- 合法输出率、任务成功率、重复动作、停止原因、调用数和 token；费用按真实计费依据计算。

官方 [13 题示例](https://docs.typesafe.ai/cookbooks/parallel_questions)中，合并请求约 **0.27 秒**，13 次串行请求共 **2.71 秒**。它使用 `jev-1.12`、约 5.4 万字符的 GDPR 文档，每种方式重复 5 次。这是多问题合并的示例，不能直接套用为机械臂或本机 MiniCPM 的速度；若把串行请求改成客户端并发，比较结果也会变化。
