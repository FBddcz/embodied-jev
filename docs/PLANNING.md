# 逐步 XYZ 规划与可选相机输入

`incremental` 模式把每一步的运动方向、步长和夹爪操作交给模型。每轮读取当前观测与最近六步的实际结果，调用模型选择一个动作，检查并执行，再重新观测。程序没有规定“靠近—抓取—搬运—释放”的阶段顺序，也不会在模型失败后切换到规则策略。

本页说明实现和复现方法；真实模型运行与失败边界见[规划实验记录](PLANNING_RESULTS.md)。

## 控制与观测

| 配置 | 模型收到的内容 | 适合检验什么 |
| --- | --- | --- |
| `skills` | 结构化观测与预设技能选项 | 原有技能选择流程，技能内部路径由程序生成 |
| `incremental` + `privileged` | 仿真物体与目标坐标、机器人状态、最近动作结果 | 给定准确状态时的逐步动作规划 |
| `incremental` + `rgbd` | RGB-D 检测估计的物体与目标坐标、相机信息、机器人状态 | 感知误差下的结构化规划；没有把原始图像交给模型 |
| `incremental` + `vision` | 已启用相机的原始 RGB、各路标定信息、机器人本体与接触状态、最近动作结果 | 模型根据图像决定动作与重新规划 |

直接图像模式不提供物体真值坐标、目标真值坐标、场景配置、成功真值或预演出的未来位置。机器人本体与接触状态仍来自仿真传感器；最终成功判定由物理评测层完成。

`vision` 需要支持图像的 OpenAI 兼容或 Claude 原生模型。Jev、结构化本地 API、当前 MiniCPM 适配器可运行状态输入规划，收到图像时会明确报错。`baseline` 是显式选择的手写对照策略，需要物体与目标坐标，因此只用于 `privileged` 或 `rgbd`。

## 固定动作菜单

所有任务共享 21 个动作：世界坐标系 X、Y、Z 各方向的正负平移，每个方向提供 **40、10、2 mm** 三种步长，共 18 个；另有张开夹爪、闭合夹爪、保持位姿。平移保持已有夹爪命令。菜单不含自动对齐、自动抓取、物体坐标生成的航点或任务阶段。

工作区之外的短步不能被选择，且不会被悄悄截短。模型选择之后，执行层只检查该动作的安全性和可达性；拒绝原因进入下一轮历史，供模型重新选择。IK、关节控制与物理仿真由程序完成。即使只剩一个可选动作，模型策略仍会调用模型，记录真实调用次数、用量与延迟。

界面的候选项顺序可以打乱，动作 ID 和语义保持不变。手写基线把原有技能目标量化为同一组短步，仅用于对照，不参与模型选项排序。

## 图像如何进入模型

外部视角覆盖桌面与目标区域；腕部相机安装在机器人手部，随末端移动，用于近距离观察夹爪附近。启用的视角在同一个仿真时刻采集，每路都有独立标定，双相机发送顺序为 `external`、`wrist`。

| 相机配置 | `camera_views` / CLI `--cameras` | 行为 |
| --- | --- | --- |
| 仅外部 | `["external"]` / `external` | 只渲染外部视角 |
| 仅腕部 | `["wrist"]` / `wrist` | 只渲染随手部移动的视角 |
| 双相机 | `["external","wrist"]` / `both` | 同时采集两种视角 |
| 无相机 | `[]` / `none` | 不创建相机观测器，使用非视觉状态输入 |

`vision` 和 `rgbd` 至少需要一台相机；界面选择“无相机”会切换到仿真状态输入。`privileged` 可以同时开启相机预览，此时图像仅供查看，不发送给模型。省略配置时，视觉模式默认双相机，仿真状态模式默认无相机。RGB-D 检测只读取启用画面，双相机时优先使用外部检测，并由腕部补充被遮挡的物体；必需物体不可见且跟踪过期时仍会停止。

- OpenAI 兼容接口：用户消息先含结构化文本，再为每个视角添加 `Camera view: external/wrist` 标签和原生 `image_url` 块；像素通过 `data:image/png;base64,...` 发送。
- Claude 原生接口：同样先含结构化文本，再添加视角标签与原生 `image` 块，`source.type=base64`、`media_type=image/png`。

