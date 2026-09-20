# GPT-6 Astra 预设技能实验

2026-09-20，通过已配置的 OpenAI 兼容 API，依次运行搬运入盘、方块堆叠、越障搬运三个任务，每个任务使用种子 0、1、2。**九局全部完成，每局 8 个动作、13 次模型调用。**

这组实验使用 **仿真真值状态＋预设技能**。模型根据位置、几何关系、夹爪和接触状态返回候选选择，随后由 MuJoCo 执行动作并提供新状态。全程使用 `chat` 适配器，没有切换为规则基线。原始图像输入、逐步 XYZ 控制的结果另见[视觉规划实验](PLANNING_RESULTS.md)。

![GPT-6 Astra 九局实验结果](results/gpt6-astra-2026-09-20.png)

## 结果

| 任务 | 成功 | 每局平均用时 | 单次决策平均耗时 |
| --- | --- | --- | --- |
| 搬运入盘 | 3/3 | 41.60 秒 | 2.77 秒 |
| 方块堆叠 | 3/3 | 43.84 秒 | 2.90 秒 |
| 越障搬运 | 3/3 | 42.06 秒 | 2.77 秒 |

九局共执行 72 个动作、调用模型 117 次。API 返回的累计用量为 **74,003 输入 tokens、1,818 输出 tokens**。单次决策平均 2.81 秒，中位数 2.61 秒，P95 为 4.23 秒；每局实际用时为 38.69–47.20 秒，平均 42.50 秒。九局实验计时合计 382.49 秒，不含局间等待和单独的连接检查。

所有导出记录帧均未出现禁止接触标志；这项统计检查的是记录帧，不是内部物理子步。各局最大抬升为 0.1975–0.1993 米，最终结果满足仿真的支撑、位置、稳定性和夹爪条件。

## 运行设置

| 项目 | 实验设置 |
| --- | --- |
| 代码提交 | [`7461356`](https://github.com/FBddcz/embodied-jev/tree/746135693c94a4f87e5d4632e16aec5bef487d21) |
| 请求模型 / 服务返回模型 | `gpt-6-astra` / `gpt-6-astra-2026-09-03` |
| 接口与输出 | OpenAI 兼容 Chat Completions，JSON 候选选择 |
| 提示版本 | `compact-effects-v4` |
| 任务 / 种子 | `transfer`、`stack`、`barrier`；各 0、1、2 |
| 预演 / 概率门槛 | 开启预演；门槛 0 |
| 每局动作预算 / 超时 | 12 个动作；600 秒 |
| 运行方式 | 依次运行，网页服务 `speed=4` |
| 采样参数 | 未额外设置 temperature、top_p 或 API seed，使用服务默认值 |

模型名称来自所选 API 的响应，未独立验证服务端权重。聊天适配器读取模型生成的选择，不提供原生候选概率。代码仍负责阶段筛选、候选动作、逆运动学和成功判定。

这是三个固定任务上的小样本开发实验。旧 MiniCPM 测试使用不同执行节奏和样本数量，不能据此作严格速度排名；TypeSafe Jev 和 Claude 还没有在本项目中完成同设置的真实 API 对照。

## 数据与复现

[公开 JSON 报告](results/gpt6-astra-2026-09-20.json)列出九局状态、调用与用量、逐次延迟、场景哈希和完整轨迹文件。`episodes[].episode_file` 指向对应的 `.json.gz`，解压后是原始实验导出。报告同时记录原始文件和压缩文件的 SHA256；未发布 Key、API 地址、账户信息或逐调用用量估算。

启动工作台，在页面保存 `gpt-6-astra` 的 OpenAI 兼容连接后，可用当前仓库的脚本重复同样的九局设置：

```bash
python scripts/benchmark_server.py --model gpt-6-astra --output runs/gpt6-server
```

脚本通过本机 8090 服务使用已保存的连接，保留 `speed=4`，不读取 Key。它拒绝覆盖已有输出目录；遇到 API 错误、超时或手动干预时停止后续实验。运行会产生真实 API 调用。

也可设置相应环境变量后使用原有 CLI：

```bash
embodied-jev benchmark --provider chat --threshold 0 \
  --tasks transfer stack barrier --seeds 0 1 2 --max-cycles 12 \
  --timeout 600 --output runs/gpt6-astra.json
```

这条命令复用任务与决策设置，但 CLI 不包含网页 `speed=4` 的展示等待，整局用时不能与本表直接比较。云端请求受网络和服务状态影响，重跑结果也可能变化。运行前请确认模型权限和 API 额度。

图表可直接从公开报告重新生成，无需 Key：

```bash
python -m pip install -e '.[plots]'
python scripts/plot_experiments.py --report docs/results/gpt6-astra-2026-09-20.json
```

下载图表：[SVG](results/gpt6-astra-2026-09-20.svg) · [PDF](results/gpt6-astra-2026-09-20.pdf)。数据表：[逐局指标](results/gpt6-astra-2026-09-20.csv) · [逐次调用耗时](results/gpt6-astra-2026-09-20-latencies.csv)。
