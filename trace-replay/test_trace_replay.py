"""CPU-only trace normalization, exact-token replay and real local activity tests."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import prepare
import replay

SPEC=importlib.util.spec_from_file_location('trace_compare',Path(__file__).with_name('compare.py'))
compare=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(compare)
SPEC=importlib.util.spec_from_file_location('trace_controller',Path(__file__).resolve().parents[2]/'sglang/python/sglang/srt/mem_cache/stream_activity.py')
controller=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(controller)


def source_rows():
    result=[]
    for sid in ['a','b']:
        for index in range(2):
            result.append(dict(provider='codex',session_id=sid,model='original-model',round_index=index,
                input_tokens_total=20+index*10,prefix_tokens=index*20,newly_append_tokens=20-index*10,
                output_tokens=2,first_input_event_type='tool_result' if index else 'user_message',
                timing_events=[dict(event_type='user_message' if index==0 else 'tool_result',timestamp='2026-06-01T00:00:00Z')],
                tools=[dict(tool_name='exec_command',tool_call_id=f'{sid}-{index}',
                            emitted_at='2026-06-01T00:00:01Z',result_at='2026-06-01T00:00:01.020Z')]))
    return result


def make_plan(root):
    source=root/'source.jsonl'
    source.write_text(''.join(json.dumps(r)+'\n' for r in source_rows()))
    return prepare.prepare(source,sessions=2,rounds=2,min_rounds=2,token_scale=1,time_scale=1,max_context=64)


class TraceTests(unittest.TestCase):
    def test_deterministic_selection_and_scaled_counts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            a=make_plan(root);b=make_plan(root)
        self.assertEqual(a,b)
        self.assertFalse(a['matches_pinned_release'])
        self.assertEqual(len(a['sessions']),2)
        self.assertEqual(a['sessions'][0]['steps'][1]['prefix_len'],20)
        replay.validate_plan(a)

    def test_scale_and_caps_explicit_without_silent_overflow(self):
        row=source_rows()[1]
        step=prepare.convert(row,0.5,2,1,0.01)
        self.assertEqual(step['prefix_len']+step['input_len'],15)
        self.assertEqual(step['output_len'],1)
        self.assertTrue(step['tools'][0]['duration_capped'])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'source.jsonl'
            p.write_text(''.join(json.dumps(r)+'\n' for r in source_rows()))
            with self.assertRaisesRegex(ValueError,'context_overflow'):
                prepare.prepare(p,sessions=1,min_rounds=2,rounds=2,token_scale=1,max_context=10)

    def test_overlapping_tools_keep_relative_offsets(self):
        row=source_rows()[0]
        row['tools'].append(dict(tool_name='Read',emitted_at='2026-06-01T00:00:01.010Z',result_at='2026-06-01T00:00:01.040Z'))
        tools=prepare.convert(row,1,1,0,0)['tools']
        self.assertAlmostEqual(tools[1]['offset_seconds'],0.01,places=5)
        self.assertLess(tools[1]['offset_seconds'],tools[0]['duration_seconds'])

    def test_unknown_tool_latency_is_rejected(self):
        row=source_rows()[0];row['tools']=[dict(tool_name='Unknown')]
        with self.assertRaisesRegex(ValueError,'missing'):
            prepare.convert(row,1,1,0,0)

    def test_human_wait_is_separate_from_tools(self):
        rows=source_rows()
        rows[1]['first_input_event_type']='user_message'
        rows[1]['timing_events']=[dict(event_type='user_message',timestamp='2026-06-01T00:00:11.020Z')]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trace.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            plan=prepare.prepare(p,sessions=2,rounds=2,min_rounds=2,token_scale=1,time_scale=0.1,human_wait_cap=0.5,max_context=64)
        session=next(s for s in plan['sessions'] if s['session_id']=='a')
        self.assertAlmostEqual(session['steps'][0]['original_human_wait_after_seconds'],10,places=4)
        self.assertEqual(session['steps'][0]['human_wait_after_seconds'],0.5)

    def test_exact_generated_output_carried_into_prefix(self):
        builder=replay.PromptBuilder(list(range(10,100)),118)
        first,_,_=builder.build(dict(prefix_len=0,input_len=5))
        builder.commit(first,[501,502])
        second,reusable,missing=builder.build(dict(prefix_len=7,input_len=2))
        self.assertEqual(second[:7],first+[501,502])
        self.assertEqual((reusable,missing),(7,0))
        third,reusable,missing=builder.build(dict(prefix_len=10,input_len=1))
        self.assertEqual((len(third),reusable,missing),(11,7,3))

    def test_generated_output_ids_required_and_fixed_lengths(self):
        response=types.SimpleNamespace(raise_for_status=lambda:None,
            json=lambda:dict(output_ids=[10,11],meta_info=dict(prompt_tokens=3,completion_tokens=2)))
        with patch('requests.post',return_value=response) as post:
            ids,record=replay.generate('http://local',[1,2,3],2,5)
            payload=post.call_args.kwargs['json']
            self.assertEqual(ids,[10,11])
            self.assertEqual(payload['input_ids'],[1,2,3])
            self.assertTrue(payload['sampling_params']['ignore_eos'])
            self.assertIsNone(record['cached_tokens'])
            self.assertIsNone(record['ttft_seconds'])
        response.json=lambda:dict(text='no token ids',meta_info=dict(prompt_tokens=3,completion_tokens=2))
        with patch('requests.post',return_value=response),self.assertRaisesRegex(RuntimeError,'exact output_ids'):
            replay.generate('http://local',[1,2,3],2,5)

    def test_union_windows_do_not_double_count_overlaps(self):
        request=dict(start_ns=5,end_ns=7,latency_seconds=2e-9,prompt_tokens=10,cached_tokens=None)
        events=[dict(event='BACKUP_SUBMITTED',bytes=100,monotonic_ns=6)]
        w=replay.window([(0,10),(4,15)],[request],events)
        self.assertEqual(w['seconds'],15/1e9)
        self.assertEqual(w['backup_submitted_bytes'],100)
        self.assertEqual(w['fully_contained_requests'],1)
        self.assertIsNone(w['cached_prompt_fraction'])
        self.assertIsNone(replay.window([(0,10)],[],None)['backup_submitted_bytes'])

    def test_comparison_rejects_different_plan_and_tool_surrogate(self):
        a=dict(plan_sha256='a',tool_workload='delay',token_pool_sha256='pool',
               replay_config={},server_info={},controller_initial={},source_versions={})
        b=copy.deepcopy(a);b['plan_sha256']='b'
        self.assertNotEqual(compare.signature(a),compare.signature(b))
        b=copy.deepcopy(a);b['tool_workload']='stream'
        self.assertNotEqual(compare.signature(a),compare.signature(b))

    def test_prefix_hit_does_not_claim_host_restore(self):
        trial=dict(requests=[dict(prompt_tokens=20,cached_tokens=10,output_tokens=2)],
                   transfer_counter_deltas={'sglang:load_back_bytes_total':None})
        replay.summarize_trial(trial)
        self.assertEqual(trial['server_prefix_hit_rate'],0.5)
        self.assertFalse(trial['host_restore_measurement_available'])
        self.assertFalse(trial['host_restore_observed'])


class LocalReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.log=self.root/'events.jsonl'
        self.control=controller.ActivityController('defer',log_path=self.log)
        self.control.serve(self.root/'control.sock');self.addCleanup(self.control.close)

    def test_subprocess_failure_ends_tool_without_losing_lease(self):
        plan=make_plan(self.root)
        args=types.SimpleNamespace(max_seconds=5,max_stream_processes=1,tool_workload='stream',
            control_socket=self.root/'control.sock',controller_mode='defer',threads=1,
            stream_binary=Path('/bin/false'),tool_timeout=3,control_timeout=10)
        runner=replay.Replay(args,plan,list(range(100,1000)))
        with self.assertRaises(subprocess.CalledProcessError):
            runner.tool(dict(index=0,name='surrogate',offset_seconds=0,duration_seconds=0),time.monotonic(),'a',0)
        self.assertEqual(self.control.snapshot()['active_tool_ids'],[])
        self.assertTrue(runner.cancel.is_set())
        self.assertIn('end_ack',runner.tools[0])

    def test_main_replay_with_local_activity_and_mocked_inference(self):
        plan=make_plan(self.root);p=self.root/'plan.json';p.write_text(json.dumps(plan))
        cache=set()
        def fake_generate(url,ids,length,timeout):
            start=time.monotonic_ns();cached=len(ids) if tuple(ids) in cache else 0;cache.add(tuple(ids))
            return [700]*length,dict(start_ns=start,end_ns=time.monotonic_ns(),latency_seconds=0.001,
                prompt_tokens=len(ids),output_tokens=length,cached_tokens=cached)
        info=dict(server_args=dict(enable_hierarchical_cache=True,hicache_write_policy='write_through',
                 enable_metrics=True,model_path='Qwen/Qwen3-0.6B',context_length=64))
        class FakeServer:
            def __init__(self,url): pass
            def get(self,path):
                if path=='/flush_cache': cache.clear()
                return types.SimpleNamespace(json=lambda:info)
            def settle(self): return dict.fromkeys(counters,0)
        tokenizer=types.SimpleNamespace(get_vocab=lambda:{str(i):i for i in range(100,500)},all_special_ids=[])
        transformers=types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a,**k:tokenizer))
        argv=['replay.py','--plan',str(p),'--controller-mode','defer','--control-socket',str(self.root/'control.sock'),
              '--output-dir',str(self.root/'results'),'--max-seconds','10']
        counters=('sglang:hicache_backup_bytes_total','sglang:load_back_bytes_total')
        with patch.object(replay,'Server',FakeServer),patch.object(replay,'generate',side_effect=fake_generate),\
             patch.object(replay,'command_output',return_value={}),patch.object(replay,'source_version',return_value={}),\
             patch.object(sys,'argv',argv),patch.dict(sys.modules,{'transformers':transformers}):
            replay.main()
        result=json.loads(next((self.root/'results').glob('trace-*.json')).read_text())
        trial=result['trials'][0]
        self.assertEqual(len(trial['requests']),6)
        self.assertEqual(len(trial['tools']),4)
        self.assertEqual(set(trial['windows']),{'tool','post_tool','whole'})
        self.assertEqual(self.control.snapshot()['active_tool_ids'],[])
        self.assertTrue(all(t.get('start_ack') and t.get('end_ack') for t in trial['tools']))
        self.assertEqual(sum(r['phase']=='post_tool_tail' for r in trial['requests']),2)
        self.assertTrue(all(r['start_ns']>=trial['trace_end_ns'] for r in trial['requests'] if r['phase']=='post_tool_tail'))
        self.assertGreater(trial['windows']['whole']['seconds'],trial['windows']['tool']['seconds'])
        if shutil.which('rsvg-convert'):
            # Exercise the delivered comparison program with synthetic measured
            # data, not a performance claim about either controller.
            normal=copy.deepcopy(result);normal['controller_mode']='normal'
            (self.root/'results/trace-normal-synthetic.json').write_text(json.dumps(normal))
            with patch.object(sys,'argv',['compare.py',str(self.root/'results'),'--output-dir',str(self.root/'plots')]):
                compare.main()
            pngs=list((self.root/'plots').glob('*.png'))
            self.assertEqual(len(pngs),4)
            self.assertTrue(all(p.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') for p in pngs))
            csv=(self.root/'plots/summary.csv').read_text()
            self.assertIn('post_tool.seconds',csv)
            self.assertIn('whole.seconds',csv)


if __name__=='__main__':
    unittest.main()