这是实际图像输入，不是把检测坐标包装成视觉描述。相机画面在动作与决策边界更新；等待 API 时仿真停留在本轮状态，面板显示的同一帧不会假装连续刷新。下一步执行完成后，模型会拿到新的同步画面。当前流程是按步采样，并非连续视频控制。

JSON 导出中的 `decision_inputs.action.images` 保存每路图像的 `sha256`、`byte_length`、`capture_id` 和 `view`，用于核对帧来源和顺序；同时保存实际发送的结构化文本。JSON 不含 PNG 字节、图像数据 URL 或 API 密钥。“下载观测图”另外提供包含 PNG 与清单的 ZIP，包括失败感知帧；它与当前实验 ID 绑定，可按哈希核对模型收到的像素。

模型必须返回一个有效 `choice`。它可附带各不超过 240 字符的 `intent` 和 `visual_evidence`，分别说明当前动作目的与可见依据。这些是供展示的简短摘要，不是思维链，也不能单独证明模型确实利用了图像。非法选项、无效结构、超长摘要和不完整响应会使本次调用失败，不会触发规则兜底。

## 运行示例

下列 `benchmark` 命令使用[环境变量连接](TECHNICAL_GUIDE.md#models)，从仓库根目录运行，每次生成汇总和逐回合 JSON。要复用界面保存的连接并保存完整相机 ZIP，使用后面的单回合脚本。

```bash
# 准确状态下的模型逐步规划
embodied-jev benchmark --provider chat --tasks transfer --seeds 0 \
  --control-mode incremental --observation-mode privileged \
  --max-cycles 60 --timeout 900 --output runs/plan-state.json

# RGB-D 检测坐标输入
embodied-jev benchmark --provider chat --tasks transfer --seeds 0 \
  --control-mode incremental --observation-mode rgbd \
  --max-cycles 60 --timeout 900 --output runs/plan-rgbd.json

# 双视角原始图像输入，同时打乱候选顺序
embodied-jev benchmark --provider chat --tasks transfer --seeds 0 \
  --control-mode incremental --observation-mode vision --cameras both --shuffle-candidates \
  --max-cycles 60 --timeout 900 --output runs/plan-vision.json

# 第 5 步之后把目标沿世界 X 方向移动 4 cm
embodied-jev benchmark --provider chat --tasks transfer --seeds 0 \
  --control-mode incremental --observation-mode vision \
  --intervention '{"kind":"target_shift","after_cycle":5,"delta_xy":[0.04,0]}' \
  --max-cycles 60 --timeout 900 --output runs/plan-target-shift.json

# 显式手写基线，对照准确状态下的短步执行
embodied-jev benchmark --provider baseline --tasks transfer --seeds 0 \
  --control-mode incremental --observation-mode privileged \
  --max-cycles 60 --output runs/plan-baseline.json
```

```bash
# 复用界面保存的连接：一回合最多 80 次真实 API 调用，拒绝覆盖已有目录
python scripts/planning_trial.py --saved-connection --cameras both \
  --shuffle-candidates --output runs/planning-dual

# 同一入口可选 --cameras external 或 wrist；无相机配 --observation privileged
```

脚本保存 `settings.json`（配置与源文件哈希）、`episode.json` 和 `cameras.zip`。超时或 API 错误作为失败保留，不自动重试。

扰动还支持 `object_shift`；`delta_xy` 的每个分量限于 ±0.06 m。扰动在指定动作边界执行并独立记录，模型通过之后的观测发现变化。要比较扰动恢复，应保持模型、任务、初始种子、控制模式和动作预算一致，分别运行有、无扰动条件。候选顺序打乱也应与原顺序成对比较。

## 展示规划能力需要哪些证据

一次成功的搬运只说明该回合完成。更有说服力的展示应同时给出：初始布局变化后是否完成、物体或目标移动后是否修正动作、动作被拒后是否采用有效替代，以及候选顺序打乱后表现是否稳定。可以从逐步记录中核对“当前帧—选择—实际位移或接触—下一帧”，并报告模型调用次数、总耗时、执行拒绝、动作预算耗尽和失败回合。

视觉贡献要与状态输入对照，并检查模型是否对图像中的变化作出正确反应；界面显示摄像头、调用了视觉模型或生成了合理摘要，都不足以证明视觉推理成功。当前实现是通用模型通过固定动作接口控制 MuJoCo 的实验平台，没有训练通用预训练 VLA，也没有据此证明真实机器人、任意物体或未知场景中的泛化能力。
