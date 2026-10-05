import os
import time
import uuid
import torch
import numpy as np
import cgo
from resnet_model import DualHeadResNet
import config

def calculate_winner_from_2d(board_2d, size):
    # 极简数地逻辑保持不变
    board = np.array(board_2d)
    black_score = np.sum(board == 1)
    white_score = np.sum(board == -1)
    visited = set()
    for r in range(size):
        for c in range(size):
            if board[r, c] == 0 and (r, c) not in visited:
                queue = [(r, c)]
                region = set()
                surrounding_colors = set()
                while queue:
                    curr_r, curr_c = queue.pop(0)
                    if (curr_r, curr_c) in visited: continue
                    visited.add((curr_r, curr_c))
                    region.add((curr_r, curr_c))
                    for dr, dc in [(-1,0), (1,0), (0,-1), (0,1)]:
                        nr, nc = curr_r + dr, curr_c + dc
                        if 0 <= nr < size and 0 <= nc < size:
                            if board[nr, nc] == 0 and (nr, nc) not in visited:
                                queue.append((nr, nc))
                            elif board[nr, nc] != 0:
                                surrounding_colors.add(board[nr, nc])
                if len(surrounding_colors) == 1:
                    color = list(surrounding_colors)[0]
                    if color == 1: black_score += len(region)
                    elif color == -1: white_score += len(region)
    return 1 if black_score > white_score + 3.5 else -1

def run_actor():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # 注意：传入 in_channels=9
    model = DualHeadResNet(board_size=config.BOARD_SIZE, in_channels=config.IN_CHANNELS, num_channels=config.NUM_CHANNELS, num_res_blocks=config.NUM_RES_BLOCKS).to(device)
    
    os.makedirs(config.DATA_DIR, exist_ok=True)
    print(f"⛏️  Actor 矿工启动！(9通道视觉版) 正在生产数据入库: {config.DATA_DIR}/")

    # 闭包：接收 C++ 算好的 9通道 1D 特征，直接送进 GPU
    def predict_fn(features_1d_list):
        features_3d = np.array(features_1d_list, dtype=np.float32).reshape((config.IN_CHANNELS, config.BOARD_SIZE, config.BOARD_SIZE))
        tensor_state = torch.tensor(features_3d).unsqueeze(0).to(device)
        with torch.no_grad():
            policy, value = model(tensor_state)
        return (policy[0].cpu().numpy().tolist(), value[0].item())

    game_count = 0
    while True:
        try:
            model.load_state_dict(torch.load(config.MODEL_PATH, map_location=device))
        except FileNotFoundError:
            pass 
        model.eval()

        env = cgo.FastGoBoard()
        mcts = cgo.FastMCTS(config.MCTS_SIMS, config.C_PUCT, predict_fn)
        train_examples = []
        step_count = 0

        while True:
            step_count += 1
            temp = 1.0 if step_count < 25 else 0.01 
            pi = mcts.get_action_prob(env, temp)
            
            # 直接提取 C++ 算好的完美特征图用于训练记录！
            features_1d = env.get_features()
            state = np.array(features_1d, dtype=np.float32).reshape((config.IN_CHANNELS, config.BOARD_SIZE, config.BOARD_SIZE))
            
            train_examples.append([state, pi, env.current_player])
            
            action = np.random.choice(len(pi), p=pi)
            env.step(action)
            
            if env.done or step_count > config.EPISODE_MAX_STEPS:
                winner = calculate_winner_from_2d(env.get_board_2d(), config.BOARD_SIZE)
                for x in train_examples:
                    x[2] = 1.0 if x[2] == winner else -1.0 
                break
        
        game_id = str(uuid.uuid4())[:8] 
        file_path = os.path.join(config.DATA_DIR, f"game_{game_id}.pt")
        torch.save(train_examples, file_path)
        
        game_count += 1
        print(f"Actor 生成了 1 局数据 ({step_count} 步) -> {file_path} | 总计产出: {game_count} 局")

if __name__ == "__main__":
    run_actor()