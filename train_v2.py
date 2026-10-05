"""Finite teacher pretraining and KL-regularized PPO. No work runs on import."""
import argparse
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import re
import time

import numpy as np
import torch
from train_v2_common import (ROOT, SCHEMA, RULES_ID, ARRAY_KEYS, Game, StudyNet, Journal,
    config_load, digest_file, fingerprint, environment_info, features, masked_log_policy,
    teacher_targets, stack_rows, shard_save, shard_load, tensor_batch, distill_loss, ppo_loss, teacher_replay_loss,
    checkpoint_save, checkpoint_load, run_lock, require_color_coverage)
from katago_service import KataGoEngine, ENGINE_FILES


def inside(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if not path.is_relative_to(parent) or path == parent:
        raise ValueError(f'输出必须位于 {parent} 的子目录：{path}')
    return path


def seed_all(seed):
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    return np.random.default_rng(seed)


def teacher_identity():
    settings = ENGINE_FILES[2].read_text(encoding='utf-8-sig')
    perspectives = re.findall(r'^\s*reportAnalysisWinratesAs\s*=\s*(\w+)',settings,re.MULTILINE)
    if not perspectives or perspectives[-1].upper() != 'BLACK':
        raise ValueError('教师配置必须明确设置 reportAnalysisWinratesAs = BLACK，以保证奖励与标签视角正确')
    return {path.name:digest_file(path) for path in ENGINE_FILES}


def query_teacher(engine, game, config, judge=False):
    query = game.query(config['judge_visits'] if judge else config['teacher_visits'])
    query['overrideSettings'] = {'wideRootNoise':0}
    raw = engine.query(query)
    if raw['rootInfo']['visits'] < config['min_teacher_visits']:
        raise ValueError('教师实际搜索不足，停止本次任务；请检查引擎日志或预算')
    return raw


def infer(model, board, config, device):
    x,legal = features(board,config['komi'])
    with torch.inference_mode():
        outputs = model(torch.from_numpy(x[None]).to(device))
        logp = masked_log_policy(outputs[0],torch.from_numpy(legal[None]).to(device))[0]
    return x,legal,logp.cpu().numpy(),float(outputs[1][0])


def choose(probabilities,rng,temperature=1):
    if temperature == 0:
        return int(np.argmax(probabilities))
    weights = np.power(np.asarray(probabilities,dtype=np.float64),1/temperature)
    weights /= weights.sum()
    return int(rng.choice(len(weights),p=weights))


def stop_due(args, started):
    return args.max_minutes > 0 and time.monotonic()-started >= args.max_minutes*60


def collect(args, config, device, journal):
    data_dir = inside(args.data,ROOT/'train_v2_data')
    data_dir.mkdir(parents=True,exist_ok=True)
    identity = teacher_identity()
    contract = {'schema':SCHEMA,'kind':'teacher','config':config,'teacher':identity}
    contract_path = data_dir/'dataset.json'
    if contract_path.exists():
        if json.loads(contract_path.read_text(encoding='utf-8')) != contract:
            raise ValueError('数据目录的配置/教师不同，请使用新的数据目录')
    else:
        contract_path.write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    journal.log('dataset',path=str(data_dir),teacher=identity,target_games=args.games)
    split_counts = {name:{'games':0,'black':0,'white':0} for name in ('train','validation')}
    engine = KataGoEngine()
    started = time.monotonic()
    try:
        for game_index in range(args.games):
            path = data_dir/f'game-{game_index:06d}.npz'
            if path.exists():
                arrays,meta = shard_load(path)
                if meta['contract'] != fingerprint(contract):
                    raise ValueError(f'数据与目录清单不符：{path}')
                colors = require_color_coverage(arrays,str(path))
                split_counts[meta['split']]['games'] += 1
                for color,count in colors.items(): split_counts[meta['split']][color] += count
                continue
            rng = np.random.default_rng(config['seed']+game_index)
            game = Game(size=9,komi=config['komi'],mode='review')
            rows, moves, visits = [],[],[]
            game_started = time.monotonic()
            for move_index in range(config['max_moves']):
                board,_ = game.position()
                x,legal = features(board,config['komi'])
                raw = query_teacher(engine,game,config)
                visits.append(raw['rootInfo']['visits'])
                target = teacher_targets(raw,legal,board.player)
                # The teacher is queried every ply; retain both players' targets.
                rows.append(dict(x=x,legal=legal,**target))
                action = choose(target['policy'],rng,1 if move_index < config['explore_moves'] else config['late_temperature'])
                game.play(action); moves.append(action)
                if game.position()[0].passes >= 2:
                    break
            source_id = fingerprint({'rules':RULES_ID,'moves':moves})
            split = 'validation' if int(source_id[:8],16)/2**32 < config['validation_fraction'] else 'train'
            meta = {'kind':'teacher','source_id':source_id,'split':split,'contract':fingerprint(contract),
                    'seed':config['seed']+game_index,'moves':moves,'termination':'passes' if game.position()[0].passes>=2 else 'move_cap',
                    'teacher_visits':{'min':min(visits),'max':max(visits),'mean':float(np.mean(visits))}}
            arrays = stack_rows(rows)
            colors = require_color_coverage(arrays,str(path))
            meta['color_counts'] = colors
            shard_save(path,arrays,meta)
            split_counts[split]['games'] += 1
            for color,count in colors.items(): split_counts[split][color] += count
            journal.log('collected_game',game_index=game_index,positions=len(rows),moves=len(moves),split=split,
                        color_counts=colors,termination=meta['termination'],teacher_visits=meta['teacher_visits'],seconds=time.monotonic()-game_started,path=str(path),sha256=digest_file(path))
            if stop_due(args,started):
                journal.log('budget_pause',completed_games=game_index+1,target_games=args.games)
                break
    finally:
        engine.close()
    colors = {color:sum(counts[color] for counts in split_counts.values()) for color in ('black','white')}
    completed = sum(counts['games'] for counts in split_counts.values())
    journal.log('collection_summary',path=str(data_dir),games=completed,target_games=args.games,
                complete=completed==args.games,positions=sum(colors.values()),color_counts=colors,split_counts=split_counts)


def dataset_load(path,config):
    path = Path(path)
    contract = json.loads((path/'dataset.json').read_text(encoding='utf-8'))
    if contract['kind'] != 'teacher' or contract['config']['komi'] != config['komi']:
        raise ValueError('预训练只读取新协议的教师数据，不能读取旧 .pt 自对弈池')
    groups = {'train':[],'validation':[]}
    manifest,seen = {},set()
    count = 0
    for file in sorted(path.glob('game-*.npz')):
        data,meta = shard_load(file)
        if meta['contract'] != fingerprint(contract):
            raise ValueError(f'混入其他配置数据：{file}')
        manifest[file.name] = digest_file(file)
        if meta['source_id'] in seen:
            continue
        seen.add(meta['source_id'])
        count += len(data['x'])
        if count > config['max_dataset_positions']:
            raise ValueError('数据超过内存上限；请分实验使用较小的数据目录或提高 max_dataset_positions')
        groups[meta['split']].append(data)
    if any(len(group)<2 for group in groups.values()):
        raise ValueError('至少需要 2 局训练棋谱和 2 局验证棋谱。继续 collect 增加总局数；不要按局面随机拆分')
    merged = {name:{key:np.concatenate([game[key] for game in games]) for key in ARRAY_KEYS} for name,games in groups.items()}
    colors = {name:require_color_coverage(values,f'{path} / {name} 分区') for name,values in merged.items()}
    return merged, {'files':manifest,'unique_games':len(seen),'split_games':{name:len(games) for name,games in groups.items()},'color_counts':colors}


def metric_numbers(metrics):
    return {key:float(value.detach()) for key,value in metrics.items()}


def validate(model,data,config,device):
    model.eval()
    totals = {}
    with torch.inference_mode():
        for start in range(0,len(data['x']),config['batch_size']):
            indexes = np.arange(start,min(start+config['batch_size'],len(data['x'])))
            batch = tensor_batch(data,indexes,device)
            _,metrics = distill_loss(model(batch['x']),batch,config)
            for key,value in metric_numbers(metrics).items():
                totals[key] = totals.get(key,0)+value*len(indexes)
    return {key:value/len(data['x']) for key,value in totals.items()}


def amp_tools(config,device):
    enabled = config['amp'] and device.type == 'cuda'
    dtype = torch.bfloat16 if enabled and torch.cuda.is_bf16_supported() else torch.float16
    scaler = torch.amp.GradScaler('cuda',enabled=enabled and dtype==torch.float16)
    def context():
        return torch.autocast('cuda',dtype=dtype) if enabled else nullcontext()
    return scaler,context


def update(loss,model,optimizer,scaler,config):
    if not bool(torch.isfinite(loss)):
        raise FloatingPointError('损失非有限数，未执行参数更新')
    optimizer.zero_grad(set_to_none=True)
    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(),config['grad_clip'],error_if_nonfinite=True)
    scaler.step(optimizer); scaler.update()
    return float(norm)


