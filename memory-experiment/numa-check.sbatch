#!/bin/bash -l
#SBATCH --partition=ice-gpu
#SBATCH --gres=gpu:l40s:1
#SBATCH --gres-flags=enforce-binding
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --hint=nomultithread
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --job-name=cs6222-numa-check
#SBATCH --output=/home/hice1/twu474/cs6222/results/numa-validation-%j.log

set -euo pipefail
cd /home/hice1/twu474/cs6222
module load python/3.12.5 cuda/13.0.1
source .venv/bin/activate
srun --cpu-bind=none -N1 -n1 -c16 python -u memory-experiment/check-numa.py
