# 演示与视频导出

README 的实验章节默认展开三段动图：Jev 分层 XYZ 搬运、GPT 双相机视觉搬运，以及托盘移动后重新抓取。它们由已保存的实验记录生成，不需要重新请求模型。

| 演示 | 动作数 | 原始实验耗时 | 视频 / 动图 |
| --- | --- | --- | --- |
| Jev 分层 XYZ 与真实概率 | 88 | 79.71 秒 | [下载 MP4](https://github.com/FBddcz/embodied-jev/raw/refs/heads/main/docs/media/jev-hierarchical.mp4) · [GIF](media/jev-hierarchical.gif) |
| 看图抓取与搬运 | 32 | 235.41 秒 | [下载 MP4](https://github.com/FBddcz/embodied-jev/raw/refs/heads/main/docs/media/vision-transfer.mp4) · [GIF](media/vision-transfer.gif) |
| 托盘移动与重新抓取 | 43 | 342.21 秒 | [下载 MP4](https://github.com/FBddcz/embodied-jev/raw/refs/heads/main/docs/media/vision-recovery.mp4) · [GIF](media/vision-recovery.gif) |

MP4 可下载后播放，分辨率为 1280 × 1080，适合暂停查看决策。Jev GIF 是 **8 倍速**，约 14 秒；两段视觉 GIF 是 **5 倍速**，约 9 秒和 12 秒。完整设置与失败说明见[Jev 实测](JEV_EVALUATION.md)和[视觉规划结果](PLANNING_RESULTS.md)。

## Jev 的概率动图

这段来自 `jev-1.13.0` 的第二局分层 XYZ 记录，使用仿真坐标和接触反馈，没有给模型发送图像。左侧重绘保存的关节与物体姿态，右侧分别显示八个子目标和 X/Y/Z/夹爪的实际候选概率；选中项与 API 返回一致，不计算联合成功率。下方列出当前动作、两次请求的延迟和执行反馈。

每个动作在 MP4 中展示 1.2 秒，总长 110.6 秒；GIF 在此基础上加速 8 倍。第 80 步下放时失抓、方块落到托盘上，后续模型张爪并撤离，视频明确标出这段过程。终态通过不等于精确放置稳定，回放速度也不代表真实 API 速度。

## 两段视觉演示怎样读？

- **左侧动作回放**：用实验保存的关节和物体姿态重新绘制，没有重新运行控制策略或物理轨迹。
- **右侧两台相机**：来自该步真正发送给模型的 PNG。动作进行时保持本步输入，进入下一步才切到新观测；结尾显示最终观测。
- **左下候选面板**：显示当轮实际候选顺序，绿色高亮模型选中项，并显示该次真实 API 调用耗时。
- **评分来源与动作说明**：有服务商返回的候选概率才显示数值。这两局的 GPT 接口没有概率，画面明确标注“此接口未提供”；绿色不表示概率 100%。简短动作说明来自模型回复。
- **物理反馈**：标注动作执行、持物变化和失抓。开场、扰动和结束字幕是额外的演示说明，不是模型回复。

MP4 保留全部动作，每步展示 1.2 秒，省略 API 等待，正常视频约 43 秒，扰动视频约 59 秒。GIF 在这个基础上加速 5 倍。这不是机器人或模型的实时速度。相机原图只是缩放排版，GIF 和 MP4 的压缩会改变显示像素，原始 PNG 与哈希仍在相机 ZIP 中。

“行知 · EmbodiedJev”是工作台名称，画面另列实际调用模型。两段相机演示是 GPT 视觉实验，单列的分层概率动图才是官方 Jev 的真实调用。

第二段中，托盘在第 20 步后沿 X 平移 6 cm，这是启用的外部扰动。第 19 步后双指接触丢失则是实际执行中出现的情况；第 25 步重新抓住。视频中分别标注，不把它们合成一次预设恢复动画。

## 从记录重新导出

先安装可选的视频依赖：

```bash
python -m pip install -e '.[video]'
```

从仓库根目录运行：

```bash
python scripts/render_demo.py \
  --episode docs/results/jev-hierarchical-live-2026-09-20.json.gz \
  --output runs/demo-jev --title 'Jev 分层 XYZ · 真实概率与动作' --gif-speed 8

python scripts/render_demo.py \
  --episode docs/results/planning-vision-gpt6-v2-cameras-transfer.json.gz \
  --cameras docs/results/planning-vision-gpt6-v2-cameras-transfer-cameras.zip \
  --output runs/demo-transfer --title '看图完成抓取与搬运'

python scripts/render_demo.py \
  --episode docs/results/planning-vision-gpt6-v2-live-target-shift-run1.json.gz \
  --cameras docs/results/planning-vision-gpt6-v2-live-target-shift-run1-cameras.zip \
  --output runs/demo-recovery --title '托盘移动后，重新抓取并调整路线'
```

需要可用的 MuJoCo 渲染环境和中文字体。脚本会尝试系统常见字体，也可用 `--font` 指定字体文件。`--gif-speed` 默认是 5，可单独调整动图速度。输出同名 MP4、GIF、封面 PNG 和描述来源的 JSON；已有文件不会被覆盖，除非显式加 `--overwrite`。

导出前检查场景版本；视觉回合还检查实验 ID 和每张模型输入图的哈希，分层回合检查记录的子目标、通道概率与动作一致性。场景改动后应切回原实验代码版本再导出。当前支持搬运任务的分层坐标记录和双相机逐步视觉记录，视觉记录支持标注目标移动。

[Jev 演示的文件信息](media/jev-hierarchical.json) · [正常视觉演示的文件信息](media/vision-transfer.json) · [扰动演示的文件信息](media/vision-recovery.json)
