# 演示与视频导出

首页放了两段真实双相机视觉回合：正常搬运，以及托盘移动后重新抓取、调整路线的回合。它们由已保存的实验记录生成，不需要重新请求模型。

| 演示 | 动作数 | 原始实验耗时 | 视频 / 动图 |
| --- | --- | --- | --- |
| 看图抓取与搬运 | 32 | 235.41 秒 | [MP4](media/vision-transfer.mp4) · [GIF](media/vision-transfer.gif) |
| 托盘移动与重新抓取 | 43 | 342.21 秒 | [MP4](media/vision-recovery.mp4) · [GIF](media/vision-recovery.gif) |

MP4 分辨率为 1280 × 900，适合全屏观看；GIF 是便于 README 直接播放的压缩预览。完整实验设置与失败回合见[视觉规划结果](PLANNING_RESULTS.md)。

## 画面各部分是什么意思？

- **左侧动作回放**：用实验保存的关节和物体姿态重新绘制，没有重新运行控制策略或物理轨迹。
- **右侧两台相机**：来自该步真正发送给模型的 PNG。动作进行时保持本步输入，进入下一步才切到新观测；结尾显示最终观测。
- **下方模型意图**：使用这一步模型返回的简短说明。开场、扰动提示和结束说明是额外的演示字幕，已分别标注。

回放保留全部动作，每步展示 1.2 秒，省略 API 等待。正常视频约 43 秒，扰动视频约 59 秒；这不是机器人或模型的实时速度。相机原图只是缩放排版，GIF 和 MP4 的压缩会改变显示像素，原始 PNG 与哈希仍在相机 ZIP 中。

第二段中，托盘在第 20 步后沿 X 平移 6 cm，这是启用的外部扰动。第 19 步后双指接触丢失则是实际执行中出现的情况；第 25 步重新抓住。视频中分别标注，不把它们合成一次预设恢复动画。

## 从记录重新导出

先安装可选的视频依赖：

```bash
python -m pip install -e '.[video]'
```

从仓库根目录运行：

```bash
python scripts/render_demo.py \
  --episode docs/results/planning-vision-gpt6-v2-cameras-transfer.json.gz \
  --cameras docs/results/planning-vision-gpt6-v2-cameras-transfer-cameras.zip \
  --output runs/demo-transfer --title '看图完成抓取与搬运'

python scripts/render_demo.py \
  --episode docs/results/planning-vision-gpt6-v2-live-target-shift-run1.json.gz \
  --cameras docs/results/planning-vision-gpt6-v2-live-target-shift-run1-cameras.zip \
  --output runs/demo-recovery --title '托盘移动后，重新抓取并调整路线'
```

需要可用的 MuJoCo 渲染环境和中文字体。脚本会尝试系统常见字体，也可用 `--font` 指定字体文件。输出同名 MP4、GIF、封面 PNG 和描述来源的 JSON；已有文件不会被覆盖，除非显式加 `--overwrite`。

导出前会检查场景版本、实验 ID 和每张模型输入图的哈希。场景改动后应切回原实验代码版本再导出。当前脚本面向搬运任务的双相机逐步视觉记录，支持标注目标移动；其他实验类型需要相应适配。

[正常演示的文件信息](media/vision-transfer.json) · [扰动演示的文件信息](media/vision-recovery.json)
