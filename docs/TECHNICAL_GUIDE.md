# 技术说明

行知用 MuJoCo 模拟 Franka Panda。模型可以选择预设技能，也可以根据每轮观察选择 XYZ 短步和夹爪动作。观察来源包括仿真状态、RGB-D 检测坐标和直接相机图像。浏览器负责显示与控制，Python 服务负责物理、模型请求和实验记录。安装命令见 [README](../README.md#-零机器人基础快速上手)，多模型实验见 [对比指南](COMPARISON.md)。

安装后，规则基线不需要 GPU、Key 或模型下载；构建后的页面也不依赖外部字体或 CDN。同一服务的多个浏览器标签页共享单实验和模型对比的状态。

<a id="models"></a>

## 模型接入与配置保存

点击 **决策模型** 旁的插头图标，填写接口地址、模型 ID 和 Key。保存不调用模型；**测试调用**会发送一次小请求，**运行实验**会连续请求决策，云端费用由服务商计收。

通过 `embodied-jev serve` 启动时，Key 存入系统钥匙串，服务重启后会恢复已保存的连接。URL、模型 ID、配置名称等信息单独保存在仓库外：

| 系统 | 连接信息目录 |
| --- | --- |
| macOS | `~/Library/Application Support/EmbodiedJev` |
| Windows | `%LOCALAPPDATA%/EmbodiedJev` |
| Linux | `$XDG_CONFIG_HOME/embodied-jev`，未设置时使用 `~/.config/embodied-jev` |

系统钥匙串不可用或拒绝访问时，页面会显示“仅本次会话”；这时刷新页面仍保留配置，但重启服务后本次修改不会恢复。程序不会把 Key 改存到明文文件。密码保存后不回填，也不进入浏览器存储、场景预设、实验导出或日志。更换接口地址后，留空密码不会沿用旧地址的 Key。

也可在启动前设置环境变量，字段见 [`.env.example`](../.env.example)；程序不会自动读取 `.env`。成功恢复的已保存连接优先于同一接口类型的环境变量。单独运行 `benchmark` 时仍需环境变量，不会自动使用网页保存的连接。

若只想临时使用配置，可运行 `embodied-jev serve --memory-only`，或设置 `EMBODIED_JEV_PERSISTENCE=memory`。这会跳过系统存储，Key 随服务退出而清除。

保存、未验证、验证通过和失败会分开显示。修改配置会使上次验证失效；测试期间改了配置，旧测试结果会丢弃。连接测试和独立输入测试最多同时执行两个请求。模型错误会停止对应实验，保留失败记录。

### OpenAI 兼容 API

选择 **OpenAI 兼容 API**，填写平台给出的 Base URL、模型 ID 和 Key。地址通常以 `/v1` 结尾，程序补上 `/chat/completions`。支持兼容此协议的云端平台和本地服务，实际可用模型以所选服务为准。

适配器把状态与候选放进 `messages`，要求返回 `{"choice":"candidate_id"}`，并检查选择是否属于当前候选。若平台不接受 `response_format`，可关闭 JSON 模式，但返回内容仍须是有效 JSON。聊天接口不提供原生候选概率，因此不使用模型自报概率，也不应用概率门槛。

直接视觉规划还会用 `image_url` 内容块发送本步相机 PNG。逐步规划的响应允许附上 `intent` 和 `visual_evidence`，各最多 240 字符，供界面展示动作意图与可见依据。这些是简短公开说明，不是模型内部推理过程；是否判断正确仍需看实际执行结果。

```bash
export EMBODIED_API_BASE=https://your-provider.example/v1
export EMBODIED_API_MODEL=your-model-id
read -s EMBODIED_API_KEY
export EMBODIED_API_KEY
embodied-jev serve --port 8090
```

### MiniCPM5-2B

```bash
python -m pip install -e '.[minicpm]'
export EMBODIED_MINICPM=1
export EMBODIED_DEVICE=auto
embodied-jev warmup
embodied-jev serve --port 8090
```

选择 **MiniCPM5-2B** 后，服务会加载 `openbmb/MiniCPM5-2B`，固定权重版本为 `12a3808a956f869c767195e9266b59c4d21d92e2`。首次下载约 5 GB，运行还需额外内存。`auto` 依次尝试 CUDA、Apple MPS、CPU；MPS/CUDA 使用 FP16，CPU 使用 FP32。Apple Silicon 可设置 `EMBODIED_DEVICE=mps` 明确指定 GPU。

`warmup` 加载模型并做一次真实的双候选决策，然后退出。网页服务会从下载缓存加载自己的模型实例；同一服务内重置实验会复用权重。页面显示加载、就绪和错误状态。缓存完整后可用 `HF_HUB_OFFLINE=1` 禁止模型下载请求。

```bash
EMBODIED_MINICPM=1 EMBODIED_DEVICE=mps embodied-jev benchmark \
  --provider minicpm --seeds 0 1 2 --threshold 0.55 \
  --timeout 600 --output runs/benchmark-minicpm.json
```

评测保存汇总和每个任务/种子的完整记录，包括实际调用、权重版本、设备、延迟、概率与物理结果。低于门槛时，批量实验记为 `uncertain` 并结束该局；`--threshold 0` 可关闭门槛。

本地适配器使用非思考聊天模板，读取候选字母的下一 token logits，再只对这些候选做 softmax。它检查完整提示词的 token 边界，上下文上限为 4096 tokens。候选概率表示相对偏好，尚未校准为动作成功率；目前三个任务的真实推理仍未成功，见 [实测记录](VALIDATION.md)。量化权重已有 MLX/GGUF 版本，但本项目尚未接入这些后端。

### Claude 原生 Messages API

选择 **Claude 原生 API**，默认地址是 `https://api.anthropic.com/v1`，程序补上 `/messages`。模型 ID 默认 `claude-fable-5-1`，需确认账号或平台已开放该模型。

```bash
export EMBODIED_CLAUDE_BASE=https://api.anthropic.com/v1
export EMBODIED_CLAUDE_MODEL=claude-fable-5-1
read -s ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY
embodied-jev serve --port 8090
```

请求使用 `x-api-key`、`anthropic-version: 2023-06-01` 和 `max_tokens: 1024`，通过 `select_action` 工具的枚举参数约束候选。返回值只能选择已有动作，不会执行模型生成的代码。截断、拒绝、未知候选、格式错误或多个工具调用都会终止该次决策；记录中保留模型名、用量与延迟。

直接视觉模式使用原生 `image` 内容块。逐步规划的工具参数同样允许返回简短的 `intent` 和 `visual_evidence`。当前 Jev、结构化决策服务和本地 MiniCPM 适配器只接状态输入。

参见 [Messages API](https://platform.claude.com/docs/en/api/messages) 和 [模型列表](https://platform.claude.com/docs/en/models/overview)。Anthropic 的 [OpenAI SDK 兼容层](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk) 有参数限制，例如忽略 `response_format`；使用中转服务时请按它实际提供的协议选择入口。

### TypeSafe Jev

```bash
read -s TYPESAFE_API_KEY
export TYPESAFE_API_KEY
export TYPESAFE_MODEL=jev-latest
embodied-jev serve --port 8090
```

选择 **TypeSafe Jev** 后，请求发送到 `https://api.typesafe.ai/v1/systemone`。需要获准访问的 TypeSafe Key；做对照实验时，可将 `jev-latest` 改成账号支持的固定版本。协议见 [官方 API 文档](https://docs.typesafe.ai/api)。

### 结构化决策服务

```bash
export EMBODIED_LOCAL_URL=http://127.0.0.1:8078/v1/systemone
export EMBODIED_LOCAL_MODEL=minicpm-jev
# 需要鉴权时设置 EMBODIED_LOCAL_KEY
embodied-jev serve --port 8090
```

服务需接受 `{model, state, questions: {action: {type: "choice", instructions, criteria}}}`，返回 `{model, answers: {action: {choice, probabilities}}, usage}`。每个候选都必须有概率，数值有限且总和约为 1，所选项应具有最高概率。

[openroboto 的适配器](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_policy.py) 还使用 OpenRouter 的实验性地址 `https://openrouter.ai/api/alpha/decisions`、模型 `typesafe/jev-1.13`。在行知中可通过 **Jev / 结构化决策 API** 填写这组完整地址和有访问权限的 OpenRouter Key。普通聊天接口权限不代表能访问该路由，本项目尚未实测这项服务。

## 一轮决策如何执行

**预设技能模式（`skills`）**先根据物体、目标和接触信息筛选可行阶段，再让模型选阶段与动作。技能目标由程序生成；只有一个阶段时直接采用，并记为无需模型调用。若开启预演，程序先在仿真副本中检查候选，模型从剩余动作中选择。输入保留最近两次动作结果。

**逐步模式（`incremental`）**每轮提供同一个 21 项动作菜单：XYZ 各轴正负方向的 40、10、2 mm 位移，以及张爪、闭爪、保持。模型根据当前观察与最近六次实际结果选一步，不经过任务阶段筛选。选中后才做范围和安全检查；被拒绝的动作及原因进入反馈，程序不自动换动作。完整流程见[逐步规划](PLANNING.md)。

两种模式都用阻尼最小二乘 IK 和关节执行器完成动作，再读取新状态。有原生候选概率的接口会检查概率门槛；Chat / Claude 没有这类概率，不使用模型自报的概率。

物理步长为 0.002 秒，即每仿真秒 500 步；姿态记录约每仿真秒 25 帧。相机在决策与动作边界采样，模型调用另有自己的等待时间。这三个频率分开计算。完整输入、候选和执行前后状态都保存在历史中。

连续三次同阶段动作几乎没有位姿变化，且接触与夹爪证据不变时，实验会以 `stalled` 停止。逐步模式还要求三次选择的是同一个动作。系统保留结果，不替模型选择下一步，也不自动切换为规则基线。

抓取依靠双侧手指与自由物体的实际接触。成功条件包括目标支撑接触、XY 误差小于 25 mm、速度低于 25 mm/s 并稳定至少 0.4 秒、夹爪打开，以及末端高度至少 170 mm。堆叠任务的支撑块固定在桌面上。碰撞监测目前覆盖部分末端连杆/手指与桌面、障碍的接触，尚不完整。

当前末端朝向固定，模型不能自行生成关节代码。仿真状态与 RGB-D 模式向模型提供坐标；直接视觉模式提供所选相机的原始 RGB、标定和机器人自身反馈，不提供方块或目标的真值坐标。后者用于逐步控制，不经过依赖坐标的技能生成。相机配置见[视觉说明](VISION.md)。未知物体、任意任务和真机控制仍需扩展；快速推理的来源与局限见[源码分析](FAST_INFERENCE.md)。

### 修改候选动作

预设技能在 `planning.py` 中定义：`eligible_phases()` 筛选阶段，`candidates()` 生成目标坐标、夹爪指令和时长。逐步动作菜单与输入整理在 `incremental.py`，执行循环和选中后的安全检查在 `runtime.py`。

| 修改内容 | 代码位置 |
| --- | --- |
| 阶段名称与中文显示 | `PHASES` 和前端阶段映射 |
| 给模型的阶段解释 | `PHASE_GUIDANCE`，保留对应位移和夹爪信息 |
| 目标、时长或新动作 | `candidates()`，同时检查预演、执行和接触反馈 |
| 新阶段 | 阶段筛选、候选生成和前端映射都需更新 |
| 逐步位移幅度或夹爪选项 | `incremental.py` 的固定菜单，以及 `runtime.py` 中的执行与拒绝反馈 |

“输入测试”可以编辑测试候选，但实验台的可执行动作仍由代码定义。选项可用中文；语言、顺序和候选集合变化都可能影响模型，需要记录版本并重测。`direct` 与 `gentle` 目前使用相同终点、不同执行时长。

## 日志、暂停与并发

每局保留最多 200 条运行事件，页面显示最近 12 条，导出包含保留的全部事件。需要文件日志时：

```bash
embodied-jev serve --port 8090 --log-file runs/server.jsonl
```

JSONL 日志在 2 MB 时轮转，保留三份备份。日志只记录约定字段，不记录请求正文、Key 或原始服务商错误。轮询访问日志已关闭；页面空闲或隐藏时会降低轮询频率。

控制请求携带实验 ID。旧标签页控制已被重置的实验会收到 HTTP 409；重复开始不会覆盖正在执行的单步。停止或重置后，即使旧模型请求才返回，也不会执行其动作。HTTP 请求本身可能仍要等到返回或超时。对比实验也有独立 ID 和相同的过期请求检查。

## 扩展与开发

**扩展**页可管理具名模型配置、场景预设和独立输入测试。预设也可用于 `benchmark --preset file.json`，详细用法见 [扩展指南](EXTENDING.md)。补充上下文与仿真观测分开保存，不替换位置、接触或成功条件。

相关接口包括 `GET/POST /api/model-profiles`、`POST /api/presets/validate` 和 `POST /api/decision/probe`。实验通过 `profile_id` 选择连接，provider 必须匹配。每局使用创建时的连接副本；之后修改配置不会改变已运行实验的地址和 Key。

```bash
python -m pip install -e '.[test]'
pytest -q
embodied-jev benchmark --output runs/benchmark.json
# UI 测试使用独立的 8099 端口，先构建前端
npm run build
npx playwright install chromium
npm run test:ui
# 后端已运行在 8090 时，启动前端热更新
npm run dev
```

源码位于 `src/embodied_jev/` 和 `frontend/`，测试位于 `tests/` 和 `tests-ui/`。`npm run build` 将网页资源打包进 Python 包，构建 wheel 前也需执行。测试结果和已知失败见 [验证记录](VALIDATION.md)。

参考来源见 [参考映射](REFERENCES.md)。原创代码采用 MIT；Panda 模型与网格保留 Apache-2.0 许可，详见 [第三方声明](../THIRD_PARTY_NOTICES.md)。模型权重单独下载，适用各自许可。
