# LIBERO：纯 GPT-6 与 GPT-6 + Jev

**用真实仿真相机观测比较两种分工：GPT-6 同时负责视觉规划和局部动作，或由 GPT-6 规划、Jev 执行局部决策。** 当前采用原版 LIBERO 的开发任务子集，不是完整套件评测，也没有接入训练好的 VLA。

**已发布三局成功录像；当前四局结果为纯 GPT-6 2/2、GPT-6 + Jev 1/2。混合组微波炉耗尽 1200 秒，分析后待重测。** [结果、费用与回放](results/libero-vision/RESULTS.md)。

## 两种模式

| 环节 | 纯 GPT-6 | GPT-6 + Jev |
| --- | --- | --- |
| 视觉与阶段规划 | GPT-6 Astra | GPT-6 Astra |
| 局部 XYZ、旋转和夹爪选择 | GPT-6 Astra | Jev |
| 执行与成功判定 | LIBERO / robosuite OSC_POSE；官方 check_success | 相同 |
| 观测与预算 | 双相机 RGB-D、本体反馈、固定初态 | 相同 |

GPT-6 看到外部与腕部 RGB，选择目标像素；程序使用该像素的**真实深度和相机标定**得到三维航点。也允许模型根据反馈选择相对位移。局部控制器接收航点、本体反馈与近期位移，不接收物体真值、奖励、目标谓词或未来仿真结果。

每次动作后重新读取传感器。到达航点、连续停滞或达到模型指定的阶段步数时，重新请求视觉规划。执行器只限制动作幅度与时长，**保留模型选出的方向**。两种模式共用阶段接口和刷新条件，但独立调用规划器；这是系统对照，不是固定同一条高层轨迹的单因素消融。

## 当前协议

- 原版 LIBERO：8f1084e3132a39270c3a13ebe37270a43ece2a01。
- robosuite 1.4.1、MuJoCo 3.5.0、NumPy 1.26.4、PyTorch 2.2.2；独立 Python 3.11 环境。
- 关顶层抽屉、关微波炉；libero_90 的任务 0 / 33，初态 0，seed 0。
- 两台 384 × 384 相机；RGB 与深度同时上下翻转，统一到左上角原点。
- 固定 10 步初始化；20 Hz，动作重复 5 步，最大归一化幅度 0.5。
- 本次使用 wall-time 模式：每局成功或初始化后运行满 1200 秒停止，无步数／请求上限；每个模型任务含重试保留 20 美元已知费用准入保护，并非服务端硬性扣费上限。
- 运行前冻结源码与协议；配对检查官方初态和初始化后的完整仿真状态哈希。

**费用和速度包含视觉规划、局部控制的全部请求。** 延迟按客户端测量，包含网络等待；缺失用量显示未知。价格使用 [GPT-6 Astra 官方标准价](https://developers.openai.com/api/docs/models/gpt-6-astra)和 [TypeSafe Jev 官方价](https://docs.typesafe.ai/models)，中转平台实收、缓存优惠可能不同。Jev 返回 output token 统计，但官方输出免费。

对照录像采用共同的实际运行时间轴，按同一倍速播放，保留模型等待；单列初始化耗时。不要与旧 Meta-World 按环境步对齐、省略等待的动图比较播放速度。

## 运行

先在工作台保存 GPT-6 Astra 与 Jev 连接。凭据继续存于系统钥匙串，不进入实验清单或结果。

安装独立模拟器环境，无需训练数据集或模型权重：

~~~bash
python3.11 -m venv .venv-libero
.venv-libero/bin/python -m pip install -r benchmarks/requirements-libero.txt
python scripts/setup_libero.py --root .sim/LIBERO
python -m pip install -e '.[video]'
~~~

下载器固定官方版本，按上游文件哈希校验；网络中断时再次运行即可跳过已完成文件。源码和资产约 416 MiB，环境和缓存另计。

~~~bash
embodied-jev libero-compare \
  --manifest benchmarks/libero-vision-compare.json \
  --worker-python .venv-libero/bin/python \
  --libero-root .sim/LIBERO \
  --output runs/libero-vision --budget-mode wall-time --timeout 1200 --max-usd 20
~~~

默认运行两个任务 × 两种模式；上面的 wall-time 参数采用本次时间预算，省略时仍使用 200 步／80 请求的短测默认值。加 --modes noop --max-steps 5 可做零模型调用的安装检查；已有输出目录不会混写。Linux 使用可工作的 EGL 或 OSMesa；macOS 使用 CGL，需要允许进程访问系统图形服务。

导出共同墙钟时间的 8 倍速对照：

~~~bash
python scripts/render_libero_comparison.py \
  --episodes runs/libero-vision/gpt6/drawer-init0 runs/libero-vision/gpt6-jev/drawer-init0 \
  --output runs/libero-drawer-video --speed 8
~~~

## 参考

[Dimweaker/jev-libero](https://github.com/Dimweaker/jev-libero) 提供了 LIBERO 分层选择、任务配置和交互回放的参考。它的公开控制器使用仿真状态与物理预演；本项目的新增实验采用相机选点与深度测量。未复制上游录像或把其成绩当作本项目成绩。