def training_setup(args,config,device,stage,extra):
    latest = args.run/'latest.pt'
    rng = seed_all(config['seed'])
    if args.resume:
        if not latest.exists():
            raise ValueError('--resume 需要本 run 已存在 latest.pt')
        model,state = checkpoint_load(latest,device)
        if state['config'] != config or state['stage'] != stage or state['contract'] != extra:
            raise ValueError('断点的配置、阶段或数据/初始模型与当前任务不一致')
    else:
        if latest.exists():
            raise ValueError('已有 latest.pt。继续请加 --resume，新实验请更换 --run')
        if args.init:
            model,initial = checkpoint_load(args.init,device)
            if any(initial['config'][key] != config[key] for key in ('channels','blocks','board_size','komi')):
                raise ValueError('初始化模型结构或规则与配置不符')
        else:
            model = StudyNet(config['channels'],config['blocks']).to(device)
        state = None
    isolated = stage == 'ppo' and getattr(args,'isolated_critic',False)
    if isolated and model.critic is None and state is None:
        model.enable_isolated_critic()
    if (model.critic is not None) != isolated:
        raise ValueError('独立价值分支与当前模式不一致；请保持 --isolated-critic，或使用原预训练模型开始新实验')
    optimizer = torch.optim.AdamW(model.parameters(),lr=config['pretrain_lr'] if stage=='pretrain' else config['ppo_lr'],weight_decay=config['weight_decay'])
    scaler,autocast = amp_tools(config,device)
    if state:
        optimizer.load_state_dict(state['optimizer']); scaler.load_state_dict(state['scaler'])
        rng.bit_generator.state = state['rng']
        torch.set_rng_state(state['torch_rng'])
        if state['cuda_rng'] and device.type=='cuda':
            torch.cuda.set_rng_state_all(state['cuda_rng'])
    return model,optimizer,scaler,autocast,rng,state


