# 🧪 比较 MiniCPM、GPT 和 Claude

同一个机器人、同一组任务，换不同模型做决策。当前批量评测入口支持所有已实现的 provider；需要自己配置实际模型与 API 权限。**接口支持不等于已有模型排名。**

## 先选接入方式

| 模型路线 | provider | 配置 |
| --- | --- | --- |
| MiniCPM5-2B FP16 | `minicpm` | `EMBODIED_MINICPM=1`，本地权重，MPS/CUDA |
| GPT / 平台提供的 Claude | `chat` | `EMBODIED_API_BASE`、`EMBODIED_API_MODEL`、`EMBODIED_API_KEY` |
| Claude 原生 | `claude` | `EMBODIED_CLAUDE_BASE`、`EMBODIED_CLAUDE_MODEL`、`ANTHROPIC_API_KEY` |
| TypeSafe Jev | `jev` | `TYPESAFE_API_KEY`、`TYPESAFE_MODEL` |

[OpenAI 官方模型页](https://developers.openai.com/api/docs/models/gpt-6-astra)列出 `gpt-6-astra`；[Anthropic 模型页](https://platform.claude.com/docs/en/models/overview)列出 `claude-fable-5-1`。中转平台可能使用带厂商前缀的 ID，以实际平台为准。Claude Code 和 Codex 是工具，不作为这里的模型名称。

## 运行可复现的对照

先设置对应环境变量，再执行。API Key 不要写进命令行参数、仓库或实验报告。

```bash
# 本地 MiniCPM：显式关闭未经校准的概率门槛
embodied-jev benchmark --provider minicpm --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/minicpm.json

# 云端模型：执行前确认自己的 API 配置及费用预算
embodied-jev benchmark --provider chat --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/chat.json

embodied-jev benchmark --provider claude --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/claude.json

# Official TypeSafe Jev: set TYPESAFE_API_KEY and pin an available model version
TYPESAFE_MODEL=jev-1.13.0 embodied-jev benchmark --provider jev --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 30 \
  --output runs/jev.json
```

每个实验会产生汇总文件和完整 episode 文件。桌面表单中的 Key 只保存在网页服务进程里；单独启动 CLI 评测需要环境变量。云端批量实验会产生费用，目前没有自动费用上限。

## 比什么，怎么比

- 固定代码版本、任务、种子、动作预算、预演开关、场景哈希和提示词版本。
- 使用同样的几何/接触观察和候选动作。阶段条件由代码提供；评估的是这一受约束系统中的模型选择能力。
- 主比较将概率门槛设为 0。聊天/Claude 接口没有原生候选概率，不能拿它们和有概率门槛的 MiniCPM 做不加说明的成功率比较。
- 记录成功、失败、停滞、超时、动作数、模型调用数、输入/输出 token、决策延迟和完整轨迹。
- 将本地冷启动与预热后决策延迟分开。云端延迟包含网络开销；本地硬件和后台负载要写明。
- 云端费用按运行时服务商价格计算，缓存和推理 token 计费规则另行说明。本地推理没有 API 账单，但有硬件与能耗成本。
- 分开记录 FP16、MLX 4-bit、GGUF Q4/Q8，量化后端与提示模板变化也可能影响结果。

三个任务的少量种子只能作为开发检查。正式对比应增加未用于调参的种子与场景，冻结提示词，对全部模型使用同样条件，并公开失败。不同接口的输出约束也不同：MiniCPM 读取候选 logits，chat 生成 JSON，Claude 原生生成工具参数；该差异应随报告说明。
