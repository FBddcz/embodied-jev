<div align="center">

# 🤖 EmbodiedJev · 行知

### 把具身 AI 实验，搬到你的浏览器里。

**📦 开箱体验三大仿真任务 · 🧠 本地小模型 / 云端 API · 🆚 多模型同场对比**

无需机械臂，无需先训练模型。跟着步骤启动，在自己的电脑上体验「观察 → 决策 → 执行 → 反馈」。

[![Checks](https://github.com/FBddcz/embodied-jev/actions/workflows/ci.yml/badge.svg)](https://github.com/FBddcz/embodied-jev/actions)
[![Stars](https://img.shields.io/github/stars/FBddcz/embodied-jev?style=flat&color=f4b942)](https://github.com/FBddcz/embodied-jev/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-5c7cfa.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MuJoCo](https://img.shields.io/badge/Physics-MuJoCo-08a88a)](https://mujoco.org/)

[🚀 快速上手](#-零机器人基础快速上手) · [🧠 接入模型](#-给机械臂接上模型) · [📊 实测结果](docs/VALIDATION.md) · [🤝 一起维护](#-一起把行知做得更好)

![行知具身决策工作台](docs/workbench-desktop.png)

**行而有据，知而能行。**

如果这个项目帮你迈出了具身 AI 的第一步，欢迎点一颗 **⭐ Star**，让更多人一起玩、一起改！

</div>

## ✨ 打开行知，你能做什么？

- 📦 **先玩起来，再接模型**：内置规则基线，无需 API Key、GPU 或模型权重，即可运行三个任务。首次安装依赖需要联网。
- 🦾 **真实物理交互**：MuJoCo + Franka Panda，夹爪接触、抓取、搬运和放置都有物理反馈。
- 🧠 **模型入口放在界面里**：可接 TypeSafe Jev、Claude 原生 API、OpenAI 兼容 API，以及本地 MiniCPM5-2B。
- 👀 **决策过程看得见**：分开展示“做什么”和“怎么做”，查看阶段选择、动作概率、调用次数与延迟。
- 🆚 **同一任务，不同模型**：可选 2–3 路独立实验，统一任务和种子，真实运行后按仿真时间对齐回放。
- 🧩 **按自己的想法扩展**：多套具名模型接口、任务/场景预设、JSON 导入导出与独立输入测试，方便二次开发。
- 🧪 **执行前先预演**：在仿真副本里检查候选动作，拦截已配置的碰撞和抓取丢失情况。
- ⏯️ **实验随手控制**：运行、暂停、单步、停止、重置、轨迹回放、JSON 导出，一站完成。
- 💻 **本地工作台**：浏览器交互，桌面与手机尺寸均有适配；数据由本机服务处理，调用云端模型时会发送任务状态。

## 🎯 三个任务，感受完整闭环

| 任务 | 你会看到什么 |
| --- | --- |
| 📥 搬运入盘 | 抓住红色方块，搬到蓝色托盘，松爪并撤离 |
| 🧱 方块堆叠 | 将红色方块放在固定蓝色方块上，检查支撑与稳定状态 |
| 🚧 越障搬运 | 抬高物体、越过障碍，再放到目标位置 |

抓取依靠双侧夹爪接触，成功由物理状态判定。页面中的“验证通过”对应实际仿真检查。

## 🚀 零机器人基础，快速上手

**准备好 Python 3.11+、Node.js 22.12+ 和 Git。** 不需要机器人硬件，也不要求懂 MuJoCo。

### 1️⃣ 下载项目

```bash
git clone https://github.com/FBddcz/embodied-jev.git
cd embodied-jev
```

### 2️⃣ 安装并启动

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci
npm run build
embodied-jev serve --port 8090
```

<details>
<summary>🪟 Windows PowerShell 命令</summary>

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
npm ci
npm run build
embodied-jev serve --port 8090
```

Windows 提供上述启动方式；当前已验证的环境为 macOS 与 Linux CI。

</details>

### 3️⃣ 打开页面，运行第一个实验

访问 **[http://127.0.0.1:8090](http://127.0.0.1:8090)** → 选择任务 → 保持“规则基线” → 点击 **运行实验**。🎉

拖动场景调整视角，单步查看动作，运行结束后拖动时间轴回放。右上角可导出实验 JSON。

> 💡 默认演示由规则策略决策，模型调用数为 **0**。接入并选择模型后，才会进行模型决策。浏览器标签页共享本机的单实验与对比实验；两种视图各自保留状态。

## 🆚 一屏对比：同一任务，不同决策

打开顶部 **模型对比**，选择 2–3 个模型，统一任务、种子和动作预算，再开始实验。没有 Key？可以先用两路规则基线体验完整操作。

![模型对比界面：双规则基线示例](docs/comparison-desktop.png)

*图中为双规则基线的界面示例，非云端模型对比成绩。*

- 🎛️ **自己选模型**：MiniCPM、Jev、GPT、Claude 等已配置入口；同一 API 也能填写不同模型 ID。
- 🍎 **依次 / 并行可选**：默认依次运行；并行最多两路，本地 MiniCPM 推理仍共用模型锁。
- 👀 **实时看差异**：每路独立机械臂场景、阶段、动作和运行状态，模型各自推进。
- ⏯️ **对齐看轨迹**：暂停或结束后，沿同一仿真时间回放；实际耗时保留模型等待，不能拿回放速度当推理速度。
- 📁 **记录带走**：导出各路配置、决策和轨迹。未接通的模型不生成模拟成绩，未知费用也不会显示为 0 美元。

接入方法和比较边界见 **[模型对比指南](docs/COMPARISON.md)**。完整轨迹回放来自这次运行，服务重启后需重新实验；目前还没有导入历史文件的入口。

## 🧠 给机械臂接上模型

| 方式 | 适合谁 | 需要准备 |
| --- | --- | --- |
| 🎮 规则基线 | 想先体验仿真和界面 | 安装项目即可 |
| 🍎 MiniCPM5-2B 本地 | 想研究小模型的决策能力 | 下载权重、安装本地推理依赖 |
| ⚡ TypeSafe Jev | 已有 TypeSafe 账号 | 官方 API Key |
| 🌐 OpenAI 兼容 API | 已有云端平台或本地服务 | Base URL、模型 ID、API Key |
| 🟠 Claude 原生 API | 想直接调用 Anthropic 模型 | Anthropic API Key、模型 ID |
| 🔌 结构化决策 API | 已有返回候选概率的服务 | 接口地址与协议适配 |

### 🌐 已有 API？在界面里填就行

点击 **决策模型** 旁边的插头图标 → 选择接口类型 → 填入 **Base URL、模型 ID、API Key** → 保存 → 测试调用。

![模型连接界面](docs/model-connection.png)

- OpenAI 兼容接口通常填写以 `/v1` 结尾的 Base URL，程序补上 `/chat/completions`。
- 模型 ID 以你的服务商实际提供的名称为准。聊天兼容不代表支持 Jev 专用决策接口。
- Key 由系统钥匙串保存，连接信息放在仓库外的应用目录；刷新页面和重启服务后可恢复。系统存储不可用时，页面会显示“仅本次会话”，不会改存明文 Key。
- 保存配置不发请求；**测试调用**和模型实验会产生真实调用，费用按服务商规则计算。
- **已保存 ≠ 已验证**：页面分开展示连接状态；改动配置后需要重新测试。OpenAI 示例只填入地址和模型名，不会自动发请求。

🔐 服务默认只监听 `127.0.0.1`。Key 不进入浏览器存储、预设、导出或日志；保存后密码框清空，更换接口地址不会自动沿用旧 Key。存储位置和恢复方式见 [模型接入指南](docs/TECHNICAL_GUIDE.md#models)。

普通聊天 API 只返回动作选择，界面不会把模型自报的“信心”当作真实概率。具体协议和环境变量见 [模型接入指南](docs/TECHNICAL_GUIDE.md#models)。

### ⚡ 官方 Jev：获准访问后，填 Key 就能连接

TypeSafe 当前采用邀请制。先到 [TypeSafe 官网](https://typesafe.ai) 点击 **Join Waitlist**，填写邮箱申请访问；收到邀请后，用获批邮箱登录 [官方控制台](https://console.typesafe.ai) 创建 API Key。已有访问权限的用户可直接进入控制台。然后回到行知的模型连接窗口：

1. 选择 **TypeSafe Jev**，官方地址已自动填好。
2. 模型默认 **`jev-latest`**；做固定版本对比时可改为账号有权限的版本，例如 `jev-1.13.0`。
3. 粘贴 TypeSafe Key，点击 **测试调用**。通过后会显示服务实际返回的模型版本和延迟。
4. 关闭窗口、选择任务，点击 **单步** 或 **运行实验**，开始用官方 Jev 决策。

官方接口是 `https://api.typesafe.ai/v1/systemone`，使用候选决策协议并返回概率；OpenAI 或 Anthropic 的 Key 不能直接用于这个入口。`jev-latest` 会随官方更新，实验导出会记录实际返回的模型名。详见 [官方 API 文档](https://docs.typesafe.ai/api)。

### 🟠 Claude 也能接，后续对比更方便

在模型连接中选 **Claude 原生 API**，Base URL 使用 `https://api.anthropic.com/v1`，填写你的 Anthropic Key 和有权限调用的模型 ID，例如 `claude-fable-5-1`。程序调用原生 Messages API，让模型从同一组动作中选择，返回结果仍会做合法性检查。

如果你的中转平台已经将 Claude 封装成 OpenAI 兼容接口，继续选择 **OpenAI 兼容 API** 即可。两种入口分别保存配置。Claude Code 是编程工具，实验调用的是具体模型 API；工具订阅不等同于 API 余额。

准备做 GPT / Claude / MiniCPM 对比？见 [公平对比与复现说明](docs/COMPARISON.md)。本轮已补充 GPT-6 Astra 的真实 API 实验，Jev / Claude 的同设置对照还待完成。

### 🍎 本地 MiniCPM5-2B：不依赖付费 API

先完成快速启动中的环境安装，然后运行：

```bash
python -m pip install -e '.[minicpm]'
export EMBODIED_MINICPM=1
export EMBODIED_DEVICE=auto
embodied-jev warmup
embodied-jev serve --port 8090
```

在界面选择 **MiniCPM5-2B**。首次加载会下载约 **5 GB** 权重；运行还需额外内存。Apple Silicon 可用 `EMBODIED_DEVICE=mps` 明确指定 Apple GPU。当前适配器在 MPS/CUDA 使用 FP16，在 CPU 使用 FP32。

`warmup` 用一次真实决策检查模型加载。服务启动后会从缓存加载自己的模型实例；页面显示加载、就绪与错误状态。缓存完整后，可设置 `HF_HUB_OFFLINE=1` 离线运行。

**想要更轻？** 官方另有 [MLX 4-bit（约 1.42 GB）](https://huggingface.co/openbmb/MiniCPM5-2B-MLX)、[GGUF Q4（约 1.56 GB）和 Q8（约 2.68 GB）](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF)。这些是权重大小；当前工作台接的是 Transformers FP16 路线，量化后端尚待集成。

## 🔄 它是怎么工作的？

**观察物体和接触 → 选择操作阶段 → 预演候选动作 → 模型选择动作 → 机械臂执行 → 再观察**

模型负责在给定选项中做选择，代码负责生成动作、控制关节和检查结果。第一步回答“抓取、搬运还是松开？”，第二步回答“执行哪一个候选动作？”；仅有一个可行阶段时，界面会标注无需模型调用。

输入是 MuJoCo 直接提供的**位置、几何关系、夹爪状态和接触反馈等结构化状态**，以 JSON 形式传给模型。执行后的新状态会进入下一轮决策，形成闭环。目前适合学习、调试和比较受约束的决策策略；相机视觉、自由任务规划和真机控制仍是未来扩展方向。候选概率表示模型对当前选项的偏好，不等于动作成功率。

⚡ **Jev 为什么能快速选择？** 同一状态下合并独立问题、直接输出有限候选概率，可以减少重复输入和文字生成。行知也压缩了几何状态与近期反馈，并复用 HTTP 连接；本地 MiniCPM 直接读取候选 logits。不同路线的速度不能直接等同，详见 [快速推理原理与开源参考](docs/FAST_INFERENCE.md)。

## 📊 有结果，也有边界

内置规则基线已完成 **3 个任务 × 3 个种子 = 9/9 次成功**，每次 8 个动作，监测到的禁止接触步数为 0。这个结果属于规则基线，不能代替 MiniCPM 或 Jev 的模型成绩。

🌐 **GPT-6 Astra 已完成真实 API 闭环：9/9 成功！** 三个任务各跑种子 0、1、2，每局 8 个动作、13 次模型调用。单次决策平均 **2.81 秒**，每局平均 **42.50 秒**，导出记录帧未见禁止接触。这里的动作由模型选择，运行中没有切回规则基线。完整设置、用量和原始轨迹见 **[GPT-6 实验记录](docs/GPT6_EXPERIMENT.md)**。

![GPT-6 Astra 真实 API 实验结果](docs/results/gpt6-astra-2026-09-20.png)

🍎 **MiniCPM 已在 M2 / 16 GB 上用 FP16 真正跑过推理**。最新紧凑状态与候选位移提示在三个任务的 seed 0 上仍是 **0/3 成功**：两项因连续无进展自动停止，一项达到 12 步预算，尚未完成抓取。我们公开失败、耗时和完整轨迹，决策策略还需优化。

以上都是固定任务的小样本实验；GPT 的模型名由所选 API 返回，两批模型实验的样本数与执行节奏也不同，暂不作严格速度排名。TypeSafe Jev / Claude 当前仍只有模拟响应测试，真实结果待补充。

想复现实验、查看模型状态与验证范围？进入 **[完整验证记录](docs/VALIDATION.md)**。

```bash
# 规则基线
embodied-jev benchmark --output runs/baseline.json

# MiniCPM 本地模型：先配置并下载模型
EMBODIED_MINICPM=1 embodied-jev benchmark --provider minicpm \
  --seeds 0 1 2 --threshold 0.55 --output runs/minicpm.json
```

报告会记录成功与失败、调用次数、延迟、阈值和轨迹。默认低概率会暂停，批量评测中记录为 `uncertain`；没有自动切回规则基线。

## 🛠️ 想继续折腾？

顶部 **扩展** 可以保存多套模型连接、调整物体/目标/障碍参数、导入导出任务预设，也能用自己填写的状态与候选单独测试模型。自定义场景会真正进入 MuJoCo；输入测试只返回选择，不控制机器人。

页面预设基于现有三类任务。全新任务、机器人、动作或成功条件需要代码适配，入口和示例见 **[扩展与二次开发指南](docs/EXTENDING.md)**。欢迎把你的 API、任务模板和场景预设接进来！🛠️

- 📘 [技术说明与高级配置](docs/TECHNICAL_GUIDE.md)
- ⚡ [Jev 快速推理与开源实现](docs/FAST_INFERENCE.md)
- 🧪 [验证范围与结果](docs/VALIDATION.md)
- 🔍 [参考项目与设计来源](docs/REFERENCES.md)
- 🤝 [开发与贡献指南](CONTRIBUTING.md)

```bash
python -m pip install -e '.[test]'
pytest -q
npx playwright install chromium
npm run test:ui
```

## 🗺️ 接下来，一起解锁

- [x] MuJoCo + Panda 三大任务、接触反馈与动作预演
- [x] 可视化控制、阶段/动作决策显示、回放与导出
- [x] TypeSafe / 结构化决策 / OpenAI 兼容 / Claude 原生 API 接口
- [x] MiniCPM5-2B 本地推理适配与评测入口
- [x] 2–3 路模型对比、真实运行、同步仿真时间回放
- [x] 具名模型配置、场景预设与独立自定义输入测试
- [ ] Apple Silicon 的 MLX 量化后端
- [x] GPT-6 Astra 真实 API 实验：三个任务 × 三个种子
- [ ] 补齐 Jev / Claude 等模型的同设置对照
- [ ] 更多物体、场景与失败恢复策略
- [ ] 更丰富的观察方式和仿真环境

以上未勾选项是路线图，欢迎一起实现。🚀

## 🤝 一起把行知做得更好

**喜欢这个方向？欢迎 ⭐ Star、🍴 Fork，也欢迎留下第一条 Issue 或 PR！**

你可以帮忙补充新手文档、复现失败案例、接入模型、优化界面，或者设计新的仿真任务。第一次贡献也非常欢迎，不必等到“全部学会”才开始。

[🐛 报告问题 / 提建议](https://github.com/FBddcz/embodied-jev/issues) · [🛠️ 提交 PR](https://github.com/FBddcz/embodied-jev/pulls) · [📖 贡献指南](CONTRIBUTING.md)

提交实验结果时，请附上模型、任务、种子和配置；成功与失败都值得记录。让每一个改进都能被别人复现。

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=FBddcz/embodied-jev&type=Date)](https://star-history.com/#FBddcz/embodied-jev&Date)

每一颗 Star，都是对这个小项目的一份鼓励。欢迎共同维护，让具身决策实验更容易开始、更容易理解。💙

## 🙏 致谢与许可

灵感来自 Jev 机器人实验、SemIf、MuJoCo Menagerie 等开源探索，详见 [参考映射](docs/REFERENCES.md)。感谢所有分享代码、实验和失败经验的开发者。

项目原创代码采用 **MIT**；Panda 资产保留上游 **Apache-2.0** 许可，见 [第三方声明](THIRD_PARTY_NOTICES.md)。模型权重不随仓库分发，其许可单独适用。

行知是独立实验项目，与 TypeSafe、OpenBMB、SemIf 没有隶属关系。Jev 是 TypeSafe 的模型；本地 MiniCPM 模式借鉴有限候选决策思路，使用的是 MiniCPM 权重。