def pretrain(args,config,device,journal):
    data,manifest = dataset_load(args.data,config)
    contract = {'manifest':manifest,'epochs':args.epochs,'initial_sha':digest_file(args.init) if args.init else None}
    model,opt,scaler,autocast,rng,state = training_setup(args,config,device,'pretrain',contract)
    epoch_done = state['progress'] if state else 0
    best_loss = state['best_loss'] if state else float('inf')
    steps = state['steps'] if state else 0
    batches = math.ceil(len(data['train']['x'])/config['batch_size'])
    total_steps = batches*args.epochs
    extra = {'contract':contract,'steps':steps,'best_loss':best_loss}
    journal.log('dataset_loaded',manifest=manifest,positions={name:len(values['x']) for name,values in data.items()})
    journal.log('model',parameters=sum(parameter.numel() for parameter in model.parameters()),device=str(device))
    if not state:
        initial_metrics = validate(model,data['validation'],config,device)
        best_loss = initial_metrics['loss']
        extra['best_loss'] = best_loss
        journal.log('validation_initial',metrics=initial_metrics)
        checkpoint_save(args.run/'latest.pt',model,opt,scaler,config,'pretrain',0,rng,extra)
        checkpoint_save(args.run/'best.pt',model,opt,scaler,config,'pretrain',0,rng,extra)
    started = time.monotonic()
    for epoch in range(epoch_done,args.epochs):
        model.train()
        order = rng.permutation(len(data['train']['x']))
        epoch_start = time.monotonic()
        if device.type=='cuda': torch.cuda.reset_peak_memory_stats()
        for offset in range(0,len(order),config['batch_size']):
            batch = tensor_batch(data['train'],order[offset:offset+config['batch_size']],device,rng)
            warmup = max(1,int(total_steps*config['warmup_fraction']))
            scale = (steps+1)/warmup if steps < warmup else .1+.9*.5*(1+math.cos(math.pi*(steps-warmup)/max(1,total_steps-warmup)))
            opt.param_groups[0]['lr'] = config['pretrain_lr']*scale
            with autocast():
                loss,metrics = distill_loss(model(batch['x']),batch,config)
            norm = update(loss,model,opt,scaler,config)
            steps += 1
            if steps % config['log_every'] == 0:
                journal.log('train_step',epoch=epoch+1,step=steps,lr=opt.param_groups[0]['lr'],grad_norm=norm,metrics=metric_numbers(metrics))
        metrics = validate(model,data['validation'],config,device)
        improved = metrics['loss'] < best_loss
        best_loss = min(best_loss,metrics['loss'])
        extra.update(steps=steps,best_loss=best_loss)
        checkpoint_save(args.run/'latest.pt',model,opt,scaler,config,'pretrain',epoch+1,rng,extra)
        if improved:
            checkpoint_save(args.run/'best.pt',model,opt,scaler,config,'pretrain',epoch+1,rng,extra)
        journal.log('epoch_complete',epoch=epoch+1,step=steps,validation=metrics,best_loss=best_loss,
                    seconds=time.monotonic()-epoch_start,samples_per_second=len(order)/max(.001,time.monotonic()-epoch_start),
                    gpu_peak_mb=torch.cuda.max_memory_allocated()/1024**2 if device.type=='cuda' else 0,
                    checkpoint=str(args.run/'latest.pt'),checkpoint_sha=digest_file(args.run/'latest.pt'))
        if stop_due(args,started):
            journal.log('budget_pause',completed_epochs=epoch+1,target_epochs=args.epochs)
            break


