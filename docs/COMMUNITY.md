# 🤝 分享你的复现

**欢迎把你的机器人仿真实验放进 [在线展示页](https://fbddcz.github.io/embodied-jev/)。** 每条作品保留作者名字、GitHub 账号、实验日期、任务、方法和结果，链接回你的代码。成功、失败和部分完成都欢迎。

## 🚀 两种投稿方式

**会提 PR：** 复制 [JSON 模板](../community/examples/entry.example.json) 到 `community/entries/<你的英文编号>.json`，填写真实信息，运行下面的校验并提交 PR。合并后，GitHub Pages 自动构建并展示你的作品。

```bash
python scripts/community_entries.py --validate
```

**不熟悉 PR：** 打开 [社区复现投稿表](https://github.com/FBddcz/embodied-jev/issues/new?template=community-reproduction.yml)，填写信息并上传录像。维护者整理为 PR 后再收录到展示页；提交 Issue 不会立即发布。

## 🎬 录像和复现信息

录像推荐 MP4。可以在 GitHub Issue 正文中拖入文件，再把生成的 `https://github.com/user-attachments/assets/...` 链接填到 `video_url`；也支持 GitHub raw、`raw.githubusercontent.com`、`user-images.githubusercontent.com` 或你的 `github.io` 直接文件地址。可选封面支持 PNG、JPEG、WebP。

**请保留足够信息让别人重跑：** 模型实际版本、仿真版本、任务 ID、seed／初始状态、观测内容、预算、运行命令、成功判定，以及录像是否加速或省略等待。把这些写在你公开 GitHub 仓库的复现说明中，字段分别链接到实验协议、复现步骤、代码与结果。推荐代码链接固定到 commit，避免之后改动导致无法复现。

| 字段 | 填写内容 |
| --- | --- |
| `id` | 小写英文、数字和连字符，必须与文件名一致 |
| `author.name` / `author.github` | 展示名字 / GitHub 用户名，不带 `@` |
| `date` | 实际实验日期，`YYYY-MM-DD` |
| `task` / `environment` | 任务和 seed；平台为 `libero`、`metaworld`、`panda` 或 `other` |
| `method` / `result` | 模型与控制方法；结果为 `success`、`failure` 或 `partial` |
| `protocol` / `reproduction_url` / `source_url` | 可公开阅读的 GitHub 协议说明 / 复现步骤 / 代码与结果链接 |
| `video_url` / `poster_url` | 录像 / 可选封面的直接 HTTPS 地址 |
| `metrics` | 可选：`steps`、`wall_seconds`、`estimated_cost_usd` |
| `metrics.cost_basis` | 填费用时必填：计费来源、输入输出用量、是否包含重试等口径 |
| `playback` | 是否加速、倍数、是否省略请求等待 |

**未知数据填 `null` 或省略，不能填 0 代替。** 0 仅适用于已确认没有花费／步数的情况。费用统一为美元估算，注明口径；没有可靠用量时可以不填。请勿上传密钥、私人视频或无权公开的内容。

## 🧪 数据如何展示

社区投稿显示为 **「社区复现 · 作者报告」**，不混入项目自身的成功率、费用统计，也不意味着项目已独立重跑或验证作者身份。投稿必须能追溯到代码、说明和录像。自动校验只检查格式、字段、日期和允许的公开链接形式，不会下载或执行投稿内容，也不会验证远端文件存在。

展示页采用静态 GitHub Pages，没有账号系统和文件上传后端。PR 是正式收录入口；Issue 表单是便捷提交入口。没有投稿时不生成虚构作品，`community/examples/` 只存模板，不进入展示目录。
