import torch
import torch.nn as nn
import torch.nn.functional as F

class ResBlock(nn.Module):
    """
    残差块 (Residual Block)：AlphaZero 架构的核心组件。
    通过 skip connection (跳跃连接) 解决网络加深时的梯度消失问题。
    """
    def __init__(self, num_channels):
        super(ResBlock, self).__init__()
        # 3x3 卷积，padding=1 保证特征图大小不变 (9x9)
        self.conv1 = nn.Conv2d(num_channels, num_channels, kernel_size=3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(num_channels)
        self.conv2 = nn.Conv2d(num_channels, num_channels, kernel_size=3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(num_channels)

    def forward(self, x):
        residual = x
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += residual  # 将输入直接加到输出上 (Skip Connection)
        out = F.relu(out)
        return out

class DualHeadResNet(nn.Module):
    """
    双头残差网络：接收当前棋盘状态，输出落子概率分布和局面胜率评估。
    """
    def __init__(self, board_size=9, in_channels=9, num_channels=64, num_res_blocks=3):
        super(DualHeadResNet, self).__init__()
        self.board_size = board_size
        self.action_size = board_size * board_size + 1  # 81个交叉点 + 1个Pass
        
        # 1. 初始卷积层 (特征提取起点)
        self.conv_initial = nn.Conv2d(in_channels, num_channels, kernel_size=3, padding=1, bias=False)
        self.bn_initial = nn.BatchNorm2d(num_channels)
        
        # 2. 骨干网络 (Backbone)：堆叠残差块
        self.res_blocks = nn.ModuleList([ResBlock(num_channels) for _ in range(num_res_blocks)])
        
        # 3. 策略头 (Policy Head)：预测下一步该下哪 (输出概率分布)
        self.policy_conv = nn.Conv2d(num_channels, 2, kernel_size=1, bias=False) # 降维到2个通道
        self.policy_bn = nn.BatchNorm2d(2)
        self.policy_fc = nn.Linear(2 * board_size * board_size, self.action_size)
        
        # 4. 价值头 (Value Head)：评估当前局面的胜率 (输出标量)
        self.value_conv = nn.Conv2d(num_channels, 1, kernel_size=1, bias=False)  # 降维到1个通道
        self.value_bn = nn.BatchNorm2d(1)
        self.value_fc1 = nn.Linear(1 * board_size * board_size, 64)
        self.value_fc2 = nn.Linear(64, 1)

    def forward(self, x):
        # x 的形状要求: (Batch_Size, Channels, 9, 9)
        
        # 通过初始层
        x = F.relu(self.bn_initial(self.conv_initial(x)))
        
        # 通过残差骨干网络
        for block in self.res_blocks:
            x = block(x)
            
        # --- 策略头计算 ---
        p = F.relu(self.policy_bn(self.policy_conv(x)))
        p = p.view(p.size(0), -1)  # 展平: (Batch_Size, 2 * 9 * 9)
        p = self.policy_fc(p)
        # 使用 Softmax 将输出转化为概率分布 P(s, a)
        policy_out = F.softmax(p, dim=1) 
        
        # --- 价值头计算 ---
        v = F.relu(self.value_bn(self.value_conv(x)))
        v = v.view(v.size(0), -1)  # 展平: (Batch_Size, 1 * 9 * 9)
        v = F.relu(self.value_fc1(v))
        v = self.value_fc2(v)
        # 使用 Tanh 将输出压缩到 [-1, 1] 区间，代表输赢评估
        value_out = torch.tanh(v) 
        
        return policy_out, value_out

if __name__ == "__main__":
    # 模拟一个 Batch Size = 8 的输入数据
    # 形状: (Batch=8, Channels=4, Height=9, Width=9)
    dummy_input = torch.randn(8, 4, 9, 9)
    
    # 实例化模型
    model = DualHeadResNet(board_size=9, in_channels=4, num_channels=64, num_res_blocks=3)
    
    # 将模拟数据喂给模型
    policy, value = model(dummy_input)
    
    print(f"输入张量形状: {dummy_input.shape}")
    print(f"策略头 (Policy) 输出形状: {policy.shape} (应该为 [8, 82])")
    print(f"价值头 (Value) 输出形状: {value.shape} (应该为 [8, 1])")
    
    # 检查策略头的概率和是否为 1
    sum_probs = torch.sum(policy[0]).item()
    print(f"第一个样本的落子概率总和: {sum_probs:.4f}")