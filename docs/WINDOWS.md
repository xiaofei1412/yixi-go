# Windows 免安装版

[Release 页面](https://github.com/xiaofei1412/yixi-go/releases/tag/v1.0.0) · [直接下载 ZIP](https://github.com/xiaofei1412/yixi-go/releases/download/v1.0.0/YixiGo-v1.0.0-windows-x64.zip)

完整解压，双击 `YixiGo.exe`，浏览器自动打开。保留启动器窗口；关闭该窗口会停止本地服务。重复双击会打开已有实例的页面，不会再开一个数据库服务。

## 运行条件

- Windows 10/11 x64，支持 OpenCL 的显卡与厂商驱动。未提供 macOS/Linux/ARM 原生包或 CPU 后备引擎。
- 已包含 Python 3.10 运行时、KataGo 1.16.4 OpenCL、b28 模型、前端和运行依赖。无需 Python、Node.js、PyTorch、CUDA Toolkit 或手动下载模型。
- 首次分析会生成当前显卡的调优缓存，可能较慢；超时后可稍候重试。本机全新缓存验收约 80 秒，其他硬件不保证相同时长或性能。
- 此版本未代码签名。Release 附有 `SHA256SUMS.txt`，可使用 `Get-FileHash .\YixiGo-v1.0.0-windows-x64.zip -Algorithm SHA256` 核对完整性。

## 数据归属与备份

| 内容 | 位置 / 行为 |
| --- | --- |
| 程序、引擎、模型 | 解压后的程序文件夹；运行时不写入用户记录 |
| 数据库 | `%LOCALAPPDATA%\YixiGo\games.sqlite3`，首次运行自动建表，初始为空 |
| 启动和引擎日志 | 同一数据目录中的 `launcher.log`、`engine.log` |
| 显卡调优缓存 | 同一数据目录中的 `engine` 子目录 |
| 网络 | 仅监听 `127.0.0.1`，自动选择空闲端口，无云同步 |

不同电脑和 Windows 账户各自独立；同一账户运行多个程序副本仍共用同一份数据。这里的私有指本地存储与 Windows 账户目录隔离，数据库没有额外加密，也不是应用内多账号系统。

点击启动器“打开数据目录”。**退出弈习后**复制整个目录即可备份；恢复时先退出，再将备份放回。删除程序文件夹不会删除这里的棋谱。升级前建议先备份。

源码版继续使用仓库内 `product_data`，不会自动迁移或覆盖旧记录。需要迁移时，退出两种版本，先备份目标数据目录，再把旧 `product_data/games.sqlite3` 复制进去；此操作会替换目标已有棋谱库。

## 开发者复现打包

先按[引擎说明](../katago_engine/INSTALL.md)准备相同版本的 Windows OpenCL 发行文件和模型，再在项目根目录执行：

```powershell
py -3.10 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r packaging\requirements-windows-lock.txt
.\.venv-build\Scripts\python.exe scripts\build-windows.py
```

`requirements-build.txt` 是直接依赖入口；锁文件保存本次验收的完整安装版本。构建必须在 Windows x64 上完成，不能把已有虚拟环境直接搬到另一台电脑使用。构建期间会下载第三方许可文件并缓存于 `build/license-cache`。

输出在 `dist/YixiGo-v1.0.0-windows-x64.zip`，另生成 `dist/SHA256SUMS.txt`。目录内 `manifest.json` 记录版本、构建时 Git 提交/工作区状态及逐文件 SHA-256。构建采用明确的文件清单，不复制原始数据库、日志、训练数据或学生权重。重新打包前退出正在运行的此版本。

独立空数据目录验收（请使用一个从未使用过的目录）：

```powershell
$env:YIXI_DATA_DIR = Join-Path $PWD 'build\acceptance-new-user'
$p = Start-Process '.\dist\YixiGo-v1.0.0-windows-x64\YixiGo.exe' -ArgumentList '--self-test' -Wait -PassThru
$p.ExitCode
Get-Content "$env:YIXI_DATA_DIR\self-test.json"
Remove-Item Env:YIXI_DATA_DIR
```

此命令会在指定目录导入公开演示棋谱、启动真实引擎并验收分析、练习和变化对照。成功退出码为 0，并生成 `self-test.json`；未显式指定 `YIXI_DATA_DIR` 时拒绝自测，避免向默认用户目录写入测试棋谱。普通用户直接双击即可，无需这些命令。

发布时应从已提交且干净的工作区构建，把 ZIP 与 SHA256SUMS 上传到相同提交对应的 GitHub Release；`dist` 不进入源码 Git 历史。

## 已验证范围

本次针对 Windows 本机做了产品回归、账户目录隔离、空库初始化、中文/空格/`&` 路径、真实 EXE 服务、真实 KataGo 学习流程和启动器窗口验收。没有在所有显卡型号或一台全新 Windows 机器上做兼容性认证；GPU 驱动仍是用户机器需要具备的条件。
