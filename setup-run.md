# SGLang setup and first GPU inference run

Completed October 8, 2026, on PACE ICE with an NVIDIA L40S (46068 MiB GPU memory). SGLang release: `v0.5.21`; commit: `e00930c5489053f26d86b179cee0d087f846acbb`.

## 1. Request a GPU and enter the compute node

From an ICE login terminal:

```bash
salloc -p ice-gpu --gres=gpu:l40s:1 \
  --time=01:00:00 --mem=32G --cpus-per-task=4
```

Wait for Slurm to grant the allocation and report the node ready. If still on the login node, enter the allocation:

```bash
srun --pty bash
hostname
nvidia-smi
```

This run used job `6107916` on `atl1-1-03-004-21-0`. When connecting from a different login terminal, use the active job ID explicitly:

```bash
srun --jobid=<active-job-id> --pty bash
```

Job IDs change each run. The one-hour time limit begins when the allocation is granted.

## 2. Clone SGLang

```bash
cd ~/cs6222
git clone --branch v0.5.21 https://github.com/sgl-project/sglang.git
git -C sglang rev-parse HEAD > sglang-commit.txt
```

HiCache is included in SGLang; no separate clone is needed.

## 3. Create the Python environment and install

```bash
module load python/3.12.5 cuda/13.0.1
cd ~/cs6222
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
SGLANG_BUILD_RUST_EXTS=none python -m pip install -e ./sglang/python
```

The initial install without `SGLANG_BUILD_RUST_EXTS=none` failed because `cargo` was missing. This flag skips Rust extensions and allowed this initial inference setup to proceed. Rust-dependent features require a Rust toolchain and rebuilding those extensions later. Editable installation makes local Python source changes available without reinstalling.

Installation was performed in a separate login terminal. Files are shared between terminals, but loaded modules and environment activation are specific to each shell.

## 4. Activate the environment in the GPU terminal

```bash
module load python/3.12.5 cuda/13.0.1
source ~/cs6222/.venv/bin/activate
cd ~/cs6222

python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

Observed output:

```text
True
NVIDIA L40S
```

Lmod warned about unloaded `libxml2` and `xz` dependencies, but the GPU check and subsequent inference succeeded in this run.

## 5. Start Qwen

```bash
sglang serve Qwen/Qwen3-0.6B \
  --host 127.0.0.1 \
  --port 30000 \
  > server.log 2>&1 &

SERVER_PID=$!
tail -f server.log
```

The first launch downloaded model weights and captured CUDA graphs before the HTTP server became ready. During startup, the health check returned `000` and curl reported connection refused. The server later returned `200`.

Press Ctrl+C to stop `tail`; the background server continues running. Then inspect GPU use in the same terminal:

```bash
nvidia-smi
```

This run showed `sglang::scheduler` using about 40 GB GPU memory. Logs attributed about 1.18 GB to model weights and about 35.86 GB to the preallocated KV cache. GPU memory allocation alone does not establish server readiness.

## 6. Check readiness and send the test prompt

Run these from the same compute node as the server:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:30000/health
```

Once it returns `200`:

```bash
curl http://127.0.0.1:30000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-0.6B",
    "messages": [{"role": "user", "content": "Explain KV caching in two sentences."}],
    "chat_template_kwargs": {"enable_thinking": false},
    "max_tokens": 128,
    "temperature": 0
  }'
```

The server returned a completion with 20 prompt tokens and 28 completion tokens. Its answer confused LLM KV caching with a key-value database cache; this test established working inference, not answer quality.

`127.0.0.1` refers to the machine running curl. A curl command on the login node cannot reach this compute-node server through its own localhost.

## Useful follow-up commands

```bash
# Inspect startup errors or progress.
tail -n 60 server.log

# Record installed package versions.
python -m pip freeze > ~/cs6222/python-packages.txt

# Stop the background server from the shell that saved SERVER_PID.
kill "$SERVER_PID"

# Inspect or cancel an allocation.
squeue -u "$USER"
scancel <job-id>
```

## Next step

Enable HiCache with explicit GPU and host cache budgets that fit the allocation, then run a workload that verifies actual GPU-to-host and host-to-GPU KV transfers. HiCache was disabled in this run.
