# 围棋模型训练：从旧自对弈到蒸馏、PPO 与实验收尾

[返回项目首页](README.md) · [产品路线](README-product.md)

本文按实际工作顺序记录需求、实现、采样诊断、训练、逐项优化与最终选择，再提供方法细节和复现说明。历史实验均已完成；当前阶段不再追加训练或评估。

目标是在单机预算内实践完整的预训练与后训练流程，形成可追溯、可解释的项目经历。这里的预训练是围棋领域的监督蒸馏；后训练是教师末局估值奖励下的在线 PPO，不是语言模型预训练、RLHF、GRPO 或完整 AlphaZero 复现。产品继续使用 KataGo，学生网络尚未部署到产品中。

## 阅读导航

- [按顺序回顾训练路线](#按顺序回顾训练路线)：每次调整的动机、结果和后续判断。
- [模型与训练方法](#模型与训练方法)：特征、损失、监督重放及价值梯度隔离。
- [复现与日常操作](#复现与日常操作)：环境、命令、预算、阶段完成标准。
- [日志、恢复与诊断](#日志恢复与诊断)：输出结构、恢复约束和核对材料。
- [证据索引与验证边界](#证据索引与验证边界)：本地报告位置与测试范围。

## 按顺序回顾训练路线

### 0. 原始起点：保留 C++ MCTS 自对弈路线

原项目采用 Actor/Learner 分工：`worker_selfplay.py` 调用 `cpp_src/fast_mcts.cpp` 中的棋盘和 MCTS，以神经网络提供策略与价值；生成的对局进入 `data_buffer/`。`worker_train.py` 读取文件数据池、执行 D4 增强并更新双头残差网络，权重写入 `best_model_fast.pth`。

现有旧配置为 9 路、9 通道输入、128 通道/5 个残差块、每步 400 次 MCTS、batch 256、每轮 200 次更新，经验池最多 100,000 状态。旧代码使用近似终局数子、白贴 3.5，并包含需要单独审计的搜索终局回传和低温度数值处理。保留它用于展示项目起点，不将其视为已经严格验证的训练基线。

旧脚本、C++ 源码、数据与权重均保留。现有 `cgo.cp312-win_amd64.pyd` 绑定 Windows/CPython 3.12，不能作为通用跨平台二进制。新路线不依赖该扩展，也不混用旧数据或直接加载旧 9 通道权重。

### 1. 明确实践目标，新增独立训练系统

在明确“重在完整训练实践，而非与 KataGo 竞争”后，新增 `train_v2.py`、`train_v2_common.py`、配置、依赖和专用日志，提供 `doctor / collect / pretrain / posttrain / validate / evaluate / report` 七个入口。最初只编写脚本和验证，不自动执行真实训练；后续逐阶段推进。

流程固定为：

```text
KataGo 教师自对弈与逐手标签
  → 按棋局去重、训练/验证分区
  → 四项监督的蒸馏预训练
  → 学生直接按策略自对弈、教师给末局奖励
  → 带冻结参考 KL 约束的 PPO
  → 教师标签验证 + 独立开局交叉执色评估
```

规则固定为 9 路、中国面积规则、全局同形禁着、禁止自杀、白贴 7.5。学生在 PPO 采样和评估落子时均不使用 MCTS；教师用于生成预训练标签和末局评价。开始即记录配置、环境、源码/教师哈希、随机状态与检查点，避免只留下一个无法解释来源的权重文件。

### 2. 首次 20 局：发现只保存黑方的采样缺陷

首次采集 20 局，耗时 **169.261 秒**，保存 **373 个状态，黑 373 / 白 0**。原因是 `sample_every=4` 与交替落子的奇偶性相互作用：每隔四手保存，始终落在同一种执棋方。

这批数据可以衡量采样成本，却不适合直接进入预训练。修复将默认设置改为 `sample_every=1`，逐手保存，并拒绝其他间隔；新增每局、续采汇总和训练/验证两个分区的黑白覆盖检查，日志加入 `color_counts` 与 `collection_summary`。旧 `teacher-first` 数据保留作问题证据，不参与后续训练。

训练专项测试由初始 15 项增至 20 项，覆盖完整保存、黑白标签符号、续采统计、旧配置拒绝及分区缺色拒绝。修复不只是调整一个配置，还把已出现的问题变成后续自动检查的约束。

### 3. 重采 20 局并验收，再扩到 200 局

修正后的 20 局耗时 **152.765 秒**，保存 **1,392 个状态，黑 702 / 白 690**。逐步合法回放、输入特征、概率掩码、标签范围和文件哈希检查通过；20 局均双停结束，无长度截断。但按棋局哈希分区后只有训练 19 局、验证 1 局，因此先扩采，不直接开始预训练。

沿用新数据目录补采 180 局，耗时 **1,313.537 秒，约 21 分 54 秒**，总数达到 200 后冻结：

| 项目                  |                              结果 |
| --------------------- | --------------------------------: |
| 唯一完整棋局 / 状态数 |                      200 / 14,308 |
| 黑方 / 白方状态       |                     7,215 / 7,093 |
| 训练棋局 / 状态       |                      170 / 12,145 |
| 验证棋局 / 状态       |                        30 / 2,163 |
| 每局手数              |     最少 39，最多 108，平均 71.54 |
| 终止方式              | 200 局全部双停，无 160 手上限截断 |

有 1 局第 2 手出现 pass，随后继续下到 55 手，不是双停立即结束。训练/验证标签的当前方价值均值约 0.0061/0.0035，但绝对价值超过 0.9 的比例约 61.5%/52.3%，需要注意标签偏向确定输赢的分布。

审计未发现完整棋局跨分区重复，但验证中 **193 行（约 8.9%，98 种特征状态）** 与训练共享开局，均发生在落子前索引 0～17。按棋局分区减少泄漏，不能消除不同棋局共有的早期局面；因此监督验证不能单独证明新局面泛化棋力。

冻结数据为 `train_v2_data/teacher-balanced/`。所有分片的哈希、来源、数值、合法回放和特征均验收通过。预训练开始后不再向该目录追加数据。

### 4. 首次预训练：建立可重复比较的起点

在 RTX 5080 Laptop GPU 上完成 **10 个 epoch、950 次优化更新**；日志总耗时 **12.764 秒**，不含解释器启动。网络仅 320,251 参数，本阶段直接使用已有标签，耗时远低于搜索采样；不能把这一速度外推到大模型或其他电脑。

| 教师验证指标       | 随机初始化 | 第 10 个 epoch |
| ------------------ | ---------: | -------------: |
| 总损失             |     4.7632 |         3.4295 |
| 策略软标签交叉熵   |     3.8917 |         2.8061 |
| 教师首选动作一致率 |      2.91% |         27.37% |
| 价值 MSE           |     0.7505 |         0.5983 |
| 目差 MAE           |  5.4381 目 |      5.0642 目 |
| 领地 MSE           |     1.1813 |         0.2280 |

`best.pt` 与 `latest.pt` 均位于第 10 个 epoch、950 步，模型参数有限，优化器进度正确。后续所有 PPO 方案均从此 `best.pt` 独立初始化。

训练说明了教师标签拟合改善，没有测得段位。末两轮改善变小，同时学习率正衰减，不能据此证明收敛。掩码前非法概率质量约 0.416→0.633；由于训练和行动始终使用合法掩码，这也不等于实际非法落子率。

### 5. 首轮 PPO：先诊断“很快完成”，再决定是否加轮数

用户完成默认 **8 局/轮 × 3 轮**，实际 24 局、1,942 状态、34 次更新，耗时 **27.303 秒**。日志、行为概率与检查点检查正常，没有 KL 提前停止、前 20 手 pass 或长度截断。

教师验证价值 MSE 升至 0.7384，目差 MAE 升至 6.237 目。补做 20 对/40 局监测评估，候选对预训练为 18 胜/1 平/21 负，教师裁定得分率 **46.25%**，配对 95% 区间 **32.5%～60%**。

结论是样本太少，未确认收益；既不能凭速度快继续堆轮数，也不能凭教师拟合退化断言实战显著变弱。下一步先测试每轮增加采样量。

### 6. 扩采到每轮 32 局：识别更新次数的混杂因素

仅把每轮 8 局改为 32 局，从同一预训练模型重开 3 轮。完成 96 局、8,013 状态、**111 次更新**，耗时 **82.355 秒**。第一轮近似旧策略 KL 达到 0.0554，触发既有 0.03 阈值而提前停止该轮后续更新；其余两轮完成，未出现早盘停着或截断。

教师目差 MAE 6.677 目、价值 MSE 0.7497；相同 20 对监测开局为 21 胜/2 平/17 负，得分率 **55%（40%～70%）**，仍未确认强度提升。

诊断发现扩采同时使更新次数从 34 增至 111，不能将差异完全归因于数据量。于是保留 32 局/轮，下一项只增加 batch，以接近原来的更新预算。

### 7. batch 128→512：小样本高分经扩大评估回落

完成 96 局、8,296 状态、**36 次更新**，耗时 **96.711 秒**。没有 KL 提前停止、早盘停着或截断。教师目差 MAE 为 6.050 目、价值 MSE 0.6543，相比每轮 32 局/batch 128 的拟合退化有所减轻。

原 40 局监测集曾达到 **66.25%（48.75%～82.5%）**；另取种子 20271005 的 100 对/200 局，结果为 100 胜/4 平/96 负，即 **51%（43.75%～58%）**，没有确认收益。此后这批开局作为开发评估集使用，重复比较时不再称为全新测试。

这一步保留了更可比较的更新预算，也说明小样本高分不能直接用于挑选模型或写性能提升结论。

### 8. 加入教师监督重放 0.1：保留能力的目标未达成

新增可选监督重放，仅从教师训练分区抽样，在 PPO 之外监督策略、目差与领地，不加入教师价值目标。它不进入 PPO 行为概率比；权重、数据清单纳入恢复检查，默认关闭。新增 5 项测试，专项测试达到 25 项。

从同一预训练起点完成 96 局、8,349 状态、36 次更新，耗时 **83.899 秒**，逐步日志确认辅助损失实际参与更新。教师目差 MAE **7.181 目**、价值 MSE **0.8329**，没有达成预期的能力保留目标。开发集 200 局为 **48.25%（41.25%～55.25%）**；相对无监督 batch 512 的差为 -2.75 个百分点，区间 -13.25～+8，不足以判定显著强弱。

局部初始梯度测量中，加权价值梯度范数约为监督重放的 13～30 倍，提示共享表示可能受到较强价值更新影响。该测量只描述少量批次，不证明退化原因。基于这个线索，先测试降低价值权重，再考虑梯度隔离。

本节记录当时的负结果。后来统一比较中该检查点得到较高对局分数，最终被保留为 PPO 候选，不能把早期诊断或后期高分单独当作完整结论。

### 9. 价值权重 0.5→0.1：拟合保留与回报学习存在取舍

关闭教师重放，相对 batch 512 对照只将 `ppo_value_weight` 从 0.5 改为 0.1。完成 96 局、8,124 状态、36 次更新，耗时 **82.705 秒**，无 KL 提前停止、早盘停着或截断。

价值 MSE 改善到 0.6408、目差 MAE 到 5.736 目，但开发集 200 局得分 **44%（37.5%～50.5%）**，另取种子 20281006 的 200 局为 **45.75%（39.25%～52.5%）**。相对原权重开发集 51%，差值区间仍包含零。

在各自当轮 rollout 上，较小价值权重也减弱了对当前回报的拟合，提示减少扰动与价值学习之间的取舍；不同轮次的数据分布并不相同，不能由此确认对局退化的因果。没有继续降低权重或延长轮数，下一项改为隔离梯度。

### 10. 独立 critic：保留目差能力，但仍需对局验证

新增 `--isolated-critic`：复制预训练价值映射，令其读取 `trunk.detach()`，阻断价值损失进入共享骨干；保留原 `value_score` 分支提供目差输出。初始预测保持一致，新增 **4,225 参数，总计 324,476**。策略仍可更新骨干，全局梯度裁剪仍共用，因此不是完全独立优化器，也不是严格等参数量对照。

恢复时检查独立分支标记；旧检查点仍兼容，拒绝静默关闭该开关。新增 5 项测试，专项测试达到 30 项。

保持价值权重 0.5、关闭监督重放，完成 96 局、8,397 状态、36 次更新，耗时 **83.994 秒**。原 `value_score` 参数逐轮与预训练完全一致；没有 KL 提前停止、早盘停着或截断。教师价值 MSE **0.5900**，目差 MAE **5.116 目**，接近预训练 5.064 目；领地误差略升，说明共享特征改变仍会影响其他输出。

### 11. 六方案统一比较：固定检查点，统一开局与对手

前面不同样本上的分数不能直接排榜。最终预先固定各方案第三轮检查点，用全新种子 **20291007** 的共同 100 对开局，每个候选对同一个预训练基线各 200 局，合计 **1,200 局**。不是六个候选互相循环赛；旧方案训练预算也并不完全相同。

双方贪心落子、不加搜索，每对使用同一个四手合法随机开局并交换黑白；末局 KataGo 以 128 visits 预算的目差评价裁定，候选领先超过 0.5 目计 1 分，落后超过 0.5 目计 0 分，中间计 0.5。下表的“胜/平/负”均由该教师估值产生，属于**教师裁定得分率**，不是正式棋力评级。

| PPO 方案                 | 训练局数 / 更新数 |           得分率 | 配对 95% 区间  | 教师价值 MSE / 目差 MAE |
| ------------------------ | ----------------: | ---------------: | -------------- | ----------------------- |
| 8 局/轮，batch 128       |           24 / 34 |           42.25% | 36.5%～48%     | 0.7384 / 6.237 目       |
| 32 局/轮，batch 128      |          96 / 111 |           43.25% | 37%～49.5%     | 0.7497 / 6.677 目       |
| 32 局/轮，batch 512      |           96 / 36 |           49.75% | 44%～55.75%    | 0.6543 / 6.050 目       |
| batch 512 + 教师监督 0.1 |           96 / 36 | **58.75%** | 52.75%～64.75% | 0.8329 / 7.181 目       |
| batch 512 + 价值权重 0.1 |           96 / 36 |           44.75% | 38.25%～51.25% | 0.6408 / 5.736 目       |
| batch 512 + 独立 critic  |           96 / 36 | **56.75%** | 50.5%～63%     | 0.5900 / 5.116 目       |

预训练是固定参考，教师价值 MSE 0.5983、目差 MAE 5.064 目，不另造“自我对局 50%”的测量值。验证统一使用 2,163 状态、FP32、batch 128。

教师监督比独立 critic 观察得分高 2 个百分点，但配对差区间 **-6.75～+10.75 个百分点**，无法确认谁更强。额外以 20,000 次配对 bootstrap 对六个候选相对基线的比较做 Bonferroni 调整，教师监督区间约 50.58%～66.50%，独立 critic 为 48.50%～65%。这只是本批开局上的近似证据，不覆盖训练种子差异。

所有 1,200 局双停结束，评估日志耗时 819.505 秒；完整跑满，没有按中途得分换检查点或提前停止。新开局与先前评估无完全相同的序列，候选、参考、教师和冻结数据哈希核对通过；零完全重合不等于局面分布完全不同。

### 12. 最后一批复核与当前预算下的停止决定

考虑教师监督较早只有 48.25%，仅对观察排名靠前的两个固定候选再取全新种子 **20301008**，各 100 对/200 局，之后按预定计划停止。

| 候选         | 教师裁定胜 / 平 / 负 |           得分率 | 配对 95% 区间  |
| ------------ | -------------------- | ---------------: | -------------- |
| 教师监督 0.1 | 110 / 5 / 85         | **56.25%** | 49.75%～62.75% |
| 独立 critic  | 98 / 1 / 101         |           49.25% | 42.75%～55.5%  |

两候选对同一参考的得分差为 7 个百分点，区间 **-1.75～+15.5 个百分点**，仍不能证明显著强弱；两项参考比较的 Bonferroni 调整区间也均包含 50%。全部 400 局双停结束，新开局无完全重复。最后两阶段共 1,600 局，评估日志耗时 **1,119.106 秒，约 18 分 39 秒**。

教师监督三批各 200 局的结果为 **48.25%、58.75%、56.25%**，600 局描述性均值约 54.42%；该汇总包含开发和候选筛选数据，不能当作又一次独立确认。候选筛选后新批次的区间仍跨过 50%，不能只展示最好一批宣称稳定提升。

| 最终角色     | 保留检查点                                                 | 选择理由                                         |
| ------------ | ---------------------------------------------------------- | ------------------------------------------------ |
| PPO 对弈候选 | `train_v2_runs/11-ppo32-batch512-replay01/round-0003.pt` | 共同评估与最终复核的观察得分均领先，仍有不确定性 |
| 稳定基线     | `train_v2_runs/02-pretrain/best.pt`                      | 教师拟合整体更好，保留供未来比较，不覆盖         |
| 能力保留消融 | `train_v2_runs/16-ppo-isolated-critic/round-0003.pt`     | 目差预测接近预训练，体现梯度路由的取舍           |

默认配置和产品模型不自动替换。教师监督候选的估值能力退化，不能因对局观察分数较高就用于产品复盘。

**阶段到此收尾。** 每个配置只有一个训练种子、三轮 PPO，没有系统扩大数据、网络和训练长度。“手动收敛”在这里准确指当前预算和实践目标下的停止决定，不证明数学收敛、全局最优或更多算力无效。将来有明确新假设、独立数据或新预算再另开实验，不为追逐漂亮分数继续重复评估。

## 模型与训练方法

### 输入与输出

默认残差网络宽度 64、4 个残差块，GroupNorm + SiLU，不使用依赖小批次统计的 BatchNorm。输入的 11 个平面按以下顺序排列：

- 己方棋子、对方棋子；所有在盘棋子的 1 气 / 2 气 / 3 气及以上三个平面。
- 合法落子平面（pass 另占策略第 82 项）；前一、前二棋盘，按当前落子方编码为 ±1。
- 当前是否黑方；当前方视角的贴目补偿 `-player × 7.5 / 81`；连续停着次数除以 2。

四个头分别输出 82 个策略 logits、局面价值 [-1,1]、归一化目差 [-1,1]、9×9 领地预测 [-1,1]。非法落点在采样、训练和评估时均掩码；日志的 `raw_illegal_mass` 衡量掩码前的概率质量，不表示实际走了非法棋。

可选的 `posttrain --isolated-critic` 会复制预训练价值映射为独立分支，以 `trunk.detach()` 阻断其梯度进入共享骨干，并与原 value_score 分支分开。初始预测保持一致，增加 4,225 个参数，输出接口仍为上述四项。策略仍训练骨干，因此目差与领地的输入特征仍会变化；全局梯度裁剪仍共用，未实现完全分离的优化器。该开关用于最后一项对照，效果见前文统一比较，不代表默认开启。

新检查点记录独立分支标记，旧检查点默认按原结构加载。恢复独立分支训练必须保持 `--isolated-critic`；关闭开关或把该检查点用于普通预训练会被拒绝，避免无意改变训练目标。旧模型和默认配置不受影响。

### 预训练目标

教师引擎配置必须明确是 `reportAnalysisWinratesAs = BLACK`，程序会校验。教师策略取候选落点访问次数归一化；其他标签从黑方视角统一转换到**当前落子方**：

- `value = player × (2 × black_winrate − 1)`；player：黑为 +1，白为 -1。
- `score = clip(player × black_score_lead / 81, −1, 1)`。
- `ownership = player × black_ownership`。
- 总损失：策略软标签交叉熵 + 1.0×价值 MSE + 0.25×目差 MSE + 0.1×领地 MSE。

AdamW，学习率 3e-4，weight decay 1e-4，batch 128，前 10% 步数 warmup，之后 cosine 到初始学习率的 10%，梯度范数裁剪 1。每个训练样本随机采用 D4 的旋转 / 镜像，策略、掩码、历史特征与领地同时变换，pass 不变。

CUDA 默认使用混合精度：支持时 BF16，否则 FP16 + GradScaler；CPU 用 FP32。验证使用 FP32。保存随机状态使恢复路径可追踪，但 CUDA 算子、KataGo 搜索和不同驱动下不保证位级一致。

### 后训练目标

自对弈每步保存实际行为动作概率 `old_logp` 和 `old_value`。整局末尾教师给出黑方软奖励 `r_B=2p_B−1`，第 t 步的回报为 `R_t=player_t×r_B`，折扣 gamma=1，优势是 `R_t−V_old(s_t)`，再在整批中标准化。这里用终点估值回报，没有额外的学习奖励模型，也没有实现 GAE。

策略概率比 `ratio=exp(logπ_new(a|s)−old_logp)`；优化目标为 PPO clip 策略损失 + 0.5×价值 MSE + 0.05×KL(当前策略 || 冻结预训练策略) − 0.01×策略熵。clip=0.2，学习率 3e-5，最多 2 个 epoch；近似旧策略 KL 大于 0.03 时停止继续更新该轮。

**PPO 不对状态做 D4 随机增强**，因为缓存的行为概率对应原始状态，随意旋转会破坏概率比。每轮收集期间策略保持不变；梯度更新只使用本轮数据，历史 rollout 仅供审计，不作为离策略经验池。

默认 PPO 使用冻结参考模型约束行为；价值头继续训练，目差 / 领地没有辅助监督，共享骨干变化也可能导致它们退化，因此要执行前后 `validate`。

可选教师监督重放：在 `posttrain` 后同时指定 `--replay-data train_v2_data/teacher-balanced --replay-weight 0.1`。每次实际 PPO 更新，从教师**训练分区**独立抽取与当前 PPO minibatch 等量的样本，有放回抽样，不使用验证分区。追加损失为 `0.1 × (策略 CE + 0.25 × 目差 MSE + 0.1 × 领地 MSE)`；不包含教师价值监督，以免直接混淆学生回报与教师价值目标。价值与目差仍共用部分网络，未实现完全分离的价值网络。

教师监督单独前向，不混入 PPO 概率比，不改变行为概率，也不对 PPO 状态做增强。监督抽样使用独立的逐轮 RNG；日志记录各项 replay 损失及相加前的 `ppo_loss`，原 `loss` 记录总损失。监督权重及完整数据清单进入恢复校验；恢复时不能悄悄换权重、数据或关闭监督。默认不带这两个参数时保持原 PPO 行为与检查点约定不变。

教师监督重放经过独立试验和统一比较后，被保留为最终 PPO 候选，但默认配置不自动改变。下面是历史执行命令，仅供核对；重新实验必须更换 `--run`，只有恢复同一实验才加 `--resume`，并保留所有参数：

```powershell
.\.venv\Scripts\python.exe train_v2.py --config train_v2_config_ppo32_batch512.json --run train_v2_runs/11-ppo32-batch512-replay01 --device cuda --max-minutes 10 posttrain --init train_v2_runs/02-pretrain/best.pt --rounds 3 --replay-data train_v2_data/teacher-balanced --replay-weight 0.1
```

终点教师估值减少了错误死活计分的问题，但它依赖强教师对后续的预期。弱学生可能通过过早停着或进入老师看好但自己不会下的局面获得偏高奖励。必须联合查看前 20 手出现 pass 的局比例 `early_pass_rate`、截断率 `truncation_rate`、验证退化与对照对局，不能只看奖励或 PPO loss。

## 复现与日常操作

### 环境与成本

历史实测环境为 Windows、RTX 5080 Laptop GPU、Python 3.14.6、PyTorch 2.10.0+cu128、NumPy 2.5.2，使用已有 `.venv`。这是日志中的实际环境；`requirements-train-v2.txt` 中 NumPy 固定为 2.2.6，并不与历史环境完全一致。

为分离产品和训练依赖，原方案建议另建 64 位 Python 3.12 环境。下面保留该安装方案；独立 3.12 环境尚未在本机安装验收，不能声称它与历史运行逐位一致。PyTorch CUDA wheel 必须适配目标显卡和驱动，其他硬件先做环境检查和 20 局测量。

```powershell
py -3.12 -m venv .venv-train
.\.venv-train\Scripts\python.exe -m pip install --upgrade pip
.\.venv-train\Scripts\python.exe -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv-train\Scripts\python.exe -m pip install -r requirements-train-v2.txt
$trainPython = (Resolve-Path '.\.venv-train\Scripts\python.exe').Path
```

若已有同时安装 torch、numpy、sgfmill 的环境，可把 `$trainPython` 改为它的解释器。复制到新电脑时重建虚拟环境。教师需要 `katago_service.py` 的 `ENGINE_FILES` 指向有效的引擎、模型及配置，当前是产品目录中的本地 KataGo；配置必须明确 `reportAnalysisWinratesAs = BLACK`。

采集和 PPO 会调用真实引擎，建议关闭产品中持续运行的引擎，避免同时占用 GPU。新训练不写用户棋谱数据库，但复用的引擎通信模块仍将错误日志写入 `product_data/engine.log`。

### 从新目录复现一轮

以下使用 `reproduce-*` 新目录示范；已有同名实验时先换一组新名称。历史结果不需要重新启动。全局选项 `--config / --run / --device / --max-minutes` 写在子命令前；各条命令按阶段执行，验收后再继续。

**1. 环境检查。**

```powershell
& $trainPython train_v2.py --run train_v2_runs/reproduce-doctor --device cuda doctor
```

预期 CUDA 可用、`kernel_check=8`、三个教师文件均存在。此命令仅检查小矩阵计算和文件，不代表已完成真实引擎请求。默认 `auto` 可退回 CPU，显式 `--device cuda` 则在 GPU 不可用时报错。

**2. 先采 20 局，验收后再达到总计 200 局。**

```powershell
& $trainPython train_v2.py --run train_v2_runs/reproduce-collect --max-minutes 20 collect --data train_v2_data/reproduce-teacher --games 20
& $trainPython train_v2.py --run train_v2_runs/reproduce-collect --max-minutes 30 collect --data train_v2_data/reproduce-teacher --games 200
```

`--games` 是总目标，不是新增数量，第二条会保留前 20 局。每步教师搜索预算 64 visits，实际不足 16 拒绝；前 24 手温度 1，之后 0.25，最多 160 手。续采不需要 `--resume`，会检查已有分片与配置/教师身份。采满后确认训练和验证都有黑白样本及足够独立棋局，再冻结数据。程序每个分区至少 2 局只是运行下限，本次实践验收使用 30 局验证集。

**3. 预训练 10 个完整 epoch。**

```powershell
& $trainPython train_v2.py --run train_v2_runs/reproduce-pretrain --device cuda --max-minutes 30 pretrain --data train_v2_data/reproduce-teacher --epochs 10
```

检查每轮验证、`best.pt` 的实际 progress 和模型有限性。中断后用相同命令加 `--resume`，保持 `--epochs 10`，不要直接改变调度器总周期。

**4. 先运行基础 PPO；其他消融各用独立目录。**

```powershell
& $trainPython train_v2.py --run train_v2_runs/reproduce-ppo-base --device cuda --max-minutes 30 posttrain --init train_v2_runs/reproduce-pretrain/best.pt --rounds 3
```

下表是本次六方案的参数对应关系；所有对照都从同一预训练起点重开，并非沿上一方案继续训练。

| 方案                | `--config`                                    | `posttrain` 额外参数                                       |
| ------------------- | ----------------------------------------------- | ------------------------------------------------------------ |
| 8 局/轮、batch 128  | `train_v2_config.json`                        | 无                                                           |
| 32 局/轮、batch 128 | `train_v2_config_ppo32.json`                  | 无                                                           |
| 32 局/轮、batch 512 | `train_v2_config_ppo32_batch512.json`         | 无                                                           |
| 教师重放 0.1        | `train_v2_config_ppo32_batch512.json`         | `--replay-data <同一冻结教师数据目录> --replay-weight 0.1` |
| 价值权重 0.1        | `train_v2_config_ppo32_batch512_value01.json` | 无                                                           |
| 独立 critic         | `train_v2_config_ppo32_batch512.json`         | `--isolated-critic`                                        |

例如，复现最终保留方案与梯度隔离消融：

```powershell
& $trainPython train_v2.py --config train_v2_config_ppo32_batch512.json --run train_v2_runs/reproduce-ppo-replay --device cuda --max-minutes 30 posttrain --init train_v2_runs/reproduce-pretrain/best.pt --rounds 3 --replay-data train_v2_data/reproduce-teacher --replay-weight 0.1
& $trainPython train_v2.py --config train_v2_config_ppo32_batch512.json --run train_v2_runs/reproduce-ppo-isolated --device cuda --max-minutes 30 posttrain --init train_v2_runs/reproduce-pretrain/best.pt --rounds 3 --isolated-critic
```

`--rounds` 是总目标；恢复相同方案时加 `--resume`，保留 init、配置、replay 和 critic 开关。新超参数使用新配置和新 run，不覆盖原试验。

**5. 重算教师验证，再按预先确定的规模评估。**

```powershell
& $trainPython train_v2.py --run train_v2_runs/reproduce-val-pretrain validate --checkpoint train_v2_runs/reproduce-pretrain/best.pt --data train_v2_data/reproduce-teacher
& $trainPython train_v2.py --run train_v2_runs/reproduce-val-replay validate --checkpoint train_v2_runs/reproduce-ppo-replay/round-0003.pt --data train_v2_data/reproduce-teacher
```

独立开局的种子来自配置文件，不是命令行 `--seed`。复制配置并只改 seed，再用同一评估配置比较候选；下面示例种子用于新一轮流程，不能将其结果预先假定为历史结果：

```powershell
$config = Get-Content train_v2_config.json -Raw | ConvertFrom-Json
$config.seed = 20311009
$config | ConvertTo-Json | Set-Content train_v2_runs/reproduce-eval-config.json -Encoding utf8
& $trainPython train_v2.py --config train_v2_runs/reproduce-eval-config.json --run train_v2_runs/reproduce-eval-replay --device cuda --max-minutes 30 evaluate --candidate train_v2_runs/reproduce-ppo-replay/round-0003.pt --reference train_v2_runs/reproduce-pretrain/best.pt --pairs 100
```

前序命令会创建 `train_v2_runs/`。配置只写一次，恢复期间不改；评估相同命令可从完整配对继续，无需 `--resume`。变更候选、参考、教师、配置或配对总数时另开目录。20 对只能作探索性监测；正式比较应事先固定样本数和候选，不能中途见好就停。本项目独立开局复核还不等于多个训练种子的重复实验。

### 训练频率和阶段完成标准

无需每天定时训练；同样数据、目标与参数下，频率本身没有特殊收益。一次只推进一个可诊断的阶段，不并行运行多个占用同一 GPU 或写同一目录的任务。

| 阶段            | 本机实际成本           | 完成后检查                                                 |
| --------------- | ---------------------- | ---------------------------------------------------------- |
| 20 局校准       | 修正后约 2 分 33 秒    | 实际 visits、逐手合法性、黑白覆盖、分区、终止分布          |
| 补采 180 局     | 约 21 分 54 秒         | 唯一棋局、验证规模、分片/教师身份、开局重叠；然后冻结      |
| 10 epoch 预训练 | 日志约 12.8 秒         | 完整 epoch、best 非空、指标趋势、有限参数和恢复进度        |
| 3 轮 PPO        | 本次约 27～97 秒       | 三个 round 完成，实际更新数、KL、梯度、pass/截断、前后验证 |
| 100 对评估      | 单候选本次约 2～3 分钟 | 完整 200 局、相同开局交换黑白、区间、身份校验              |

上述为特定硬件和小网络的日志耗时，不包含所有进程启动/人工诊断时间，也不是其他机器的承诺。操作时可留 30 分钟软预算；`--max-minutes` 在完整局、epoch、round 或配对边界停止，不强行切断正在进行的更新，doctor/validate/report 不按它切断。

完成一个阶段是满足预定预算并通过数据/运行验收，不等于性能一定提升。预训练若连续几轮验证不改善或明显过拟合，先诊断；脚本没有自动早停回调。PPO 不能只看平均奖励或总 loss，更不能比较换过系数后的 loss 大小就声称优化有效。

## 日志、恢复与诊断

### 文件职责

| 文件 / 目录                                                           | 用途                                       |
| --------------------------------------------------------------------- | ------------------------------------------ |
| `train_v2.py`                                                       | 七个入口、采集、训练、推理评估与恢复流程   |
| `train_v2_common.py`                                                | 模型、特征、损失、数据格式、日志和检查点   |
| `train_v2_config*.json`                                             | 基础配置及本次三种参数对照                 |
| `training-v2-log.jsonl`                                             | 全局追加日志，记录各次执行与准备事件       |
| `train_v2_data/<名称>/dataset.json`、每局 `.npz`                  | 数据协议、教师身份、按局分片与标签         |
| `train_v2_runs/<名称>/events.jsonl`                                 | 该运行的详细事件，是诊断首选材料           |
| `latest.pt`、预训练 `best.pt`                                     | 最近完整进度、验证总损失最低的预训练检查点 |
| PPO`round-0001.pt` 等                                               | 每轮完整检查点，不自动等同“最好模型”     |
| `validation.json`、`evaluation.json`、`evaluation-summary.json` | 验证指标、配对明细和得分汇总               |
| `diagnosis-input.json`                                              | report 生成的最近事件摘要                  |

运行记录包含完整参数、Python/PyTorch/NumPy/CUDA/GPU 信息、源码 SHA256、教师 exe/模型/配置 SHA256、数据清单和分片哈希。检查点还保存优化器、混合精度状态、随机状态、训练进度、参考模型身份；加载使用 `weights_only=True`。固定种子和恢复状态提高可追溯性，但不保证不同 CUDA 算子、驱动与引擎搜索的位级一致。

关键事件：`collected_game`、`collection_summary`、`dataset_loaded`、`validation_initial`、`train_step`、`epoch_complete`、`rollout_summary`、`ppo_step`、`ppo_kl_stop`、`round_complete`、`validation_result`、`evaluation_summary`、`budget_pause`、`error`。训练批指标默认每 20 step 记录，每 epoch 有完整验证。

```powershell
& $trainPython train_v2.py --run train_v2_runs/reproduce-report report --source train_v2_runs/reproduce-ppo-replay
```

摘要不替代完整趋势。提供 run 目录即可在本地诊断；转交其他机器时至少提供配置、`events.jsonl`、验证/评估 JSON 和相关计划，不必先传全部权重。全局 JSONL 自行读取时可用 `utf-8-sig` 兼容初始 BOM。

诊断顺序：先看运行是否完整，再看数据/视角和更新预算，接着检查熵、行为概率、KL/裁剪、价值拟合、梯度、停着和截断，最后结合统一评估判断是否保留。高奖励、loss 下降和教师指标改善都不能单独替代对局证据。

### 恢复边界

- 采集按完整棋局续采；预训练和 PPO 只保证最后完整 epoch/round 的恢复点。中断日志中的 step 不等于已保存进度。
- 恢复必须保持配置、冻结数据、初始参考和教师身份一致；重放权重/数据及独立 critic 开关也属于协议。
- 每个 run 同时只运行一个命令，同一数据目录只用一个采集进程。预算暂停后确认原进程结束再恢复。
- 强制结束可能留下 `active.lock`；先核对其中 PID 已停止，才清理该 run 的锁文件，不删除检查点和数据。
- 新训练目标、数据、网络或超参数一律另开 run；不要把有限实验续跑成不可比较的无限循环。

## 证据索引与验证边界

以下路径均相对项目根目录，指本机已归档材料。`train_v2_runs/` 与 `train_v2_data/` 被 `.gitignore` 排除，GitHub 源码读者不应假定它们随仓库提供；本文已保留关键参数和结果。需要独立审计时再提供去除个人路径等信息的实验材料。

| 阶段             | 本地证据入口                                                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 初始采样缺陷     | `train_v2_runs/01-collect/sampling-diagnosis-20games.md`                                                                                 |
| 修正后 20/200 局 | `train_v2_runs/01-collect-balanced/sampling-check-20games.json`、`sampling-check-200games.json`                                        |
| 预训练           | `train_v2_runs/02-pretrain/pretraining-summary.md` 与同目录 JSON / events                                                                |
| 默认 PPO         | `train_v2_runs/03-ppo/diagnosis-round3.md`；评估 `06-eval-ppo3-diagnosis/`                                                             |
| 32 局/轮         | `train_v2_runs/04-ppo32/README-experiment.md`、`optimization-assessment.md`；评估 `07-eval-ppo32-diagnosis/`                         |
| batch 512        | `train_v2_runs/08-ppo32-batch512/README-experiment.md`；评估 `09-eval-batch512-monitor/`、`10-eval-batch512-independent/`            |
| 教师监督         | `train_v2_runs/11-ppo32-batch512-replay01/README-experiment.md`；评估 `12-eval-replay01-independent/`                                  |
| 价值权重         | `train_v2_runs/13-ppo32-batch512-value01/README-experiment.md`；评估 `14-eval-value01-development/`、`15-eval-value01-confirmation/` |
| 独立 critic      | `train_v2_runs/16-ppo-isolated-critic/README-experiment.md`                                                                              |
| 统一比较         | `train_v2_runs/17-final-comparison/README-comparison.md`、`plan.json`、`comparison.json`                                             |
| 最终复核         | `train_v2_runs/18-final-confirmation/README-confirmation.md`、`confirmation.json`、`final-selection.json`、`verification.json`     |

历史报告中的“尚未执行”表示当时状态；后续是否执行及最终结论以本文时间线和最终选择记录为准。新增监督前的源码保存在 `08-ppo32-batch512/source-before-replay/`，隔离前源码保存在 `16-ppo-isolated-critic/source-before-isolation/`。

最终已通过 **30 项训练专项测试**：原 20 项覆盖黑白采样、续采、分区、特征、空间变换、掩码、教师标签、PPO 公式、检查点/RNG、恢复校验与仅推理验证；监督重放和独立 critic 各增加 5 项。测试用临时数据、教师替身与 CPU 运算，部分执行反向传播，但不调用 optimizer.step 或真实引擎。实际训练和评估审计另行记录，不能把单测通过当作性能收益。

```powershell
& $trainPython -m unittest discover -s tests -p test_training_v2.py -v
```

当前可用于项目经历的成果是：完整的多任务蒸馏与 PPO 流程、数据质量修复、六组可追溯对照，以及在不确定性下作出模型保留决定。尚未完成多训练种子、严格终局计分、系统规模扩展或学生模型产品部署。
