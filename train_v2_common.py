"""Shared, independently versioned components for the educational training route."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from go_game import Board, Game, action_from_coordinate

ROOT = Path(__file__).resolve().parent
SCHEMA = 1
CHANNELS = 11
RULES_ID = 'Chinese-positional-no-suicide-komi7.5-v2'
LOG = ROOT / 'training-v2-log.jsonl'
ARRAY_KEYS = ('x', 'legal', 'policy', 'value', 'score', 'ownership')


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def config_load(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    defaults = json.loads((ROOT/'train_v2_config.json').read_text(encoding='utf-8-sig'))
    if set(config) != set(defaults):
        raise ValueError('配置字段与 train_v2_config.json 不一致，请复制完整配置后修改')
    if config['board_size'] != 9 or config['komi'] != 7.5:
        raise ValueError('此轮实验固定 9 路、中国面积规则、7.5 贴目；变更需新数据协议')
    positive = ['channels','blocks','teacher_visits','judge_visits','min_teacher_visits','max_moves',
                'sample_every','max_dataset_positions','batch_size','log_every','ppo_games_per_round','ppo_epochs']
    for key in positive:
        if type(config[key]) is not int or config[key] <= 0:
            raise ValueError(f'{key} 必须为正整数')
    if config['sample_every'] != 1:
        raise ValueError('当前教师采样必须设置 sample_every=1，逐手保存以确保黑白双方覆盖；请使用新数据目录')
    if config['channels'] % 8 or config['batch_size'] < 2 or config['max_moves'] < 2:
        raise ValueError('channels 必须是 8 的倍数，batch_size/max_moves 至少为 2')
    if not 0 < config['validation_fraction'] < .5 or not 0 < config['late_temperature'] <= 1:
        raise ValueError('验证比例需在 (0,0.5)，后半盘温度需在 (0,1]')
    if config['min_teacher_visits'] > min(config['teacher_visits'],config['judge_visits']):
        raise ValueError('最少教师搜索次数不能大于预算')
    for key in ('pretrain_lr','ppo_lr','grad_clip','ppo_clip','ppo_target_kl'):
        if not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError(f'{key} 必须为正数')
    for key in ('weight_decay','value_weight','score_weight','ownership_weight','reference_kl_weight','entropy_weight','ppo_value_weight'):
        if not math.isfinite(config[key]) or config[key] < 0:
            raise ValueError(f'{key} 不能为负数')
    if not 0 <= config['warmup_fraction'] < 1 or not 0 < config['ppo_clip'] < 1:
        raise ValueError('warmup_fraction 或 ppo_clip 超出范围')
    if type(config['seed']) is not int or type(config['explore_moves']) is not int or config['explore_moves'] < 0 or type(config['amp']) is not bool:
        raise ValueError('seed/explore_moves/amp 类型或数值不正确')
    return config


def features(board, komi):
    size = board.size
    cells = np.array(board.cells).reshape(size,size)
    result = np.zeros((CHANNELS,size,size),dtype=np.float32)
    result[0], result[1] = cells == board.player, cells == -board.player
    checked = set()
    for i, color in enumerate(board.cells):
        if not color or i in checked:
            continue
        group, liberties = board.group(i)
        checked.update(group)
        plane = 2 + min(len(liberties),3)-1
        for point in group:
            result[plane,point//size,point%size] = 1
    legal = np.zeros(size*size+1,dtype=np.bool_)
    legal[board.legal_moves()] = True
    result[5] = legal[:-1].reshape(size,size)
    for channel, offset in ((6,2),(7,3)):
        if len(board.history) >= offset:
            result[channel] = np.array(board.history[-offset]).reshape(size,size)*board.player
    result[8].fill(board.player == 1)
    result[9].fill(-board.player*komi/(size*size))
    result[10].fill(min(board.passes,2)/2)
    return result, legal


def augment(x, legal, policy, ownership, rotation, flip):
    def spatial(value):
        value = np.rot90(value,rotation,axes=(-2,-1))
        return np.ascontiguousarray(np.flip(value,axis=-1) if flip else value)
    size = x.shape[-1]
    return (spatial(x), np.r_[spatial(legal[:-1].reshape(size,size)).ravel(),legal[-1]],
            np.r_[spatial(policy[:-1].reshape(size,size)).ravel(),policy[-1]], spatial(ownership))


class Residual(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.layers = nn.Sequential(nn.Conv2d(channels,channels,3,padding=1,bias=False),nn.GroupNorm(8,channels),nn.SiLU(),
                                    nn.Conv2d(channels,channels,3,padding=1,bias=False),nn.GroupNorm(8,channels))
    def forward(self,x):
        return F.silu(x+self.layers(x))


class StudyNet(nn.Module):
    def __init__(self, channels=64, blocks=4):
        super().__init__()
        self.trunk = nn.Sequential(nn.Conv2d(CHANNELS,channels,3,padding=1,bias=False),nn.GroupNorm(8,channels),nn.SiLU(),
                                   *(Residual(channels) for _ in range(blocks)))
        self.policy = nn.Sequential(nn.Conv2d(channels,2,1),nn.Flatten(),nn.Linear(162,82))
        self.value_score = nn.Sequential(nn.AdaptiveAvgPool2d(1),nn.Flatten(),nn.Linear(channels,64),nn.SiLU(),nn.Linear(64,2),nn.Tanh())
        self.ownership = nn.Conv2d(channels,1,1)
        self.critic = None

    def enable_isolated_critic(self):
        if self.critic is not None:
            raise ValueError('独立价值分支已存在')
        # Copy the teacher value mapping without random initialization or unused score outputs.
        self.critic = deepcopy(self.value_score)
        last = self.critic[-2]
        last.weight = nn.Parameter(last.weight[:1].detach().clone())
        last.bias = nn.Parameter(last.bias[:1].detach().clone())
        last.out_features = 1

    def forward(self,x):
        trunk = self.trunk(x)
        values = self.value_score(trunk)
        value = values[:,0] if self.critic is None else self.critic(trunk.detach()).squeeze(1)
        return self.policy(trunk), value, values[:,1], self.ownership(trunk).squeeze(1).tanh()


def masked_log_policy(logits, legal):
    if not bool(legal.any(dim=-1).all()):
        raise ValueError('样本不存在合法落点')
    return F.log_softmax(logits.float().masked_fill(~legal,-1e9),dim=-1)


def distill_loss(outputs, batch, config):
    logits, value, score, ownership = outputs
    logp = masked_log_policy(logits,batch['legal'])
    p_loss = -(batch['policy']*logp).sum(-1).mean()
    v_loss = F.mse_loss(value.float(),batch['value'])
    score_loss = F.mse_loss(score.float(),batch['score'])
    own_loss = F.mse_loss(ownership.float(),batch['ownership'])
    total = p_loss + config['value_weight']*v_loss + config['score_weight']*score_loss + config['ownership_weight']*own_loss
    metrics = {'loss':total,'policy_ce':p_loss,'value_mse':v_loss,'score_mae_points':(score-batch['score']).abs().mean()*81,
               'ownership_mse':own_loss,'policy_top1':(logp.argmax(-1)==batch['policy'].argmax(-1)).float().mean(),
               'entropy':-(logp.exp()*logp).sum(-1).mean(),
               'raw_illegal_mass':(logits.float().softmax(-1)*(~batch['legal'])).sum(-1).mean()}
    return total,metrics


def teacher_replay_loss(outputs, batch, config):
    # The PPO critic fits student returns; do not supervise it with teacher values.
    logits, _, score, ownership = outputs
    policy = -(batch['policy']*masked_log_policy(logits,batch['legal'])).sum(-1).mean()
    score_loss = F.mse_loss(score.float(),batch['score'])
    own_loss = F.mse_loss(ownership.float(),batch['ownership'])
    total = policy + config['score_weight']*score_loss + config['ownership_weight']*own_loss
    return total, {'replay_loss':total,'replay_policy_ce':policy,'replay_score_mse':score_loss,'replay_ownership_mse':own_loss}


def ppo_loss(outputs, batch, reference_logp, config):
    logits, value, _, _ = outputs
    logp = masked_log_policy(logits,batch['legal'])
    selected = logp.gather(1,batch['action'][:,None]).squeeze(1)
    logratio = selected-batch['old_logp']
    ratio = logratio.exp()
    advantage = batch['advantage']
    surrogate = -torch.minimum(ratio*advantage,ratio.clamp(1-config['ppo_clip'],1+config['ppo_clip'])*advantage).mean()
    v_loss = F.mse_loss(value.float(),batch['value'])
    entropy = -(logp.exp()*logp).sum(-1).mean()
    reference_kl = (logp.exp()*(logp-reference_logp)).sum(-1).mean()
    total = surrogate + config['ppo_value_weight']*v_loss + config['reference_kl_weight']*reference_kl - config['entropy_weight']*entropy
    return total, {'loss':total,'policy_loss':surrogate,'value_mse':v_loss,'entropy':entropy,'reference_kl':reference_kl,
                   'approx_old_kl':((ratio-1)-logratio).mean(),'clip_fraction':((ratio-1).abs()>config['ppo_clip']).float().mean()}


def checkpoint_save(path, model, optimizer, scaler, config, stage, progress, rng, extra):
    state = {'schema':SCHEMA,'config':config,'stage':stage,'progress':progress,'model':model.state_dict(),
             'isolated_critic':model.critic is not None,
             'optimizer':optimizer.state_dict(),'scaler':scaler.state_dict(),'rng':rng.bit_generator.state,
             'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],**extra}
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.partial')
    torch.save(state,temporary)
    os.replace(temporary,path)


def checkpoint_load(path, device):
    state = torch.load(path,map_location='cpu',weights_only=True)
    if state.get('schema') != SCHEMA:
        raise ValueError('不是本路线的检查点，不能载入旧 best_model_fast.pth')
    config = state['config']
    model = StudyNet(config['channels'],config['blocks']).to(device)
    if state.get('isolated_critic',False):
        model.enable_isolated_critic()
    model.load_state_dict(state['model'],strict=True)
    return model,state


def shard_save(path, arrays, meta):
    validate_arrays(arrays)
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.partial')
    with temporary.open('wb') as handle:
        np.savez_compressed(handle, **arrays, meta=np.array(json.dumps(dict(meta,schema=SCHEMA),ensure_ascii=False)))
    os.replace(temporary,path)


def shard_load(path):
    with np.load(path,allow_pickle=False) as archive:
        meta = json.loads(str(archive['meta']))
        arrays = {key:archive[key] for key in archive.files if key != 'meta'}
    if meta.get('schema') != SCHEMA:
        raise ValueError(f'数据版本不符：{path}')
    validate_arrays(arrays)
    return arrays,meta


def validate_arrays(data):
    n = len(data['x'])
    shapes = {'x':(n,CHANNELS,9,9),'legal':(n,82),'policy':(n,82),'value':(n,),'score':(n,),'ownership':(n,9,9)}
    for key,shape in shapes.items():
        if data[key].shape != shape or not np.isfinite(data[key]).all():
            raise ValueError(f'数据形状或数值不合法：{key}')
    if not n or not np.all(data['legal'].any(-1)) or not np.allclose(data['policy'].sum(-1),1,atol=1e-5):
        raise ValueError('空数据、非法动作掩码或概率未归一化')
    if (data['policy'] < 0).any() or (data['policy'][~data['legal'].astype(bool)] > 1e-6).any():
        raise ValueError('策略目标含负概率或非法落子')
    for key in ('value','score','ownership'):
        if (np.abs(data[key]) > 1.0001).any():
            raise ValueError(f'{key} 应在 [-1,1]')


def stack_rows(rows):
    return {key:np.asarray([row[key] for row in rows]) for key in rows[0]}


def require_color_coverage(data, context):
    planes = data['x'][:,8]
    colors = planes[:,0,0]
    if not np.isin(colors,[0,1]).all() or not np.all(planes == colors[:,None,None]):
        raise ValueError(f'{context}：执棋方特征必须是全 0 或全 1 平面')
    counts = {'black':int((colors==1).sum()),'white':int((colors==0).sum())}
    if not all(counts.values()):
        raise ValueError(f'{context}：黑白行动样本缺失（黑 {counts["black"]} / 白 {counts["white"]}）；请用 sample_every=1 在新目录重新采集')
    return counts


def tensor_batch(data, indexes, device, rng=None):
    subset = {key:data[key][indexes].copy() for key in data}
    if rng is not None:
        for i in range(len(indexes)):
            x,legal,pi,own = augment(subset['x'][i],subset['legal'][i],subset['policy'][i],subset['ownership'][i],int(rng.integers(4)),bool(rng.integers(2)))
            subset['x'][i],subset['legal'][i],subset['policy'][i],subset['ownership'][i] = x,legal,pi,own
    return {key:torch.as_tensor(value,dtype=torch.bool if key=='legal' else torch.long if key=='action' else torch.float32,device=device) for key,value in subset.items()}


def teacher_targets(raw, legal, player):
    root = raw['rootInfo']
    pi = np.zeros(82,dtype=np.float32)
    for move in raw['moveInfos']:
        action = action_from_coordinate(move['move'],9)
        if legal[action]:
            pi[action] += max(0,move['visits'])
    if pi.sum() <= 0:
        raise ValueError('教师没有返回可用合法策略')
    return {'policy':pi/pi.sum(),'value':np.float32(player*(2*root['winrate']-1)),
            'score':np.float32(np.clip(player*root['scoreLead']/81,-1,1)),
            'ownership':np.asarray(raw['ownership'],dtype=np.float32).reshape(9,9)*player}


def environment_info():
    return {'python':platform.python_version(),'platform':platform.platform(),'torch':torch.__version__,'numpy':np.__version__,
            'cuda_build':torch.version.cuda,'cuda_available':torch.cuda.is_available(),
            'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}


class Journal:
    def __init__(self,run,command,config):
        self.run = Path(run)
        self.run.mkdir(parents=True,exist_ok=True)
        self.command = command
        self.started = time.monotonic()
        self.session = f'{os.getpid()}-{time.time_ns()}'
        self.config = config
    def log(self,event,**fields):
        record = {'utc':datetime.now(timezone.utc).isoformat(),'session':self.session,'command':self.command,
                  'run':str(self.run.resolve()),'elapsed_seconds':round(time.monotonic()-self.started,3),'event':event,**fields}
        line = json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n'
        for path in (LOG,self.run/'events.jsonl'):
            with path.open('a',encoding='utf-8') as handle:
                handle.write(line)
        print(json.dumps(record,ensure_ascii=False),flush=True)


@contextmanager
def run_lock(run):
    path = Path(run)/'active.lock'
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.write(fd,str(os.getpid()).encode()); os.close(fd)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
