# 行知实验室

在线地址：**https://fbddcz.github.io/embodied-jev/**。

GitHub Pages 静态实验展示：按 LIBERO、Meta-World、Panda 分类，提供录像、倍速、决策时间轴、概率、末端轨迹、结果和数据链接。页面只播放已发布的真实记录；实时仿真与 API 调用仍由本地工作台完成。

~~~bash
python scripts/build_site.py
python scripts/serve_site.py --port 8123
~~~

打开 http://127.0.0.1:8123 。本地服务器支持视频字节范围请求，可正常跳转。

构建器只复制明确列出的公开媒体，并从原始结果生成目录，不扫描本地运行或连接配置。LIBERO 记录通过 `docs/results/libero-vision/index.json` 注册；完成的 v2 推盘子配对可通过 `docs/results/libero-supervisor-plate/index.json` 注册，未完成记录不发布。部署由 GitHub Actions 在 main 的 Checks 通过后执行，仓库 Pages 来源设置为 GitHub Actions。

🤝 **社区复现**由 `community/entries/*.json` 收录，构建时校验作者、日期、任务、链接与指标。独立社区区块展示署名和复现链接，例子目录不入库。参见[投稿指南](../docs/COMMUNITY.md)。

~~~bash
npm run test:site
~~~

页面、代码与项目实验媒体来自本项目；社区媒体来自各投稿作者。