def rollout(model,config,device,engine,seed):
    rng = np.random.default_rng(seed)
    game = Game(size=9,komi=config['komi'],mode='review')
    rows,players,moves = [],[],[]
    model.eval()
    for _ in range(config['max_moves']):
        board,_ = game.position()
        x,legal,logp,value = infer(model,board,config,device)
        pi = np.exp(logp)
        action = choose(pi,rng)
        rows.append({'x':x,'legal':legal,'policy':pi/pi.sum(),'action':action,'old_logp':np.float32(logp[action]),'old_value':np.float32(value)})
        players.append(board.player); moves.append(action)
        game.play(action)
        if game.position()[0].passes >= 2: break
    raw = query_teacher(engine,game,config,judge=True)
    black_return = float(2*raw['rootInfo']['winrate']-1)
    for row,player in zip(rows,players):
        row.update(value=np.float32(player*black_return),score=np.float32(np.clip(player*raw['rootInfo']['scoreLead']/81,-1,1)),
                   ownership=np.asarray(raw['ownership'],dtype=np.float32).reshape(9,9)*player)
    return stack_rows(rows), {'moves':moves,'seed':seed,'termination':'passes' if game.position()[0].passes>=2 else 'move_cap',
            'black_return':black_return,'black_score':raw['rootInfo']['scoreLead'],'judge_visits':raw['rootInfo']['visits'],
            'early_pass':any(action==81 for action in moves[:20]),'reward_source':'KataGo soft adjudication, not an official result'}


