#!/usr/bin/env bash
# Lightweight submission only; safe to run on a submit node.
source "$(dirname -- "$0")/environment.sh"
for name in SPEED_ACCOUNT SPEED_PARTITION; do
  value="${!name:-}"
  if [[ -z "$value" || "$value" == *FILL_ME* ]]; then
    echo "A new batch allocation requires $name. To reuse your existing salloc allocation, run bash speed/job.sh instead." >&2
    exit 2
  fi
done
options=(--account="$SPEED_ACCOUNT" --partition="$SPEED_PARTITION"
  --nodes=1 --ntasks=1 --cpus-per-task="$SPEED_CPUS" --gpus=1
  --mem="$SPEED_MEMORY" --time="$SPEED_TIME" --job-name=payment-dataflow
  --chdir="$DF_ROOT" --output="$DF_ROOT/results/slurm-%j.out"
  --error="$DF_ROOT/results/slurm-%j.err")
[[ -z "$SPEED_CONSTRAINT" ]] || options+=(--constraint="$SPEED_CONSTRAINT")
sbatch "${options[@]}" "$DF_ROOT/speed/job.sh"

