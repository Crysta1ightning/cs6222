"""CPU-only tests: no Slurm submissions, package installs, or CUDA execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import setup
from topology import PlacementError,choose,parse_list,pci_path


def rows(start,count,node,socket=0):
    return [dict(cpu=start+i,node=node,socket=socket,core=i) for i in range(count)]


def options(root,job=None):
    return SimpleNamespace(project=root,cpus=16,server_cores=4,gpu='l40s',memory='32G',
        time='01:00:00',partition='ice-gpu',job_id=job,numa_node=None,allow_remote_numa=False,
        wait_seconds=1,setup_timeout=1,repair_install=False,new_allocation=False,enter=False,queue=False)


class PlacementTests(unittest.TestCase):
    def test_new_allocation_gets_different_actual_ids(self):
        for start,node in [(32,2),(80,3),(128,0)]:
            result=choose(rows(start,16,node),16,4,node,{node})
            self.assertEqual(result['server_cpus'],list(range(start,start+4)))
            self.assertEqual(result['tool_cpus'],list(range(start+4,start+16)))
            self.assertEqual(result['tool_threads'],12)
            self.assertEqual(result['numa_node'],node)

    def test_smt_siblings_do_not_count_twice(self):
        topology=rows(0,8,0)+[dict(cpu=64+i,node=0,socket=0,core=i) for i in range(8)]
        with self.assertRaises(PlacementError):
            choose(topology,16,4,0,{0})

    def test_split_allocation_rejected_without_silent_shrinking(self):
        with self.assertRaisesRegex(PlacementError,'physical cores by node'):
            choose(rows(0,8,0)+rows(8,8,1,1),16,4,0,{0,1})

    def test_gpu_local_preferred_over_lower_cpu_ids(self):
        result=choose(rows(0,16,0)+rows(32,16,2,1),16,4,2,{0,2})
        self.assertEqual(result['server_cpus'],[32,33,34,35])
        self.assertTrue(result['gpu_locality_verified'])

    def test_remote_requires_explicit_choice(self):
        topology=rows(0,16,0)
        with self.assertRaises(PlacementError):
            choose(topology,16,4,1,{0,1})
        result=choose(topology,16,4,1,{0,1},allow_remote=True)
        self.assertFalse(result['gpu_locality_verified'])

    def test_unknown_gpu_node_and_memory_permissions(self):
        with self.assertRaises(PlacementError):
            choose(rows(0,16,0),16,4,None,{0})
        result=choose(rows(0,16,0),16,4,None,{0},requested_node=0)
        self.assertFalse(result['gpu_locality_verified'])
        with self.assertRaises(PlacementError):
            choose(rows(0,16,0),16,4,0,{1})

    def test_list_and_pci_normalization(self):
        self.assertEqual(parse_list('0-2,8,10-11'),{0,1,2,8,10,11})
        self.assertEqual(pci_path('00000000:CA:00.0'),'0000:ca:00.0')
        for bad in ['9-1','1;echo x','x']:
            with self.assertRaises(ValueError): parse_list(bad)


class SetupTests(unittest.TestCase):
    def test_slurm_blackwell_name_matches_nvidia_name(self):
        self.assertTrue(setup.gpu_matches('rtx_pro_6000_blackwell','NVIDIA RTX PRO 6000 Blackwell Server Edition'))
        self.assertTrue(setup.gpu_matches('h100','NVIDIA H100 80GB HBM3'))
        self.assertFalse(setup.gpu_matches('h100','NVIDIA H200'))

    def test_dry_run_defaults_and_overrides_do_not_submit(self):
        for flags,count,gpu in [([],16,'l40s'),(['--cpus','32','--gpu','h100','--memory','128G'],32,'h100')]:
            stream=io.StringIO()
            with patch.object(sys,'argv',['setup.py','--dry-run']+flags),patch.object(setup,'manage') as manage,contextlib.redirect_stdout(stream):
                setup.main()
            data=json.loads(stream.getvalue())
            manage.assert_not_called()
            self.assertIn('--cpus-per-task='+str(count),data['allocation_command'])
            self.assertIn('--gres=gpu:'+gpu+':1',data['allocation_command'])
            self.assertIn('--nodes=1',data['allocation_command'])
            self.assertEqual(data['allocation_command'][0],'salloc')

    def test_pending_single_node_range_and_owner(self):
        raw=f'JobId=42 UserId=me({os.getuid()}) JobState=PENDING NumNodes=1-1 NumCPUs=16 Reason=Resources'
        with patch.object(setup,'run',return_value=raw):
            fields,_=setup.job_info('42')
        self.assertEqual(fields['JobState'],'PENDING')
        with patch.object(setup,'run',return_value=raw.replace(f'({os.getuid()})','(999999999)')):
            with self.assertRaisesRegex(RuntimeError,'owned'): setup.job_info('42')

    def test_gpu_matches_uuid_not_physical_index_zero(self):
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,device_count=lambda:1,
            get_device_properties=lambda n:SimpleNamespace(uuid='GPU-aaaa',name='NVIDIA L40S')))
        inventory='0, GPU-bbbb, 00000000:21:00.0, NVIDIA L40S, 46068\n3, GPU-aaaa, 00000000:81:00.0, NVIDIA L40S, 46068'
        with patch.dict(sys.modules,{'torch':torch}),patch.object(setup,'run',return_value=inventory):
            gpu=setup.gpu_properties()
        self.assertEqual(gpu['nvidia_index'],3)
        self.assertEqual(gpu['uuid'],'GPU-aaaa')

    def test_ambiguous_gpu_uuid_refused(self):
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,device_count=lambda:1,
            get_device_properties=lambda n:SimpleNamespace(name='NVIDIA L40S')))
        with patch.dict(sys.modules,{'torch':torch}),patch.object(setup,'run',return_value='0, GPU-aaaa, 0000:81:00.0, NVIDIA L40S, 46068'):
            with self.assertRaisesRegex(RuntimeError,'no physical GPU index was guessed'):
                setup.gpu_properties()

    def test_new_setup_uses_salloc_and_existing_setup_enters_with_pty(self):
        for existing in [None,'42']:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);args=options(root,existing)
                fields=dict(JobState='RUNNING',NumCPUs='16')
                with patch.dict(os.environ,{},clear=True),patch.object(setup,'os') as os_mock,\
                     patch.object(setup,'snapshot',return_value=dict(preferred_looks_available=True)),\
                     patch.object(setup,'job_info',return_value=(fields,'')),contextlib.redirect_stdout(io.StringIO()):
                    os_mock.environ={}
                    setup.manage(args,root)
                command=os_mock.execvp.call_args.args[1]
                self.assertEqual(command[0],'salloc' if existing is None else 'srun')
                self.assertIn('--enter' if existing is None else '--pty',command)
                self.assertNotIn('sbatch',command)

    def test_success_saves_second_terminal_command_and_shell_environment(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);args=options(root,'42')
            fields=dict(JobState='RUNNING',NumCPUs='32',TimeLimit='01:00:00',MinMemoryNode='32G')
            def compute(args,root):
                setup.save(root/'settings.json',dict(status='ready',job_id='42'))
                (root/'env.sh').write_text('export EXP_THREADS=12\n')
            with patch.object(setup,'job_info',return_value=(fields,'')),patch.object(setup,'compute',side_effect=compute),\
                 patch.object(setup,'summary'),contextlib.redirect_stdout(io.StringIO()):
                setup.finish_compute(args,root)
            report=json.loads((root/'settings.json').read_text())
            self.assertEqual(report['slurm_job']['MinMemoryNode'],'32G')
            command=report['second_terminal_command']
            self.assertIn('--jobid=42',command)
            self.assertIn('--overlap',command)
            self.assertIn('--cpus-per-task=32',command)
            self.assertIn('--pty',command)
            self.assertIn(str(root/'session.bashrc'),command)
            self.assertIn('source '+str(root/'env.sh'),(root/'session.bashrc').read_text())
            self.assertTrue((root/'env.sh').exists())
            self.assertEqual(json.loads((root/'allocation.json').read_text())['status'],'ready')

    def test_interactive_session_automatically_loads_exports(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);bin_dir=root/'bin';bin_dir.mkdir()
            fake=bin_dir/'bash'
            fake.write_text('#!/bin/bash\nif [[ "$1" == -l ]]; then exit 0; fi\nsource "$2"\nprintf "loaded=%s\\n" "$EXP_THREADS"\n')
            fake.chmod(0o700)
            (root/'env.sh').write_text('export EXP_THREADS=12\n')
            (root/'session.bashrc').write_text('source '+str(root/'env.sh')+'\n')
            (root/'settings-summary.txt').write_text('READY: test fixture\n')
            script=Path(setup.HERE)/'session.sh'
            result=subprocess.run(['/bin/bash',str(script),str(root),'0','5',str(root),'unused'],
                                  env=dict(os.environ,PATH=str(bin_dir)+':'+os.environ['PATH']),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('loaded=12',result.stdout)

    def test_source_guard_rejects_different_allocation(self):
        values=dict(EXP_JOB_ID='999999999',EXP_HOSTNAME='wrong-host',EXP_SERVER_CPUS='0',EXP_TOOL_CPUS='1',EXP_NUMA_NODE='0')
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'env.sh';file.write_text(setup.source_guard(values)+'export PACE_TEST_APPLIED=1\n')
            result=subprocess.run(['bash','-c','source "$1" && echo applied','bash',str(file)],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('applied',result.stdout)


if __name__=='__main__':
    unittest.main()
