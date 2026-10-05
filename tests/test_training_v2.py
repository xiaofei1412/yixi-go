"""Synthetic CPU inference/loss gradients: never step an optimizer or start KataGo."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
    import torch
except ModuleNotFoundError:
    raise unittest.SkipTest('训练路线测试需要独立安装 numpy 和 torch；产品运行不依赖它们')

import train_v2 as runner
import train_v2_common as common
from go_game import Board


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.config = common.config_load(common.ROOT/'train_v2_config.json')
        self.config.update(channels=8,blocks=1,amp=False)

    def sample(self,both=False):
        board=Board(9); rows=[]
        for _ in range(2 if both else 1):
            x,legal = common.features(board,7.5)
            rows.append({'x':x,'legal':legal,'policy':legal.astype(np.float32)/legal.sum(),
                         'value':np.float32(0),'score':np.float32(0),'ownership':np.zeros((9,9),dtype=np.float32)})
            board.play(81)
        return common.stack_rows(rows)

    def test_features_player_history_pass_and_komi(self):
        board = Board(9)
        board.play(0); board.play(10)
        x,legal = common.features(board,7.5)
        self.assertEqual(x[0,0,0],1)
        self.assertEqual(x[1,1,1],1)
        self.assertEqual(x[6,0,0],1)
        self.assertEqual(x[6,1,1],0)
        self.assertAlmostEqual(float(x[9,0,0]),-7.5/81)
        self.assertFalse(legal[0]); self.assertTrue(legal[81])
        board.play(81)
        x,_ = common.features(board,7.5)
        self.assertEqual(x[0,1,1],1)
        self.assertEqual(x[8,0,0],0)
        self.assertEqual(x[10,0,0],.5)
        self.assertAlmostEqual(float(x[9,0,0]),7.5/81)

    def test_all_symmetries_preserve_policy_mask_and_pass(self):
        x = np.zeros((11,9,9),dtype=np.float32); x[0,1,2]=1
        legal = np.zeros(82,dtype=bool); legal[11]=legal[81]=True
        pi = legal.astype(np.float32)/2
        own = x[0].copy()
        for rotation in range(4):
            for flip in (False,True):
                xx,ll,pp,oo = common.augment(x,legal,pi,own,rotation,flip)
                self.assertEqual(pp[-1],.5)
                self.assertTrue(ll[-1])
                np.testing.assert_array_equal(xx[0],oo)
                np.testing.assert_array_equal(ll[:-1].reshape(9,9),oo.astype(bool))
                self.assertEqual(float(pp[~ll].sum()),0)
                np.testing.assert_array_equal(pp[:-1].reshape(9,9),oo*.5)

    def test_mask_rejects_empty_and_removes_illegal_probability(self):
        logp = common.masked_log_policy(torch.tensor([[99.,1.,2.]]),torch.tensor([[False,True,True]]))
        self.assertEqual(float(logp.exp()[0,0]),0)
        self.assertAlmostEqual(float(logp.exp().sum()),1,places=6)
        with self.assertRaises(ValueError):
            common.masked_log_policy(torch.zeros(1,3),torch.zeros(1,3,dtype=torch.bool))

    def test_white_teacher_targets_and_illegal_visits(self):
        legal = np.ones(82,dtype=bool); legal[0]=False
        raw = {'rootInfo':{'winrate':.75,'scoreLead':8.1},'ownership':[.5]*81,
               'moveInfos':[{'move':'A9','visits':99},{'move':'B9','visits':3},{'move':'pass','visits':1}]}
        target = common.teacher_targets(raw,legal,-1)
        self.assertEqual(target['policy'][0],0)
        self.assertEqual(target['policy'][1],.75)
        self.assertEqual(target['policy'][-1],.25)
        self.assertEqual(target['value'],-.5)
        self.assertAlmostEqual(float(target['score']),-.1)
        self.assertTrue(np.all(target['ownership']==-.5))

    def test_model_and_distillation_inference_are_finite(self):
        model = common.StudyNet(8,1).eval()
        batch = common.tensor_batch(self.sample(),np.array([0]),torch.device('cpu'))
        with torch.inference_mode():
            outputs = model(batch['x'])
            loss,metrics = common.distill_loss(outputs,batch,self.config)
        self.assertEqual(outputs[0].shape,(1,82))
        self.assertEqual(outputs[3].shape,(1,9,9))
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(all(torch.isfinite(value) for value in metrics.values()))
        self.assertTrue(all(parameter.grad is None for parameter in model.parameters()))

    def test_isolated_critic_preserves_initial_outputs_and_rng(self):
        model=common.StudyNet(8,1).eval()
        x=torch.from_numpy(self.sample(both=True)['x'])
        with torch.inference_mode(): before=model(x)
        rng=torch.get_rng_state().clone()
        model.enable_isolated_critic()
        self.assertTrue(torch.equal(rng,torch.get_rng_state()))
        with torch.inference_mode(): after=model(x)
        for a,b in zip(before,after): torch.testing.assert_close(a,b)
        self.assertEqual(model.critic[-2].out_features,1)
        self.assertNotEqual(model.critic[2].weight.data_ptr(),model.value_score[2].weight.data_ptr())

    def test_isolated_value_gradient_cannot_reach_trunk_or_score(self):
        model=common.StudyNet(8,1); model.enable_isolated_critic()
        x=torch.from_numpy(self.sample(both=True)['x'])
        value=model(x)[1]
        (value-1).square().mean().backward()
        self.assertGreater(sum(float(p.grad.abs().sum()) for p in model.critic.parameters()),0)
        for module in (model.trunk,model.policy,model.value_score,model.ownership):
            self.assertTrue(all(p.grad is None for p in module.parameters()))

    def test_isolated_policy_gradient_still_trains_trunk_without_critic(self):
        model=common.StudyNet(8,1); model.enable_isolated_critic()
        x=torch.from_numpy(self.sample(both=True)['x'])
        logits=model(x)[0]
        torch.nn.functional.cross_entropy(logits,torch.zeros(len(x),dtype=torch.long)).backward()
        self.assertGreater(sum(float(p.grad.abs().sum()) for p in model.trunk.parameters()),0)
        self.assertTrue(all(p.grad is None for p in model.critic.parameters()))

    def test_isolated_checkpoint_and_legacy_checkpoint_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model.pt'
            model=common.StudyNet(8,1)
            scaler=torch.amp.GradScaler('cuda',enabled=False)
            def save():
                common.checkpoint_save(path,model,torch.optim.AdamW(model.parameters()),scaler,
                                       self.config,'ppo',0,np.random.default_rng(1),{})
            save()
            state=torch.load(path,weights_only=True); del state['isolated_critic']; torch.save(state,path)
            old,_=common.checkpoint_load(path,torch.device('cpu'))
            self.assertIsNone(old.critic)
            model.enable_isolated_critic(); save()
            loaded,state=common.checkpoint_load(path,torch.device('cpu'))
            self.assertTrue(state['isolated_critic'])
            for key,value in model.state_dict().items(): torch.testing.assert_close(value,loaded.state_dict()[key])

    def test_isolated_setup_adds_critic_to_optimizer_and_rejects_disabled_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)/'run'; initial=Path(folder)/'initial.pt'
            model=common.StudyNet(8,1)
            common.checkpoint_save(initial,model,torch.optim.AdamW(model.parameters()),
                                   torch.amp.GradScaler('cuda',enabled=False),self.config,'pretrain',0,
                                   np.random.default_rng(1),{})
            args=runner.parser().parse_args(['--run',str(run),'posttrain','--init',str(initial),'--isolated-critic'])
            contract={'isolated_critic':True}
            model,opt,scaler,_,rng,_=runner.training_setup(args,self.config,torch.device('cpu'),'ppo',contract)
            optimized={id(p) for group in opt.param_groups for p in group['params']}
            self.assertTrue(all(id(p) in optimized for p in model.critic.parameters()))
            common.checkpoint_save(run/'latest.pt',model,opt,scaler,self.config,'ppo',0,rng,{'contract':contract})
            args.resume=True
            restored,*_=runner.training_setup(args,self.config,torch.device('cpu'),'ppo',contract)
            self.assertIsNotNone(restored.critic)
            args.isolated_critic=False
            with self.assertRaisesRegex(ValueError,'不一致'):
                runner.training_setup(args,self.config,torch.device('cpu'),'ppo',{})

    def test_ppo_clips_positive_and_negative_advantages(self):
        config = dict(self.config,ppo_value_weight=0,reference_kl_weight=0,entropy_weight=0)
        logits = torch.log(torch.tensor([[.8,.2],[.2,.8]]))
        legal = torch.ones((2,2),dtype=torch.bool)
        ref = common.masked_log_policy(logits,legal)
        batch = {'legal':legal,'action':torch.tensor([0,0]),'old_logp':torch.log(torch.tensor([.4,.4])),
                 'advantage':torch.tensor([1.,-1.]),'value':torch.zeros(2)}
        loss,metrics = common.ppo_loss((logits,torch.zeros(2),None,None),batch,ref,config)
        # Positive A clips ratio 2 to 1.2; negative A clips ratio .5 to .8.
        self.assertAlmostEqual(float(loss),-.2,places=6)
        self.assertAlmostEqual(float(metrics['reference_kl']),0,places=6)
        self.assertEqual(float(metrics['clip_fraction']),1)

    def test_teacher_replay_supervises_three_outputs_but_not_critic(self):
        logits=torch.zeros(2,82,requires_grad=True)
        value=torch.ones(2,requires_grad=True)
        score=torch.ones(2,requires_grad=True)
        ownership=torch.ones(2,9,9,requires_grad=True)
        batch=common.tensor_batch(self.sample(both=True),np.arange(2),torch.device('cpu'))
        batch['policy'].zero_(); batch['policy'][:,0]=1
        batch['value'].fill_(float('nan'))  # Teacher value labels must not enter this loss.
        loss,metrics=common.teacher_replay_loss((logits,value,score,ownership),batch,self.config)
        self.assertAlmostEqual(float(loss.detach()),np.log(82)+.25+.1,places=5)
        loss.backward()
        self.assertIsNone(value.grad)
        for output in (logits,score,ownership):
            self.assertTrue(torch.isfinite(output.grad).all())
            self.assertGreater(float(output.grad.abs().sum()),0)
        self.assertTrue(all(torch.isfinite(v) for v in metrics.values()))

    def test_replay_arguments_reject_invalid_combinations_before_engine(self):
        args=runner.parser().parse_args(['--run','train_v2_runs/test','posttrain','--init','unused.pt'])
        with patch.object(runner,'teacher_identity',side_effect=AssertionError('No engine access')):
            for path,weight in ((None,.1),(Path('data'),0),(Path('data'),-1),(Path('data'),float('nan'))):
                with self.subTest(path=path,weight=weight):
                    args.replay_data,args.replay_weight=path,weight
                    with self.assertRaises(ValueError):
                        runner.posttrain(args,self.config,torch.device('cpu'),Mock())

    def test_replay_contract_tracks_weight_manifest_and_training_partition(self):
        args=runner.parser().parse_args(['--run','train_v2_runs/test','posttrain','--init','unused.pt',
                                        '--replay-data','data','--replay-weight','.1'])
        teacher={'train':self.sample(both=True),'validation':self.sample()}
        manifest={'files':{'game.npz':'hash'}}
        journal=Mock()
        with patch.object(runner,'teacher_identity',return_value={}), \
             patch.object(runner,'digest_file',return_value='initial'), \
             patch.object(runner,'dataset_load',return_value=(teacher,manifest)), \
             patch.object(runner,'training_setup',side_effect=RuntimeError('stop before training')) as setup:
            with self.assertRaisesRegex(RuntimeError,'stop before training'):
                runner.posttrain(args,self.config,torch.device('cpu'),journal)
            self.assertEqual(setup.call_args.args[-1]['replay'],{'weight':.1,'manifest':manifest})
        self.assertEqual(journal.log.call_args.kwargs['split'],'train')
        self.assertEqual(journal.log.call_args.kwargs['positions'],2)

    def test_default_ppo_keeps_original_contract_and_does_not_load_replay(self):
        args=runner.parser().parse_args(['--run','train_v2_runs/test','posttrain','--init','unused.pt'])
        with patch.object(runner,'teacher_identity',return_value={}), \
             patch.object(runner,'digest_file',return_value='initial'), \
             patch.object(runner,'dataset_load',side_effect=AssertionError('Replay disabled')), \
             patch.object(runner,'training_setup',side_effect=RuntimeError('stop before training')) as setup:
            with self.assertRaisesRegex(RuntimeError,'stop before training'):
                runner.posttrain(args,self.config,torch.device('cpu'),Mock())
            self.assertEqual(setup.call_args.args[-1],{'initial_sha':'initial','teacher':{}})

    def test_resume_rejects_changed_replay_weight_data_or_disabling(self):
        from types import SimpleNamespace
        original={'initial_sha':'initial','teacher':{},'replay':{'weight':.1,'manifest':{'file':'old'}}}
        alternatives=[dict(original,replay={'weight':.2,'manifest':{'file':'old'}}),
                      dict(original,replay={'weight':.1,'manifest':{'file':'new'}}),
                      {'initial_sha':'initial','teacher':{}}]
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder); (run/'latest.pt').touch()
            state={'config':self.config,'stage':'ppo','contract':original}
            args=SimpleNamespace(run=run,resume=True)
            with patch.object(runner,'checkpoint_load',return_value=(None,state)):
                for contract in alternatives:
                    with self.subTest(contract=contract), self.assertRaisesRegex(ValueError,'不一致'):
                        runner.training_setup(args,self.config,torch.device('cpu'),'ppo',contract)

    def test_ppo_unchanged_actor_has_zero_old_kl(self):
        logits = torch.zeros(2,82); legal=torch.ones(2,82,dtype=torch.bool)
        ref=common.masked_log_policy(logits,legal)
        batch={'legal':legal,'action':torch.tensor([0,81]),'old_logp':ref[:,0],
               'advantage':torch.tensor([1.,-1.]),'value':torch.zeros(2)}
        _,metrics=common.ppo_loss((logits,torch.zeros(2),None,None),batch,ref,self.config)
        self.assertAlmostEqual(float(metrics['approx_old_kl']),0,places=6)
        self.assertAlmostEqual(float(metrics['policy_loss']),0,places=6)

    def test_shards_roundtrip_and_reject_corrupt_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sample.npz'
            data=self.sample()
            common.shard_save(path,data,{'source_id':'test'})
            loaded,meta=common.shard_load(path)
            np.testing.assert_array_equal(loaded['x'],data['x'])
            self.assertEqual(meta['schema'],common.SCHEMA)
            data['policy'][0,0]=-1
            with self.assertRaises(ValueError): common.shard_save(path,data,{})

    def test_checkpoint_roundtrip_with_empty_optimizer_and_rng(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(torch.cuda,'is_available',return_value=False):
            model=common.StudyNet(8,1)
            optimizer=torch.optim.AdamW(model.parameters())  # No step or backward.
            scaler=torch.amp.GradScaler('cuda',enabled=False)
            rng=np.random.default_rng(123)
            path=Path(folder)/'latest.pt'
            common.checkpoint_save(path,model,optimizer,scaler,self.config,'pretrain',0,rng,{'contract':{}})
            loaded,state=common.checkpoint_load(path,torch.device('cpu'))
            self.assertEqual(state['progress'],0)
            restored=np.random.default_rng(); restored.bit_generator.state=state['rng']
            self.assertEqual(rng.random(),restored.random())
            for key,value in model.state_dict().items(): torch.testing.assert_close(value,loaded.state_dict()[key])
            self.assertEqual(state['optimizer']['state'],{})

    def test_dataset_deduplicates_whole_games_and_requires_both_splits(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            contract={'kind':'teacher','config':self.config}
            (path/'dataset.json').write_text(json.dumps(contract),encoding='utf-8')
            for index,split in enumerate(('train','train','validation','validation','train')):
                common.shard_save(path/f'game-{index:06d}.npz',self.sample(both=True),
                                  {'source_id':str(index if index<4 else 0),'split':split,'contract':common.fingerprint(contract)})
            data,manifest=runner.dataset_load(path,self.config)
            self.assertEqual(manifest['unique_games'],4)
            self.assertEqual(manifest['split_games'],{'train':2,'validation':2})
            self.assertEqual(len(data['train']['x']),4)
            self.assertEqual(manifest['color_counts']['validation'],{'black':2,'white':2})
            (path/'game-000003.npz').unlink()
            with self.assertRaisesRegex(ValueError,'2'): runner.dataset_load(path,self.config)

    def test_teacher_rejects_wrong_perspective_without_starting_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'analysis.cfg'
            path.write_text('reportAnalysisWinratesAs = WHITE',encoding='utf-8')
            with patch.object(runner,'ENGINE_FILES',[path,path,path]):
                with self.assertRaisesRegex(ValueError,'BLACK'): runner.teacher_identity()

    def test_teacher_minimum_search_budget_with_stub(self):
        from types import SimpleNamespace
        engine=SimpleNamespace(query=lambda payload:{'rootInfo':{'visits':1}})
        with self.assertRaisesRegex(ValueError,'不足'):
            runner.query_teacher(engine,common.Game(size=9,komi=7.5,mode='review'),self.config)

    def test_output_path_guard_and_resume_mismatch(self):
        with self.assertRaises(ValueError): runner.inside(common.ROOT,common.ROOT/'train_v2_runs')
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder); (path/'latest.pt').touch()
            args=SimpleNamespace(run=path,resume=True)
            with patch.object(runner,'checkpoint_load',return_value=(None,{'config':{},'stage':'pretrain','contract':{}})):
                with self.assertRaisesRegex(ValueError,'不一致'):
                    runner.training_setup(args,self.config,torch.device('cpu'),'pretrain',{})

    def test_config_rejects_incompatible_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps(dict(self.config,komi=3.5)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'7.5'): common.config_load(path)

    def test_validate_cli_logs_without_training_or_engine(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(torch.cuda,'is_available',return_value=False):
            root=Path(folder); data=root/'data'; data.mkdir()
            config_path=root/'config.json'
            config_path.write_text(json.dumps(self.config),encoding='utf-8')
            contract={'kind':'teacher','config':self.config}
            (data/'dataset.json').write_text(json.dumps(contract),encoding='utf-8')
            for index,split in enumerate(('train','train','validation','validation')):
                common.shard_save(data/f'game-{index:06d}.npz',self.sample(both=True),
                                  {'source_id':str(index),'split':split,'contract':common.fingerprint(contract)})
            model=common.StudyNet(8,1)
            path=root/'initial.pt'
            common.checkpoint_save(path,model,torch.optim.AdamW(model.parameters()),
                                   torch.amp.GradScaler('cuda',enabled=False),self.config,'pretrain',0,
                                   np.random.default_rng(1),{})
            run=root/'train_v2_runs'/'validate'
            argv=['train_v2.py','--config',str(config_path),'--run',str(run),'--device','cpu',
                  'validate','--checkpoint',str(path),'--data',str(data)]
            # Only redirect output location; real source hashes still read project files.
            with patch('sys.argv',argv), patch.object(runner,'inside',return_value=run), \
                 patch.object(common,'LOG',root/'global.jsonl'), patch('builtins.print'), \
                 patch.object(runner,'update',side_effect=AssertionError('No training allowed')), \
                 patch.object(runner,'KataGoEngine',side_effect=AssertionError('No engine allowed')):
                runner.main()
            events=[json.loads(line) for line in (run/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([event['event'] for event in events],['command_start','validation_result','command_end'])
            self.assertEqual(events[1]['positions'],4)
            self.assertFalse((run/'active.lock').exists())

    def test_config_rejects_sparse_sampling_that_can_omit_white(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            for interval in (2,3,4):
                with self.subTest(interval=interval):
                    path.write_text(json.dumps(dict(self.config,sample_every=interval)),encoding='utf-8')
                    with self.assertRaisesRegex(ValueError,'sample_every=1'): common.config_load(path)

    def test_coverage_rejects_single_color_and_invalid_color_plane(self):
        data=self.sample()
        with self.assertRaisesRegex(ValueError,'黑白行动样本缺失'): common.require_color_coverage(data,'fixture')
        data['x'][:,8]=0
        with self.assertRaisesRegex(ValueError,'黑白行动样本缺失'): common.require_color_coverage(data,'fixture')
        data=self.sample(both=True); data['x'][0,8,0,1]=0
        with self.assertRaisesRegex(ValueError,'执棋方特征'): common.require_color_coverage(data,'fixture')

    def test_dataset_rejects_single_color_in_either_split(self):
        for bad_split in ('train','validation'):
            with self.subTest(split=bad_split), tempfile.TemporaryDirectory() as folder:
                path=Path(folder); contract={'kind':'teacher','config':self.config}
                (path/'dataset.json').write_text(json.dumps(contract),encoding='utf-8')
                for index,split in enumerate(('train','train','validation','validation')):
                    common.shard_save(path/f'game-{index:06d}.npz',self.sample(both=split!=bad_split),
                                      {'source_id':str(index),'split':split,'contract':common.fingerprint(contract)})
                with self.assertRaisesRegex(ValueError,bad_split+' 分区.*黑白行动样本缺失'):
                    runner.dataset_load(path,self.config)

    def test_collector_retains_both_colors_and_resume_summary(self):
        from types import SimpleNamespace
        from go_game import coordinate
        # Scripted legal moves and fake teacher replies only; no real engine or model.
        for moves in ([0,1,81,81],[0,1,2,81,81]):
            with self.subTest(moves=moves), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); data=root/'train_v2_data'/'test'
                args=SimpleNamespace(data=data,games=1,max_minutes=0)
                config=dict(self.config,max_moves=5)
                engine=Mock()
                engine.query.side_effect=[{'rootInfo':{'visits':64,'winrate':.75,'scoreLead':8.1},
                                          'ownership':[.5]*81,'moveInfos':[{'move':coordinate(action,9),'visits':64}]} for action in moves]
                journal=Mock()
                with patch.object(runner,'ROOT',root), patch.object(runner,'teacher_identity',return_value={'fake':'test'}), \
                     patch.object(runner,'KataGoEngine',return_value=engine):
                    runner.collect(args,config,torch.device('cpu'),journal)
                    arrays,meta=common.shard_load(data/'game-000000.npz')
                    n=len(moves); expected={'black':(n+1)//2,'white':n//2}
                    self.assertEqual(len(arrays['x']),n)
                    self.assertEqual(meta['color_counts'],expected)
                    np.testing.assert_array_equal(arrays['x'][:,8,0,0],[1-i%2 for i in range(n)])
                    np.testing.assert_allclose(arrays['value'],[.5*(-1)**i for i in range(n)])
                    np.testing.assert_allclose(arrays['score'],[.1*(-1)**i for i in range(n)])
                    self.assertEqual(engine.query.call_count,n)
                    self.assertEqual(journal.log.call_args.args,('collection_summary',))
                    self.assertEqual(journal.log.call_args.kwargs['color_counts'],expected)
                    runner.collect(args,config,torch.device('cpu'),journal)
                    self.assertEqual(engine.query.call_count,n)  # Completed shards reused, never queried twice.
                    self.assertEqual(journal.log.call_args.kwargs['color_counts'],expected)
                    common.shard_save(data/'game-000000.npz',{key:value[:1] for key,value in arrays.items()},meta)
                    with self.assertRaisesRegex(ValueError,'黑白行动样本缺失'):
                        runner.collect(args,config,torch.device('cpu'),journal)
                    self.assertEqual(engine.query.call_count,n)

    def test_collector_refuses_old_dataset_contract_before_engine_start(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); data=root/'train_v2_data'/'old'; data.mkdir(parents=True)
            old={'schema':common.SCHEMA,'kind':'teacher','config':dict(self.config,sample_every=4),'teacher':{'fake':'test'}}
            (data/'dataset.json').write_text(json.dumps(old),encoding='utf-8')
            args=SimpleNamespace(data=data,games=20,max_minutes=0)
            with patch.object(runner,'ROOT',root), patch.object(runner,'teacher_identity',return_value={'fake':'test'}), \
                 patch.object(runner,'KataGoEngine',side_effect=AssertionError('No real engine')):
                with self.assertRaisesRegex(ValueError,'新的数据目录'):
                    runner.collect(args,self.config,torch.device('cpu'),Mock())


if __name__=='__main__':
    unittest.main()