def posttrain(args,config,device,journal):
    if not args.init:
        raise ValueError('posttrain 必须用 --init 指定冻结的预训练检查点')
    if not math.isfinite(args.replay_weight) or args.replay_weight < 0:
        raise ValueError('replay-weight 必须为非负有限数')
    if (args.replay_data is None) != (args.replay_weight == 0):
        raise ValueError('教师监督需同时指定 --replay-data 和正数 --replay-weight；默认两者均不启用')
    identity = teacher_identity()
    contract = {'initial_sha':digest_file(args.init),'teacher':identity}
    if args.isolated_critic:
        contract['isolated_critic'] = True
    replay = None
    if args.replay_data is not None:
        teacher_data,manifest = dataset_load(args.replay_data,config)
        replay = teacher_data['train']
        contract['replay'] = {'weight':args.replay_weight,'manifest':manifest}
        journal.log('teacher_replay_loaded',weight=args.replay_weight,manifest=manifest,
                    positions=len(replay['x']),split='train',targets=['policy','score','ownership'])
        del teacher_data
    model,opt,scaler,autocast,rng,state = training_setup(args,config,device,'ppo',contract)
    reference,_ = checkpoint_load(args.init,device)
    reference.eval().requires_grad_(False)
    done,steps = (state['progress'],state['steps']) if state else (0,0)
    extra = {'contract':contract,'steps':steps}
    journal.log('model',parameters=sum(parameter.numel() for parameter in model.parameters()),device=str(device),reference_sha=contract['initial_sha'])
    if not state:
        checkpoint_save(args.run/'latest.pt',model,opt,scaler,config,'ppo',0,rng,extra)
    started = time.monotonic()
    engine = KataGoEngine()
    try:
        for iteration in range(done,args.rounds):
            # Separate stream preserves the PPO shuffle RNG and round-boundary resume.
            replay_rng = np.random.default_rng(config['seed']+700000+iteration)
            actor_sha = digest_file(args.run/'latest.pt')
            games = []
            for index in range(config['ppo_games_per_round']):
                file = args.run/'rollouts'/f'round-{iteration+1:04d}'/f'game-{index:04d}.npz'
                if file.exists():
                    arrays,meta = shard_load(file)
                    if meta['actor_sha'] != actor_sha or meta['contract'] != fingerprint(contract):
                        raise ValueError('暂存 rollout 来自其他模型，不可用于当前 PPO')
                    journal.log('rollout_reused',round=iteration+1,game=index,path=str(file))
                else:
                    began = time.monotonic()
                    arrays,meta = rollout(model,config,device,engine,config['seed']+100000*(iteration+1)+index)
                    meta.update(actor_sha=actor_sha,contract=fingerprint(contract),kind='on_policy_ppo')
                    shard_save(file,arrays,meta)
                    journal.log('rollout_game',round=iteration+1,game=index,positions=len(arrays['x']),seconds=time.monotonic()-began,**meta)
                games.append((arrays,meta))
                if stop_due(args,started):
                    journal.log('budget_pause',completed_rounds=iteration,pending_round=iteration+1,collected_games=index+1)
                    return
            data = {key:np.concatenate([item[0][key] for item in games]) for key in games[0][0]}
            advantage = data['value']-data['old_value']
            data['advantage'] = (advantage-advantage.mean())/max(1e-6,advantage.std())
            journal.log('rollout_summary',round=iteration+1,positions=len(advantage),advantage_std=float(advantage.std()),
                        truncation_rate=sum(item[1]['termination']=='move_cap' for item in games)/len(games),
                        early_pass_rate=sum(item[1]['early_pass'] for item in games)/len(games))
            early_stop = False
            for epoch in range(config['ppo_epochs']):
                model.train()
                for indexes in np.array_split(rng.permutation(len(advantage)),math.ceil(len(advantage)/config['batch_size'])):
                    # No D4 augmentation: behavior log-probabilities refer to these exact states.
                    batch = tensor_batch(data,indexes,device)
                    with torch.no_grad():
                        reference_logp = masked_log_policy(reference(batch['x'])[0],batch['legal'])
                    with autocast():
                        loss,metrics = ppo_loss(model(batch['x']),batch,reference_logp,config)
                    numbers = metric_numbers(metrics)
                    if numbers['approx_old_kl'] > config['ppo_target_kl']:
                        journal.log('ppo_kl_stop',round=iteration+1,epoch=epoch+1,metrics=numbers)
                        early_stop = True
                        break
                    if replay is not None:
                        replay_indexes = replay_rng.integers(len(replay['x']),size=len(indexes))
                        replay_batch = tensor_batch(replay,replay_indexes,device)
                        with autocast():
                            auxiliary,replay_metrics = teacher_replay_loss(model(replay_batch['x']),replay_batch,config)
                            loss = loss + args.replay_weight*auxiliary
                        numbers.update(metric_numbers(replay_metrics))
                        numbers.update(ppo_loss=numbers['loss'],loss=float(loss.detach()))
                    norm = update(loss,model,opt,scaler,config)
                    steps += 1
                    journal.log('ppo_step',round=iteration+1,epoch=epoch+1,step=steps,grad_norm=norm,metrics=numbers)
                if early_stop: break
            extra['steps'] = steps
            checkpoint_save(args.run/'latest.pt',model,opt,scaler,config,'ppo',iteration+1,rng,extra)
            checkpoint_save(args.run/f'round-{iteration+1:04d}.pt',model,opt,scaler,config,'ppo',iteration+1,rng,extra)
            journal.log('round_complete',round=iteration+1,steps=steps,checkpoint_sha=digest_file(args.run/'latest.pt'),
                        checkpoint=str(args.run/f'round-{iteration+1:04d}.pt'),early_kl_stop=early_stop)
            if stop_due(args,started):
                journal.log('budget_pause',completed_rounds=iteration+1,target_rounds=args.rounds)
                break
    finally:
        engine.close()


