# 用 VS Code 首次发布到 GitHub

[返回展示页](../README.md)

当前远程仓库已是 [xiaofei1412/yixi-go](https://github.com/xiaofei1412/yixi-go)，本地 `origin` 已对齐该地址。Windows 成品下载见 [Releases](https://github.com/xiaofei1412/yixi-go/releases)，复现打包见 [WINDOWS.md](WINDOWS.md)；ZIP 作为 Release 附件上传，不提交进源码历史。

下文保留首次发布流程，供新副本或 fork 参考：**本地项目 → 本地 Git → GitHub 远程仓库**。这里的“远程”是 Git 的 `origin`，不需要 Remote-SSH；“GitHub Repositories”扩展的远程虚拟工作区也不是上传整个本地项目的必要步骤。[VS Code 工作方式说明](https://code.visualstudio.com/docs/sourcecontrol/github)

## 1. 上传哪些文件

已补齐根目录 `.gitignore`，不删除本机任何资源。本次按真实 Git 忽略规则检查，应提交 53 个文件，合计约 0.76 MiB；后续修改会改变数量。

| 会提交 | 用途 |
| --- | --- |
| 三份 README、`docs/` | 展示图片、结果摘要、项目叙事与操作说明 |
| 产品 `.py`、`index.html`、`app.js`、`style.css` | 本地全栈产品 |
| `train_v2*.py`、`train_v2_config*.json` | 新训练实现及六组实验的配置入口 |
| 旧训练脚本、`cpp_src/`、`setup.py` | 保留原始 Actor/Learner 与 C++ 搜索源码 |
| `tests/`、`examples/` | 单元测试、真实引擎集成脚本和演示 SGF |
| 依赖清单、`start-product.cmd`、`scripts/` | 安装、启动与展示素材生成 |
| `katago_engine/product_analysis.cfg`、`INSTALL.md` | 引擎配置和外部资产安装说明 |

| 不提交，仍留在本机 | 原因 |
| --- | --- |
| `.venv*`、`.vscode/`、`__pycache__/`、`build/` | 本机环境、编辑器设置和编译产物 |
| `product_data/` | 用户棋谱、笔记、练习与数据库 |
| `data_buffer/`、`train_v2_data/`、`train_v2_runs/` | 原始数据、模型与完整本地实验记录 |
| `*.pt`、`*.pth`、`*.pyd` | 权重与平台绑定扩展；保留源码和结果摘要 |
| KataGo exe、DLL、模型、调优缓存 | 上游二进制与机器相关资源，按安装说明另行准备 |
| `.backups/`、日志、`.env*` | 备份、本地路径信息与环境秘密 |

KataGo 模型文件约 271 MB，超过普通 Git 提交的单文件 100 MiB 上限。当前采用“源码 + 外部资源安装说明”；以后若发布自己训练的权重，可单独整理 GitHub Release 或 Git LFS，不要直接把整个工作文件夹拖到网页。[GitHub 文件限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

## 2. 检查 Git 与身份

在 VS Code 用 **文件 → 打开文件夹** 打开整个项目目录，在终端运行：

```powershell
git --version
```

本机已找到 `C:\Program Files\Git\cmd\git.exe`，但当前终端 PATH 未发现 `git`。若你也遇到“无法识别”，可以在 VS Code 设置中搜索 **Git: Path**，填写该路径，并重启 VS Code；终端仍不可用时先用下面的当前会话配置，不必重装：

```powershell
$env:Path = 'C:\Program Files\Git\cmd;' + $env:Path
git --version
```

首次提交需要配置提交身份。将下面占位内容替换为你的名字，以及 GitHub 账户邮箱或账户 Settings → Emails 中提供的 noreply 邮箱；不要输入密码或令牌：

```powershell
git config --global user.name "你的提交名字"
git config --global user.email "你的 GitHub 提交邮箱"
```

若已有正确配置，直接跳过。`--global` 会影响其他仓库；希望仅对本项目设置时，先初始化，再省略 `--global`。[Git 提交身份说明](https://docs.github.com/en/get-started/git-basics/setting-your-username-in-git)

## 3. 推荐：直接由 VS Code 创建并发布

1. 点击左侧 **源代码管理**（`Ctrl+Shift+G`），选择 **初始化仓库 / Initialize Repository**。
2. 检查变更列表：应以源码、文档和展示图片为主；不应出现 `.venv`、用户数据库、模型或几千个数据分片。`.gitignore` 已配置，但首次提交仍要核对列表。
3. 将需要的文件暂存，输入提交说明，例如 `Build Go learning product and training showcase`，点击 **提交 / Commit**。
4. `Ctrl+Shift+P`，执行 **Publish to GitHub**，按浏览器提示登录自己的 GitHub 账户。
5. 选择仓库名，例如 `yixi-go`（仅为建议，需以你的账户中可用名称为准），按展示需求选择 **Public** 或 **Private**。
6. 发布成功后点击 **Open on GitHub**。根目录 `README.md` 会成为仓库首页，图片随 `docs/assets/` 一起显示。
7. 在本地终端确认真实地址：

```powershell
git remote -v
git remote get-url origin
```

输出中的 `https://github.com/<你的用户名>/<实际仓库名>.git` 才是这个项目的真实远程地址；将末尾 `.git` 去掉即为浏览器展示页。当前尚未创建，文档不编造用户名或仓库 URL。

**Publish to GitHub 会新建远程仓库；已有仓库则应使用 Add Remote。** 该流程与命令含义以 [VS Code 官方发布说明](https://code.visualstudio.com/docs/sourcecontrol/repos-remotes) 为准。

## 4. 如果先在 GitHub 网页创建了空仓库

创建时先不添加 README、License 或 `.gitignore`，避免远程先产生一份独立历史。复制仓库绿色 **Code → HTTPS** 中的真实地址。完成本地初始化与提交后，在 VS Code 终端运行：

```powershell
git branch -M main
git remote add origin https://github.com/你的用户名/实际仓库名.git
git push -u origin main
```

必须替换占位地址。如果已有 `origin`，先 `git remote -v` 核对，确需纠正时使用 `git remote set-url origin <真实地址>`。也可用命令面板 **Git: Add Remote**，名称填写 `origin`，再 **Publish Branch**。

如果远程已经有代码或提交，先克隆远程到一个新目录，再把本项目中应提交的文件复制进去、比较并提交；不要复制 `.git` 或本机环境，不要用强制推送覆盖未知历史。不要对已有仓库重复执行“创建空仓库”流程。

## 5. 发布后检查与后续更新

- 仓库首页能看到封面、学习流程动画、三栏对照和实验图；点开图片可查看原始尺寸。
- README 的产品、训练、资源安装链接能打开；`docs/results-summary.json` 可查看数值来源摘要。
- 远程文件树不含个人棋谱、环境目录和 KataGo 模型；下载源码的读者按 [引擎安装说明](../katago_engine/INSTALL.md) 准备外部资源。
- 在 GitHub 的 About 中填写简短介绍，可使用 topics：`go-game`、`katago`、`reinforcement-learning`、`ppo`、`pytorch`、`fastapi`。

后续更新：修改文件 → 查看差异 → 暂存 → 提交 → **Push / Sync Changes**。若提示远程有新提交，先拉取并解决冲突，再推送；不使用 `--force` 绕过。

仓库公开展示与授予开源许可是不同的决定，目前不要添加没有依据的许可证徽章。若之后确定开放使用的许可，再单独加入 LICENSE，并保留第三方许可说明。
