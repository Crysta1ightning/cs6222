#!/bin/bash -l
#SBATCH --partition=ice-gpu
#SBATCH --gres=gpu:1
#SBATCH --gres-flags=enforce-binding
#SBATCH --constraint=gpu-l40s|gpu-a40|gpu-h100|gpu-h200
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --hint=nomultithread
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --job-name=cs6222-stability
#SBATCH --output=/home/hice1/twu474/cs6222/results/reproducible-batch-%j.log

# All phases share one allocation/GPU; Slurm releases it when this script exits.
set -euo pipefail
cd /home/hice1/twu474/cs6222
srun --cpu-bind=none -N1 -n1 -c8 bash backup-policy/run-stability.sh
