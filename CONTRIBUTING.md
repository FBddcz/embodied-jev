# 参与开发

欢迎补文档、接模型、改界面或增加任务。遇到问题可以先开 Issue，附上复现步骤和运行环境；扩展入口见 [二次开发指南](docs/EXTENDING.md)。

## 本地检查

按 [README](README.md) 建好虚拟环境后运行：

```bash
python -m pip install -e '.[test]'
npm ci
npm run build
pytest -q
npx playwright install chromium
npm run test:ui
```

浏览器测试使用独立的 8099 端口和内存配置，不访问系统钥匙串。前端格式化命令为 `npx prettier --write frontend/ vite.config.js`。

## 提交代码或实验

- PR 说明改了什么、为什么改、如何验证。界面改动请附上桌面和手机尺寸截图。
- 新任务需要接好观察、候选动作、物理反馈、暂停/停止和回放；默认抓取继续依靠真实夹爪接触。
- 实验结果附上模型版本、任务、种子、场景与决策配置、复现命令，保留失败记录。规则基线与模型成绩分开报告，候选概率不作为物理成功率。
- API Key 只在自己的电脑上配置。提交前检查改动，不上传密钥、私人实验记录、模型缓存或虚拟环境；保留项目和机器人资产的许可声明。
