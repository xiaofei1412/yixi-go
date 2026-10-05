# 展示素材与设计说明

[返回首页](../README.md)

已完成本地 Markdown 渲染预览、图片加载及链接检查；尚未上传真实 GitHub，发布后再核对最终页面。

首页使用 GitHub 可直接显示的 Markdown、简单 HTML、仓库内 SVG/PNG/JPEG 和 GIF，无自定义脚本、外部字体或在线徽章服务。动画只按顺序强调五个学习环节，播放有限次数，最后停在完整静态流程；[静态版本](assets/learning-loop.png)可单独查看。

## 素材来源

| 文件 | 来源与含义 |
| --- | --- |
| `assets/hero.svg` | 原创矢量封面，棋子为装饰示意，不是产品截图或战术结论 |
| `assets/architecture.svg` | 按实际模块绘制的双路线关系；产品与学生模型明确分开 |
| `assets/learning-loop.gif`、`.png` | 学习流程示意，不冒充软件录屏 |
| `assets/product-review.jpg` | 实际产品页面，载入仓库自带 review-demo.sgf，真实 KataGo 分析 |
| `assets/product-comparison.jpg` | 实际产品三栏对照，study-demo.sgf 中重下 G4，回放首手；只裁取对照区域，没有改写画面中的评估数字 |
| `assets/experiments.png` | 从公开结果摘要绘制的六候选配对区间图 |
| `results-summary.json` | 从本地最终比较与复核 JSON 提取的数值，不含用户棋谱、个人路径或权重 |

截图使用独立演示数据库，不使用用户棋谱库；演示 SGF 是人工构造的功能用例。当前截图只验证这次桌面演示流程，不替代完整浏览器兼容、视觉回归或移动触控验收。

图表为六候选分别对同一个预训练参考的得分，不是候选循环赛；每个配置仅一个训练种子。读图时应同时保留首页的独立复核区间和早期负结果。

## 维护

安装 Pillow 与 matplotlib 的文档制作环境中运行：

```powershell
python scripts/build-showcase.py
```

脚本仅重建封面、架构、流程动画和实验图，不训练、不调用引擎、不重拍截图。Windows 默认使用微软雅黑；其他环境可通过 `SHOWCASE_FONT` 指定已安装的中文字体文件。更新实验图时先核实 `results-summary.json`，保留评估协议与区间。

截图重新制作时仍使用独立演示库，通过真实页面操作获取；不要在 DOM 中编造状态或把设计图标成软件截图。完整的原首页叙事保存在 [PROJECT.md](PROJECT.md)，细节仍由两份分路线 README 承载。

## 参考案例

- [Excalidraw](https://github.com/excalidraw/excalidraw)：先展示产品用途和能力，再提供开发入口。
- [LobeHub](https://github.com/lobehub/lobehub)：品牌首屏、简短导航与功能媒体分区。
- [Build your own X](https://github.com/codecrafters-io/build-your-own-x)：清楚的一句话定位和易扫描的入口。

仅借鉴信息层级，未复制这些项目的图片、标识或文案。发布流程参考 [VS Code 官方文档](https://code.visualstudio.com/docs/sourcecontrol/repos-remotes)，大文件安排参考 [GitHub 官方文档](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。
