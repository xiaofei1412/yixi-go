import os
import glob
import time
import random
import torch
import torch.optim as optim
import torch.nn.functional as F
import numpy as np
from collections import deque
from resnet_model import DualHeadResNet
import config

def get_augmented_data(state, pi):
    """
    围棋数据的 D4 对称性增强。
    随机进行旋转或翻转，使得一份数据产生 8 种变化。
    """
    # pi 的最后一个元素是 Pass 的概率，先分离出来
    pi_board = np.array(pi[:-1]).reshape((config.BOARD_SIZE, config.BOARD_SIZE))
    pi_pass = pi[-1]

    # 随机选择一种旋转 (0, 1, 2, 3 次 90 度)
    k = random.randint(0, 3)
    aug_state = np.rot90(state, k, axes=(1, 2))
    aug_pi_board = np.rot90(pi_board, k, axes=(0, 1))

    # 随机选择是否镜像翻转
    if random.random() > 0.5:
        aug_state = np.flip(aug_state, axis=2)
        aug_pi_board = np.flip(aug_pi_board, axis=1)

    # 把 pi 重新拼回去
    aug_pi = np.append(aug_pi_board.flatten(), pi_pass)
    return np.ascontiguousarray(aug_state), np.ascontiguousarray(aug_pi)

def run_learner():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # 使用升级后的宏大网络结构
    model = DualHeadResNet(
        board_size=config.BOARD_SIZE, 
        in_channels=config.IN_CHANNELS,
        num_channels=config.NUM_CHANNELS,
        num_res_blocks=config.NUM_RES_BLOCKS
    ).to(device)
    
    if os.path.exists(config.MODEL_PATH):
        try:
            model.load_state_dict(torch.load(config.MODEL_PATH, map_location=device))
            print("✅ 已加载现有模型，在此基础上继续强化！")
        except RuntimeError:
            print("\n⚠️ 警告：检测到模型结构已升级（变深变宽），自动重置权重，从头开始训练！\n")
    
    optimizer = optim.Adam(model.parameters(), lr=config.LR, weight_decay=1e-4)
    
    # 内存驻留经验池 (只存解包后的局面状态)
    memory_buffer = deque(maxlen=config.MAX_BUFFER_STATES)
    loaded_files = set() # 记录已经读过的文件，绝不重复读取！
    
    print(f"🧠 Learner 大脑启动！(高性能驻留版) 正在监控 {config.DATA_DIR}/...")

    train_step = 0
    while True:
        # 1. 扫描文件并找出新产生的数据
        all_files = set(glob.glob(os.path.join(config.DATA_DIR, "*.pt")))
        new_files = list(all_files - loaded_files)
        
        if len(new_files) > 0:
            print(f"发现 {len(new_files)} 个新对局文件，正在加载入内存池...")
            for f in new_files:
                try:
                    data = torch.load(f, weights_only=False)
                    memory_buffer.extend(data)
                    loaded_files.add(f)
                except Exception as e:
                    pass
        
        # 2. 检查内存池是否足够肥沃
        if len(memory_buffer) < 2000: # 至少攒够约 30 局再开训
            print(f"当前内存池状态数: {len(memory_buffer)}，等待矿工输送数据...")
            time.sleep(10)
            continue

        print(f"\n[{train_step}] 🚀 发起训练！当前内存池容量：{len(memory_buffer)} 个局面。")
        model.train()
        total_loss, total_v_loss, total_p_loss = 0.0, 0.0, 0.0
        
        # 3. 定量采样训练 (彻底告别卡死)
        for step in range(config.TRAINING_STEPS):
            # 随机抽出一个 Batch 的数据
            batch_data = random.sample(memory_buffer, config.BATCH_SIZE)
            
            states, target_pis, target_vs = [], [], []
            for x in batch_data:
                # 动态进行数据增强
                aug_s, aug_pi = get_augmented_data(x[0], x[1])
                states.append(aug_s)
                target_pis.append(aug_pi)
                target_vs.append([x[2]])
            
            # 转为 Tensor 送入显卡
            states_t = torch.FloatTensor(np.array(states)).to(device)
            target_pis_t = torch.FloatTensor(np.array(target_pis)).to(device)
            target_vs_t = torch.FloatTensor(np.array(target_vs)).to(device)
            
            out_pi, out_v = model(states_t)
            
            value_loss = F.mse_loss(out_v, target_vs_t)
            policy_loss = -torch.mean(torch.sum(target_pis_t * torch.log(out_pi + 1e-8), dim=1))
            loss = value_loss + policy_loss
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            total_v_loss += value_loss.item()
            total_p_loss += policy_loss.item()
            
            if (step + 1) % 50 == 0:
                print(f"  Step {step+1}/{config.TRAINING_STEPS} | 综合Loss: {loss.item():.4f} (策略: {policy_loss.item():.4f}, 价值: {value_loss.item():.4f})")

        # 4. 保存模型并清理旧文件防爆硬盘
        torch.save(model.state_dict(), config.MODEL_PATH)
        print(f"🎉 训练完成！最新权重已保存。平均 Loss: {total_loss/config.TRAINING_STEPS:.4f}")
        train_step += 1
        
        # 定期清理物理硬盘上过老的对局文件
        if len(loaded_files) > config.MAX_BUFFER_STATES // 50:
            sorted_files = sorted(list(loaded_files), key=os.path.getmtime)
            for f in sorted_files[:-2000]: # 硬盘上只保留最新的 2000 局
                try:
                    os.remove(f)
                    loaded_files.remove(f)
                except Exception:
                    pass
        
        time.sleep(5) 

if __name__ == "__main__":
    run_learner()