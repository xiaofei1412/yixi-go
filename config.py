BOARD_SIZE = 9
IN_CHANNELS = 9

MODEL_PATH = "best_model_fast.pth"
DATA_DIR = "data_buffer"

# --- 矿工 (Actor) 参数 ---
MCTS_SIMS = 400        
C_PUCT = 1.5           
EPISODE_MAX_STEPS = 120

# --- 大脑 (Learner) 参数升级 ---
NUM_CHANNELS = 128     # 模型变宽
NUM_RES_BLOCKS = 5     # 模型变深
BATCH_SIZE = 256       # 批量翻倍
LR = 0.0005             # 降低初始学习率，使训练更平稳
TRAINING_STEPS = 200   # 每次抽取数据进行多少次梯度下降（替代原本的 Epoch）
MIN_GAMES_TO_TRAIN = 50 
MAX_BUFFER_STATES = 100000 # 内存池最大容量，限制在大约 10 万个局面防爆内存