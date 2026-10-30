"""Synthetic scheduler snapshots; no cluster calls or allocations."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import resources
import setup


def node(name='node-a',gpu='l40s',state='MIXED',cpu_alloc=16,gpu_alloc=7,part='ice-gpu',memory='512G'):
    return (f'NodeName={name} Partitions={part} State={state} CoresPerSocket=16 Sockets=4 ThreadsPerCore=1 '
            f'CPUTot=64 CPUEfctv=64 CPUAlloc={cpu_alloc} MemSpecLimit=8192 '
            f'CfgTRES=cpu=64,mem={memory},gres/gpu=8,gres/gpu:{gpu}=8 '
            f'AllocTRES=cpu={cpu_alloc},mem=128G,gres/gpu={gpu_alloc},gres/gpu:{gpu}={gpu_alloc} '
            'FreeMem=999999 CPULoad=0.01')


class ResourceTests(unittest.TestCase):
    def inspect(self,text):
        return resources.inspect_nodes(text,'ice-gpu',16,'32G','l40s')

    def test_mixed_does_not_imply_a_free_gpu(self):
        self.assertFalse(self.inspect(node(gpu_alloc=8))['preferred_looks_available'])

    def test_gpu_without_cpus_or_memory_is_not_available(self):
        self.assertFalse(self.inspect(node(cpu_alloc=64))['preferred_looks_available'])
        self.assertFalse(self.inspect(node(memory='140G'))['preferred_looks_available'])

    def test_drained_reserved_down_and_other_partition_excluded(self):
        for state in ['IDLE+DRAIN','IDLE+RESERVED','DOWN','MIXED+MAINT','COMPLETING']:
            self.assertFalse(self.inspect(node(state=state))['preferred_looks_available'])
        self.assertFalse(self.inspect(node(part='coe-gpu'))['preferred_looks_available'])

    def test_preferred_and_compatible_alternative(self):
        result=self.inspect(node()+'\n'+node(name='node-b',gpu='h100'))
        self.assertTrue(result['preferred_looks_available'])
        self.assertEqual(result['alternatives'][0]['gpu'],'h100')
        self.assertFalse(result['preferred_nodes'][0]['numa_placement_verified'])

    def test_generic_allocated_gpu_count_used_conservatively(self):
        text=node(gpu_alloc=8).replace(',gres/gpu:l40s=8 FreeMem',' FreeMem')
        self.assertFalse(self.inspect(text)['preferred_looks_available'])

    def test_smaller_pilot_uses_single_snapshot_without_switching(self):
        args=SimpleNamespace(partition='ice-gpu',cpus=16,memory='32G',gpu='l40s')
        calls=[]
        def run(command,timeout): calls.append(command);return node(gpu='h100',cpu_alloc=60)
        result=resources.snapshot(run,args)
        self.assertEqual(len(calls),1)
        self.assertFalse(result['preferred_looks_available'])
        self.assertEqual(result['smaller_cpu_alternatives'][0]['cpus'],4)
        self.assertEqual(args.cpus,16)

    def test_busy_resources_do_not_submit_or_show_unrelated_inventory(self):
        args=SimpleNamespace(job_id=None,enter=False,queue=False)
        unavailable=dict(preferred_looks_available=False,preferred_gpu='l40s',cpus=16,memory='32G',
                         partition='ice-gpu',alternatives=[],smaller_cpu_alternatives=[],caveat='snapshot')
        with tempfile.TemporaryDirectory() as d,patch.dict(setup.os.environ,{},clear=True),\
             patch.object(setup,'snapshot',return_value=unavailable),patch.object(setup.os,'execvp') as execute,\
             contextlib.redirect_stdout(io.StringIO()) as text:
            with self.assertRaises(SystemExit) as stopped:
                setup.manage(args,Path(d))
            self.assertEqual(stopped.exception.code,3)
        execute.assert_not_called()
        self.assertIn('No job was submitted',text.getvalue())


if __name__=='__main__':
    unittest.main()
