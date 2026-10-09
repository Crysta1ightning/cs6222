# PACE ICE setup

We will use an NVIDIA L40S GPU for the initial setup. Current allocation: `6107916` on `atl1-1-03-004-21-0` (4 CPUs, 32 GB host RAM, one hour).

## Request a GPU

```bash
salloc -p ice-gpu --gres=gpu:l40s:1 \
  --time=01:00:00 --mem=32G --cpus-per-task=4
```

Once allocated, open a shell on the compute node:

```bash
srun --pty bash
```

## Check the compute environment

```bash
nvidia-smi
nvidia-smi topo -m
lscpu
free -h
module avail cuda python
```

Check the assigned GPU's memory and driver with `nvidia-smi`.

## Manage the job

```bash
squeue -u "$USER"
scancel <job-id>
```
