# 🙏 参考映射

感谢这些开源项目提供的思路与实践。以下为架构参考，整理于 **2026-09-20**；行知的实现与实验结果独立记录。

| 参考项目 | 借鉴点 | 行知中的实现 |
| --- | --- | --- |
| [openroboto / jev-robot-control](https://github.com/openroboto-ai/jev-robot-control) | 意图与动作分层、多模型展示 | 两阶段候选选择、独立对比与轨迹回放 |
| [jev-robotics-demo](https://github.com/FazalAAli/jev-robotics-demo) | 执行前仿真预演 | 在 MuJoCo 副本中检查接触与抓取状态 |
| [jev_fsd](https://github.com/BrendanH18/jev_fsd) | 程序生成候选，模型做选择 | 有限动作菜单与结果校验 |
| [jev-askable-arm](https://github.com/TarunTomar122/jev-askable-arm) | 机器人动作基元 | 有界目标位姿与 IK 执行 |
| [jev-drone](https://github.com/RomanSlack/jev-drone) | 感知、决策与控制分离 | 可选视觉观察、有限候选决策与独立控制循环 |
| [jevduck](https://github.com/amazedsaint/jevduck) | 可检查、可控制的实验 | 暂停、停止、结果反馈与日志 |
| [openarm-jev-lab](https://github.com/tripathiarpan20/openarm-jev-lab) | 仿真与界面分离、LIBERO-PRO 实验 | 后台实验与浏览器工作台；本项目尚未接入 LIBERO |
| [SemIf](https://github.com/TheoLeeCJ/SemIf) | 有限候选概率读出 | MiniCPM5-2B 本地决策适配 |
| [MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie) | Franka Panda 模型与资产 | 机器人模型，保留上游许可与来源 |

🎬 **演示参考**：[Dmytro Hrybov 的 MuJoCo 演示](https://x.com/dimentary/status/2101018760371171420)及[后续说明](https://x.com/dimentary/status/2101018934095003720)，展示了结构化几何、接触状态与两阶段决策。

SemIf 已公开支持 MiniCPM5-2B；它是独立开源项目。行知的本地 MiniCPM 模式使用 MiniCPM 权重，官方 Jev 则通过 [TypeSafe API](https://docs.typesafe.ai/api) 接入。候选概率不等同于物理任务成功率。

本次查阅的公开对比中，OpenRoboto 比较 Jev 与 GPT，jev-robotics-demo 比较 Jev 与 Claude；[openarm-jev-lab](https://github.com/tripathiarpan20/openarm-jev-lab/blob/c89a73f6ff10094acc3d27e45ef91a31791eadb2/README.md#what-improved)比较同一 Jev 控制器的动作菜单改进。虽然它已经运行 LIBERO-PRO，这些资料并未报告与 OpenVLA、SmolVLA 或 π0 的同条件实测对比。不能据此声称 Jev 优于 VLA。

进一步阅读：[👁️ 视觉模式](VISION.md) · [⚡ 快速推理与源码分析](FAST_INFERENCE.md) · [🆚 模型对比](COMPARISON.md) · [📊 实测结果](VALIDATION.md) · [📄 第三方许可](../THIRD_PARTY_NOTICES.md)