def evaluate(args,config,device,journal):
    candidate,first = checkpoint_load(args.candidate,device)
    reference,second = checkpoint_load(args.reference,device)
    for state in (first,second):
        if state['config']['komi'] != config['komi']:
            raise ValueError('评估贴目与模型不一致')
    candidate.eval(); reference.eval()
    identity = {'candidate':digest_file(args.candidate),'reference':digest_file(args.reference),'teacher':teacher_identity(),
                'config':config,'pairs':args.pairs}
    output = args.run/'evaluation.json'
    if output.exists():
        saved = json.loads(output.read_text(encoding='utf-8'))
        if saved['identity'] != identity:
            raise ValueError('已有其他评估，请更换 --run')
        pairs = saved['pairs']
    else:
        pairs = []
    started = time.monotonic()
    engine = KataGoEngine()
    try:
        for pair_index in range(len(pairs),args.pairs):
            rng = np.random.default_rng(config['seed']+900000+pair_index)
            opening_game = Game(size=9,komi=config['komi'],mode='review')
            opening=[]
            for _ in range(4):
                legal = opening_game.position()[0].legal_moves()[:-1]
                action = int(rng.choice(legal)); opening.append(action); opening_game.play(action)
            scores=[]
            for candidate_color in (1,-1):
                game = Game.restore(opening_game.dump())
                for _ in range(config['max_moves']-4):
                    board,_ = game.position()
                    model = candidate if board.player==candidate_color else reference
                    _,_,logp,_ = infer(model,board,config,device)
                    game.play(int(logp.argmax()))
                    if game.position()[0].passes>=2: break
                raw = query_teacher(engine,game,config,judge=True)
                lead = float(raw['rootInfo']['scoreLead'])*candidate_color
                score = 1 if lead>.5 else 0 if lead<-.5 else .5
                scores.append(score)
                journal.log('evaluation_game',pair=pair_index,candidate_color=candidate_color,opening=opening,
                            score=score,teacher_margin=lead,termination='passes' if game.position()[0].passes>=2 else 'move_cap')
            pairs.append({'opening':opening,'scores':scores})
            temp = output.with_suffix('.partial')
            temp.write_text(json.dumps({'identity':identity,'pairs':pairs},ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temp,output)
            if stop_due(args,started): break
    finally:
        engine.close()
    if pairs:
        means = np.array([np.mean(item['scores']) for item in pairs])
        rng = np.random.default_rng(config['seed'])
        boot = means[rng.integers(len(means),size=(5000,len(means)))].mean(-1)
        ci = np.quantile(boot,[.025,.975]).tolist() if len(means)>=2 else [0,1]
        summary = {'pairs':len(pairs),'target_pairs':args.pairs,'games':len(pairs)*2,'paired_score_rate':float(means.mean()),
                   'pair_bootstrap_95':ci,'complete':len(pairs)==args.pairs,
                   'interpretation':'Teacher-adjudicated, greedy policy comparison; not Elo, not official game wins. Small samples are exploratory.'}
        (args.run/'evaluation-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        journal.log('evaluation_summary',**summary)


def doctor(args,config,device,journal):
    info = environment_info()
    # A tiny inference-only operation checks CUDA kernels without running training.
    with torch.inference_mode():
        sample = torch.ones((8,8),device=device)
        info['kernel_check'] = float((sample@sample).mean())
    info['teacher_files'] = {str(path):path.exists() for path in ENGINE_FILES}
    journal.log('doctor',device=str(device),**info)


def validate_checkpoint(args,config,device,journal):
    model,state = checkpoint_load(args.checkpoint,device)
    if state['config']['komi'] != config['komi']:
        raise ValueError('验证贴目与模型不一致')
    data,manifest = dataset_load(args.data,config)
    result = {'checkpoint':str(args.checkpoint.resolve()),'checkpoint_sha':digest_file(args.checkpoint),
              'stage':state['stage'],'progress':state['progress'],'manifest':manifest,
              'positions':len(data['validation']['x']),
              'metrics':validate(model,data['validation'],config,device)}
    (args.run/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    journal.log('validation_result',**result)


def report(args,config,device,journal):
    path = Path(args.source)/'events.jsonl'
    events=[]
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            try: events.append(json.loads(line))
            except json.JSONDecodeError: continue  # A killed process can leave an incomplete final line.
    useful = ('collected_game','collection_summary','dataset_loaded','validation_initial','epoch_complete','rollout_summary','ppo_step','round_complete','validation_result','evaluation_summary','error','interrupted','budget_pause')
    result = {'source':str(path.resolve()),'events':len(events),
              'latest_by_event':{name:next((item for item in reversed(events) if item['event']==name),None) for name in useful}}
    (args.run/'diagnosis-input.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    journal.log('report_ready',path=str(args.run/'diagnosis-input.json'))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('--config',type=Path,default=ROOT/'train_v2_config.json')
    root.add_argument('--run',type=Path,required=True,help='输出目录，必须位于 train_v2_runs 下')
    root.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    root.add_argument('--max-minutes',type=float,default=30,help='软预算，在完整局/epoch/对局配对边界停止；0 表示不限制')
    sub = root.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    p=sub.add_parser('collect'); p.add_argument('--data',type=Path,required=True);p.add_argument('--games',type=int,default=20)
    p=sub.add_parser('pretrain');p.add_argument('--data',type=Path,required=True);p.add_argument('--epochs',type=int,default=10);p.add_argument('--init',type=Path);p.add_argument('--resume',action='store_true')
    p=sub.add_parser('posttrain');p.add_argument('--init',type=Path,required=True);p.add_argument('--rounds',type=int,default=3);p.add_argument('--resume',action='store_true')
    p.add_argument('--replay-data',type=Path,help='可选教师数据，仅用训练分区做辅助监督')
    p.add_argument('--replay-weight',type=float,default=0,help='策略/目差/领地辅助监督权重；0 为原 PPO')
    p.add_argument('--isolated-critic',action='store_true',help='复制独立价值分支，阻断价值梯度进入共享骨干')
    p=sub.add_parser('evaluate');p.add_argument('--candidate',type=Path,required=True);p.add_argument('--reference',type=Path,required=True);p.add_argument('--pairs',type=int,default=5)
    p=sub.add_parser('validate');p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.set_defaults(handler=validate_checkpoint)
    p=sub.add_parser('report');p.add_argument('--source',type=Path,required=True)
    return root


def main():
    args=parser().parse_args()
    config=config_load(args.config)
    for key in ('games','epochs','rounds','pairs'):
        if hasattr(args,key) and getattr(args,key)<=0: raise ValueError(f'{key} 必须为正整数')
    if args.max_minutes < 0 or not math.isfinite(args.max_minutes): raise ValueError('max-minutes 必须为非负有限数')
    if args.command=='evaluate' and config['max_moves']<6: raise ValueError('评估需要 max_moves 至少为 6')
    args.run=inside(args.run,ROOT/'train_v2_runs')
    device=torch.device('cuda' if args.device=='auto' and torch.cuda.is_available() else 'cpu' if args.device=='auto' else args.device)
    journal=Journal(args.run,args.command,config)
    with run_lock(args.run):
        journal.log('command_start',config=config,arguments={key:str(value) if isinstance(value,Path) else value for key,value in vars(args).items() if key!='handler'},
                    environment=environment_info(),sources={path.name:digest_file(path) for path in (ROOT/'train_v2.py',ROOT/'train_v2_common.py',ROOT/'train_v2_config.json',ROOT/'go_game.py',ROOT/'katago_service.py')})
        try:
            handler = getattr(args,'handler',None) or globals()[args.command]
            handler(args,config,device,journal)
            journal.log('command_end')
        except KeyboardInterrupt:
            journal.log('interrupted',message='只保证 latest.pt 已提交的 epoch/round；未提交梯度不计完成。可按 README 恢复。')
            raise SystemExit(130)
        except Exception as exc:
            journal.log('error',exception=type(exc).__name__,message=str(exc))
            raise


if __name__=='__main__':
    main()
