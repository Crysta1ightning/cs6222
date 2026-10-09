# Memory-bandwidth interference experiment

Question: do GPU→host KV backups reduce CPU memory bandwidth on our allocated node?

Three conditions:

| Condition | CPU workload | Inference | HiCache |
| --- | --- | --- | --- |
| `memory-only` | STREAM | Server stopped | Off |
| `no-hicache` | STREAM | Fresh 2048-token requests | Off |
| `hicache` | STREAM | Fresh 2048-token requests | Write-through backups |

This is a synthetic feasibility check. It does not demonstrate slowdown of real tools or benefit from a scheduling controller.

## 1. Prepare one compute-node allocation

From the ICE login terminal:

```bash
salloc -p ice-gpu --gres=gpu:l40s:1 \
  --time=02:00:00 --mem=32G --cpus-per-task=4
srun --pty bash
```

If you already have a working compute-node shell, keep it and the current allocation. Run all conditions on that same node and in that same shell so CPU placement stays consistent.

```bash
module load python/3.12.5 cuda/13.0.1
source ~/cs6222/.venv/bin/activate
cd ~/cs6222
hostname
nvidia-smi
lscpu
bash memory-experiment/build.sh
```

If `gcc` is unavailable, inspect `module avail gcc` and load an available GCC module first. No new Python packages are needed beyond the existing SGLang environment.

We use the unmodified official [STREAM C benchmark](https://www.cs.virginia.edu/stream/FTP/Code/stream.c), downloaded from the [STREAM project](https://www.cs.virginia.edu/stream/). Source attribution/license remains in `stream.c`. The build uses three 512 MiB double-precision arrays (1.5 GiB total) and 50 iterations. Verify each array exceeds four times the relevant last-level cache before changing hardware; adjust the build constants if necessary.

Use `--threads 3` in every condition. This limits the benchmark to three threads in our four-CPU allocation; it does not exclusively reserve the fourth CPU for SGLang. CPU affinity, NUMA policy, and GPU topology are saved with results. We are testing our allocation, not claiming to saturate all memory channels on the entire node.

## 2. Condition A: memory workload alone

Stop the existing server first. If its PID was saved in this shell:

```bash
kill "$SERVER_PID"
wait "$SERVER_PID"
```

`wait` may report a termination status. If the PID variable is missing, identify your server with `jobs -l` or `ps -u "$USER" -o pid,args`; stop only your server, not the entire allocation. Check that its scheduler process has exited before running the baseline.

```bash
python memory-experiment/run.py --mode memory-only --trials 5 --threads 3
```

The runner warms STREAM first, then records five trials. Each trial prints average Triad bandwidth; higher GB/s is better. Keep the saved JSON filename.

## 3. Condition B: memory workload + inference without HiCache

```bash
sglang serve Qwen/Qwen3-0.6B \
  --host 127.0.0.1 --port 30000 \
  --max-total-tokens 8192 --context-length 4096 \
  --enable-metrics --disable-cuda-graph \
  > memory-no-hicache-server.log 2>&1 &
SERVER_PID=$!
tail -f memory-no-hicache-server.log
```

Once ready, Ctrl+C stops viewing the log. Check health and run:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:30000/health
# Run only after the health response is 200.
python memory-experiment/run.py --mode no-hicache --trials 5 --threads 3
```

The runner verifies that HiCache is disabled, then sends one fresh-prompt request at a time during STREAM. It records completed request rate and transfer counters.

## 4. Condition C: memory workload + inference with HiCache

Stop the previous server and wait for it to exit:

```bash
kill "$SERVER_PID"
wait "$SERVER_PID"
```

Start the same server configuration with the HiCache options added:

```bash
sglang serve Qwen/Qwen3-0.6B \
  --host 127.0.0.1 --port 30000 \
  --max-total-tokens 8192 --context-length 4096 \
  --enable-metrics --disable-cuda-graph \
  --enable-hierarchical-cache \
  --hicache-size 4 --hicache-write-policy write_through \
  > memory-hicache-server.log 2>&1 &
SERVER_PID=$!
tail -f memory-hicache-server.log
```

After readiness, Ctrl+C stops viewing logs:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:30000/health
# Run only after the health response is 200.
python memory-experiment/run.py --mode hicache --trials 5 --threads 3
```

Fresh request IDs are placed before the long prompt body, so the workload creates new KV states and backups rather than cycling cached prompts. No cache flush is needed for this runner. Only this runner should issue requests during a measurement.

The runner warms inference before measurement, stops submitting after each trial, waits for the final request, then requires five seconds of unchanged transfer counters. This reduces accounting spillover but does not prove physical transfer completion or exact overlap. Failures and partial results are saved instead of silently discarded.

## 5. Compare results

### Inference-alone baselines

To measure whether STREAM also slows inference, keep the appropriate server running and run the same fresh-prompt workload without STREAM:

```bash
# With the HiCache-disabled server ready:
python memory-experiment/run.py --mode no-hicache --inference-only --seconds 30 --trials 5 --threads 3

# With the HiCache-enabled server ready:
python memory-experiment/run.py --mode hicache --inference-only --seconds 30 --trials 5 --threads 3
```

These commands do not launch the memory workload. Both retain the same request generation, two-request warmup, and fully-contained-request counting used in the concurrent measurements. Include their JSON files in `summarize.py` to calculate inference throughput change within each HiCache setting. Prefer rerunning concurrent measurements next to the baselines in the same allocation if earlier runs occurred under different conditions. This measures fresh-prompt prefill-dominated request throughput, not general decoding token throughput.

Results and raw STREAM logs are saved in `cs6222/results`. Pass the three exact JSON paths printed by the runner:

```bash
python memory-experiment/summarize.py \
  results/<memory-only-result>.json \
  results/<no-hicache-result>.json \
  results/<hicache-result>.json
```

Use `--trials 1` in all three runs for a quick setup pilot. Keep pilots separate from the five-trial experiment. Before treating a difference as reproducible, repeat the server blocks in reverse order (HiCache then no-HiCache) and repeat the server-stopped baseline at the end to assess drift.

The summary uses logical bytes moved divided by each kernel's average time, then the median across trials. STREAM's original best-rate values and all raw output are also retained. These are application-level bandwidth estimates, not hardware DRAM-controller measurements; write allocation and other physical traffic are not included.

Interpretation:

- If both inference conditions reduce bandwidth similarly, the effect may be inference's CPU work rather than KV backups.
- If the HiCache condition consistently reduces bandwidth more, with confirmed backup bytes and comparable inference activity, investigate transfer-related interference.
- If HiCache causes no reproducible additional degradation, the suspected mechanism is not established in this allocation.

Inference is closed-loop, so the achieved request rate may differ between server modes. The runner reports it; a large difference prevents a clean causal interpretation and calls for a follow-up at a matched request rate. Transfer counters bracket the entire benchmark process, including array initialization, and do not establish exact per-kernel overlap. Shared-node activity and NUMA placement remain possible sources of noise.

Even a positive synthetic result needs validation on real tools before implementing the scheduling controller. If the initial three-thread result is negative, do not increase pressure indefinitely: first examine CPU allocation, topology, achieved transfer rate, and whether the experiment represents useful agent workloads.
