# 🧪 从演示走向系统评测

任务多样性采用两条路线：自建场景用于定位抓取、放置、扰动和感知问题；标准 benchmark 提供更多任务与固定评测规则。把标准任务重搭成“相似场景”，不能得到可比较的 benchmark 成绩。

## 已接通的链路

`任务清单 → 独立仿真进程 → 观测 → 策略 → 原生动作接口 → 官方成功条件 → 逐步轨迹与汇总`

新增命令是 `embodied-jev evaluate`。已有 `serve`、`benchmark`、工作台和三个控制模式保持原有入口。外部模拟器使用单独 Python 进程；主项目仍使用 MuJoCo 3.13.0，Meta-World 3.1.1 使用其要求的 MuJoCo 3.3.0。

| 路线 | 适合测试什么 | 当前范围 |
| --- | --- | --- |
| 内置 Panda 场景 | 抓取／放置故障、短步与技能对照、感知与扰动 | 已接通；开发种子 0–2，预留测试种子 100–102 |
| [Meta-World](https://github.com/Farama-Foundation/Metaworld) | 到达、推物、抓放、开抽屉、开门等操控能力 | 已跑通 5 个 MT1 任务 × 3 种子；自选子集，不是完整 MT10／ML10 成绩 |
| [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO) | 语言任务、空间／物体／目标变化；后续与 VLA 对照 | RGB-D 视觉对照已真实运行：纯 GPT-6 2/2、GPT-6 + Jev 1/2（先发布三局成功录像）；两个开发任务 |
| [ManiSkill](https://github.com/haosulab/ManiSkill) / [RoboCasa](https://github.com/robocasa/robocasa) | 更多物体、机器人、场景与长任务 | 后续扩展；当前未接入 |

本轮本地接口验证：内置规则基线 **9/9**，Meta-World 官方脚本策略 **15/15**，均为 **0 次模型调用**。Meta-World 使用官方 `info.success` 判定；这些结果只证明任务、执行与报告链路可用，不证明 Jev 能完成这些任务。外部环境的机器人也不再是当前工作台里的 Panda。

## 运行

在项目环境运行内置回归：

```bash
embodied-jev evaluate --manifest benchmarks/builtin-dev.json \
  --policy baseline --max-steps 30 --output runs/builtin-dev-baseline
```

安装隔离的 Meta-World 环境，再从项目环境发起评测：

```bash
python3.11 -m venv .venv-metaworld
.venv-metaworld/bin/python -m pip install -r benchmarks/requirements-metaworld.txt

embodied-jev evaluate --manifest benchmarks/metaworld-smoke.json \
  --worker-python .venv-metaworld/bin/python --policy scripted \
  --max-steps 500 --output runs/metaworld-scripted
```

`scripted` 调用 Meta-World 自带的专家策略，`noop` 用于不动机械臂的安装检查。两者都不调用模型。工作台的规则策略使用 `baseline`，不冒充外部任务专家。

将外部评测的策略改为 `jev`、`chat` 或 `claude`。默认使用环境变量；也可通过 `--connection-source saved` 复用工作台保存的连接，Key 不进入清单、报告或命令参数：

```bash
# 先在终端环境配置 TYPESAFE_API_KEY；不需要把 Key 放进命令或清单。
embodied-jev evaluate --manifest benchmarks/metaworld-smoke.json \
  --worker-python .venv-metaworld/bin/python --policy jev \
  --max-steps 200 --max-calls 100 --timeout 300 \
  --output runs/metaworld-jev
```

预算按每局计算；上例最多 15 × 100 次 Jev 请求。建议先用小清单检查连接。没有自动重试或回退策略；已有输出目录会被拒绝，失败不能用同目录成功重跑覆盖。读取已保存连接需要操作系统凭据存储可用；失败时不会改写原连接。

外部模型每次请求回答 XYZ 与夹爪四个问题，执行归一化动作后重新观察；它与内置“分层 XYZ”是不同策略，报告分开标识。`--action-repeat N` 控制一次决策持续几个环境步，每步仍检查成功和终止，结束后丢弃剩余动作。Meta-World 使用其原生四维动作；LIBERO 输出七维 OSC 动作，其中旋转暂固定为零。当前模型评测只开放结构化状态；worker 的图像／本体观测是后续策略接入的基础，尚不是可运行的 VLA。`--control-mode` 只用于内置任务。

## Jev 与 GPT 的同条件对照

`metaworld-compare.json` 固定 reach、push、pick-place 三个任务与种子 0、1，共每模型 6 局。这是开发子集，不能代表完整 Meta-World 成绩。以下协议在运行前固定：200 环境步、40 次模型请求、300 秒、新决策最多重复 5 步、动作幅度 0.5、概率门槛 0。每局重置模型连接与动作历史。两组均使用同样的结构化状态，不加视觉或规则纠偏。

```bash
embodied-jev evaluate --manifest benchmarks/metaworld-compare.json \
  --worker-python .venv-metaworld/bin/python --policy jev --connection-source saved \
  --max-steps 200 --max-calls 40 --timeout 300 --action-repeat 5 \
  --action-scale 0.5 --threshold 0 --output runs/metaworld-compare-jev

embodied-jev evaluate --manifest benchmarks/metaworld-compare.json \
  --worker-python .venv-metaworld/bin/python --policy chat --connection-source saved \
  --max-steps 200 --max-calls 40 --timeout 300 --action-repeat 5 \
  --action-scale 0.5 --threshold 0 --output runs/metaworld-compare-chat

python -m embodied_jev.evaluation_charts \
  --reports runs/metaworld-compare-jev/summary.json runs/metaworld-compare-chat/summary.json \
  --output runs/metaworld-comparison-figures
```

`chat` 使用保存的模型名，不在脚本里假定模型身份；报告分别保存配置名与供应商实际返回名。绘图需要项目的 `plots` 可选依赖。两组上限合计 480 次请求，运行错误会停止该组剩余案例；超时后不执行迟到动作，已发出的请求仍可能等到接口超时才返回。不要在看过结果后只替换失败局；改协议时另存完整批次。

| 指标 | 口径 |
| --- | --- |
| 任务成功率 | 官方 `info.success`；展示成功／已评分／计划局数、分任务结果和 Wilson 95% 区间 |
| 输入／输出 token | 供应商返回的用量；分别标注覆盖请求数，缺报为 N/A，部分用量另存为已报告小计 |
| API 延迟 | 客户端测得的 HTTP 请求耗时，含网络与服务端等待；展示 p50／p95 和请求数 |
| 整局耗时 | 从 worker 启动到关闭的实际秒数；另存环境初始化与 rollout 耗时 |
| 调用与步数 | 决策调用次数、实际 HTTP 请求数和原生环境步数；同时展示预算耗尽／接口异常 |
| 仿真时间 | 原生步数除以环境实际控制频率；不等于模型推理速度或动图播放速度 |

图表使用同一任务与种子的配对记录，检查观测、预算、动作、代码指纹和初态是否一致。部分运行会明确标注覆盖不足；无真实调用记录时不生成模型对比图。失败不删除，未返回的 token 不画成零；不把 Jev 的候选概率称为准确率，也不将不同供应商的 token 数直接解释为费用。小样本只适合发现问题，不下排行榜或显著性结论。

## LIBERO 状态接口与视觉对照

标准套件和 LIBERO-PRO 不能混用。当前清单选择原版 `libero_spatial`，明确指定 `task_id`、`init_index` 和随机种子；无效的初态索引直接报错，不循环取模。独立 worker 使用官方 `set_init_state`、`step` 和 `check_success()`，不把目标谓词或成功标记发给策略。

准备好独立的官方 LIBERO 环境和任务资产后，用其 Python 做安装检查：

```bash
embodied-jev evaluate --manifest benchmarks/libero-spatial-smoke.json \
  --worker-python /path/to/libero-env/bin/python --policy noop \
  --max-steps 10 --output runs/libero-install-check
```

上述 `evaluate` 为状态接口。新增 **`libero-compare`** 提供双相机 RGB-D、三维航点、XYZ／旋转／夹爪选择、固定资产下载和配对初态检查，已在原版 LIBERO 的关抽屉与关微波炉任务真实运行：纯 GPT-6 **2/2**、GPT-6 + Jev **1/2**（先发布三局成功录像）（每局 1200 秒）。它是开发子集的系统对照，不是完整套件成绩或 VLA 排名。[安装与运行](LIBERO_VISION.md) · [结果与费用](results/libero-vision/RESULTS.md)。使用独立环境，不能向主项目覆盖安装。

## 报告怎么看

`summary.json` 冻结任务清单、配置与控制代码指纹；每个外部案例保存独立 worker 日志和逐步 JSONL，内置任务保存原有完整 episode。报告包含计划／已记录／未评分／缺失局数、各任务成功次数、Wilson 95% 区间、宏平均成功率、模型调用和延迟，以及失败原因。安装异常不伪装成物理失败；推理异常、超时、低置信度和预算耗尽保留为已尝试失败。缺局或运行错误会让批次标为未完整完成。

小样本区间不能代表未知任务泛化，混合任务的汇总区间也不能替代按任务结果。比较时使用相同任务／初态、观测权限、动作尺度与频率、调用／步骤预算和成功条件。内置技能的一步与外部环境的一步不等价。

开发清单用于分析和调参；测试清单留到版本冻结后再使用。测试种子变化只检验同任务新布局，不等于新任务或新物体泛化。原始图像、检测坐标和仿真真值分成不同实验组，规则基线与模型结果始终分开。

关于参考仓库、RSI、VLA 和世界模型的接入边界，见 [扩展路线](INTEGRATION_ROADMAP.md)。
