# 模型对比

在相同任务、起点和动作预算下，观察 MiniCPM、Jev、GPT、Claude 等模型如何选择。当前已公开 [GPT-6 Astra 的九局真实 API 实验](GPT6_EXPERIMENT.md)；Jev、Claude 等模型的同设置对照还待补充。云端模型需要自行配置权限。

## 在网页里并排运行

1. 在 **模型连接** 中保存接口并测试调用，或在 **扩展** 中保存多套具名连接。模型 ID 改变后需要重新验证。
2. 打开 **模型对比**，选择 2–3 路模型，设置共同任务、种子、控制方式、观察来源、相机组合、预演、概率门槛和动作预算。直接图像比较需选支持图像的连接。可选同一 API 的不同模型，也可使用不同平台的连接。
3. 选择 **依次运行**或**并行运行**，点击开始。默认依次运行，并行最多两路。
4. 查看每路场景、阶段、动作、调用次数、token 和耗时。可以统一暂停、继续、停止，最后导出完整记录。
5. 暂停或完成后，用共同时间轴按已流逝的仿真时间回放。提前结束的模型会停在最后一帧。

没有 Key 时可先用两路规则基线检查操作流程。模型调用失败会记录错误；有候选概率的模型低于门槛时记为 `uncertain`，结束该路实验并继续其他模型。

每路都有独立物理世界。并行时，先返回的模型可以先执行，不需要逐步等待其他模型。MiniCPM 共用同一个模型实例和推理锁，因此本地推理仍是串行的。比较延迟时建议依次运行，并记录冷启动、硬件和后台负载。

回放只读取本次运行的记录，不再调用模型。它按仿真时间对齐，排除了 API 等待造成的画面停顿；真实等待时间仍在实验记录中。当前支持服务进程内的姿态回放，尚无导入历史文件的入口。openroboto 的三栏页面也采用录制回放，源码说明见 [快速推理分析](FAST_INFERENCE.md)。

场景预设应用到对比后，各路使用相同的 `scene_config` 和 `user_context`，导出中保留详细配置。要比较不同场景或输入，请分别创建实验并保存导出。用法见 [扩展指南](EXTENDING.md)。

## 命令行接入

| 模型路线 | provider | 配置 |
| --- | --- | --- |
| MiniCPM5-2B FP16 | `minicpm` | `EMBODIED_MINICPM=1`，本地权重，MPS/CUDA |
| GPT / 平台提供的 Claude | `chat` | `EMBODIED_API_BASE`、`EMBODIED_API_MODEL`、`EMBODIED_API_KEY` |
| Claude 原生 | `claude` | `EMBODIED_CLAUDE_BASE`、`EMBODIED_CLAUDE_MODEL`、`ANTHROPIC_API_KEY` |
| TypeSafe Jev | `jev` | `TYPESAFE_API_KEY`、`TYPESAFE_MODEL` |

模型 ID 使用账号或平台实际开放的名称，例如已公开实验使用的 `gpt-6-astra`。中转平台可能另用带厂商前缀的 ID。这里填写模型名称，不填写开发工具名称。

先设置对应环境变量，再运行以下命令。`benchmark` 不读取网页服务保存的钥匙串配置；不要把 Key 写进命令行参数、仓库或报告。

```bash
# MiniCPM：关闭尚未校准的概率门槛
embodied-jev benchmark --provider minicpm --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/minicpm.json

# 云端模型：先配置 API，并留意批量调用费用
embodied-jev benchmark --provider chat --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/chat.json

embodied-jev benchmark --provider claude --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/claude.json

# Jev：固定为账号已开放的版本
TYPESAFE_MODEL=jev-1.13.0 embodied-jev benchmark --provider jev --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/jev.json
```

每次评测生成汇总文件和各局的完整 JSON。当前没有自动费用上限；页面也不会把未知价格显示为零，可按导出的用量和服务商当日价格计算。

## 怎样让结果可比较

- 固定代码、提示词版本、任务、种子、场景哈希、动作预算与预演开关。
- 固定控制方式、观察来源、相机组合与扰动设置。预设技能与逐步 XYZ、真值坐标与原始图像分组报告；不能直接混成一张模型排名。
- 使用相同的输入信息与动作菜单。预设技能的目标由代码生成；逐步模式由模型选短步，两者都依赖程序提供的运动控制和安全检查。
- 主比较将概率门槛设为 0。聊天与 Claude 接口没有原生候选概率，使用不同门槛会改变停止条件。
- 一并记录成功、失败、停滞、超时、动作数、调用数、输入/输出 token、延迟和完整轨迹。
- 分开报告冷启动和预热后延迟；API 时间包含网络，本地时间受设备与后台负载影响。
- FP16、MLX 4-bit、GGUF Q4/Q8 分组记录，量化方式和提示模板都可能影响选择。费用按实际计费规则计算。

三个任务的少量种子适合开发检查。正式对比还需增加未参与调参的种子和场景，冻结提示词，并公开全部失败。不同接口的输出方式也应写进报告：MiniCPM 读取候选 logits，chat 生成 JSON，Claude 原生返回工具参数。
