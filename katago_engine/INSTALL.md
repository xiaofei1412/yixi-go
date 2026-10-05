# 准备 KataGo 引擎与模型

Git 源码保留 `product_analysis.cfg`，引擎发行包、DLL、模型及本机调优缓存不随源码提交。本地完整工作目录中的这些文件仍然保留。

1. 从 [KataGo 官方 v1.16.4 发布页](https://github.com/lightvector/KataGo/releases/tag/v1.16.4) 获取适合本机的 Windows OpenCL 发行包，解压到本目录，保留发行包所需 DLL；不要覆盖项目自己的 `product_analysis.cfg`。
2. 从 [KataGo 官方训练站模型列表](https://katagotraining.org/networks/) 获取模型。本项目已验证的模型名为 `kata1-b28c512nbt-s12192929536-d5655876072.bin.gz`，放到本目录。该文件约 271 MB。若选择其他模型，需要同步修改 `katago_service.py` 的 `ENGINE_FILES`，重启服务；训练身份校验及旧分析缓存也会受影响。
3. 确保显卡驱动支持 OpenCL，再从项目根目录运行 `start-product.cmd`。首次引擎启动可能进行显卡调优。

目录至少应包含：

```text
katago_engine/
├── katago.exe
├── kata1-b28c512nbt-s12192929536-d5655876072.bin.gz
├── product_analysis.cfg
└── 发行包要求的 DLL 等依赖
```

这里记录的是本项目已经使用的版本组合，不声称是最新版本。其他操作系统需替换对应引擎、路径和启动方式。KataGo 及模型的许可遵循上游说明；本仓库不为第三方资产另行授予许可。
