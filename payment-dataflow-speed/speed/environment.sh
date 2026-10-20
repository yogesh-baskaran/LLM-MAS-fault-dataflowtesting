#!/usr/bin/env bash
# Sourced by the SPEED scripts. Does not execute experiments.
set -euo pipefail
DF_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$DF_SCRIPT_ROOT/config.env" ]]; then
  echo 'Copy config.env.example to config.env and set the SPEED repository path. Account settings are optional inside salloc.' >&2
  exit 2
fi
source "$DF_SCRIPT_ROOT/config.env"
# Defaults preserve older config.env files transferred to SPEED.
: "${DF_AGENTS:=payment}" "${DF_CART_PORT:=50062}" "${DF_CURRENCY_PORT:=50063}"
: "${DF_EMAIL_PORT:=50064}" "${DF_RECOMMENDATION_PORT:=50065}" "${DF_CHECKOUT_PORT:=50066}"
: "${DF_CATALOG_PORT:=50067}" "${DF_SHIPPING_PORT:=50068}"
: "${DF_MAX_NODE_VISITS:=3}" "${DF_MAX_PATHS:=10000}"
for name in DF_REPO DF_ROOT; do
  value="${!name:-}"
  if [[ -z "$value" || "$value" == *FILL_ME* ]]; then
    echo "Fill $name in config.env before proceeding." >&2
    exit 2
  fi
done
DF_ROOT="$(realpath -- "$DF_ROOT")"
DF_REPO="$(realpath -- "$DF_REPO")"
# SPEED scratch may be a symlink to another mount. Compare canonical paths on
# both sides, and use the actual login identity rather than an inherited USER.
DF_SCRATCH_ROOT="$(realpath -- "/speed-scratch/$(id -un)")"
if [[ "$DF_ROOT" != "$DF_SCRATCH_ROOT/"* ]]; then
  printf 'DF_ROOT must be inside your scratch directory.\nResolved project: %s\nResolved scratch: %s\n' "$DF_ROOT" "$DF_SCRATCH_ROOT" >&2
  exit 2
fi
export DF_ROOT DF_REPO
export TMPDIR="$DF_ROOT/cache/tmp" TMP="$DF_ROOT/cache/tmp"
export PIP_CACHE_DIR="$DF_ROOT/cache/pip"
export SINGULARITY_CACHEDIR="$DF_ROOT/cache/singularity"
export SINGULARITY_TMPDIR="$DF_ROOT/cache/singularity-tmp"
export OLLAMA_MODELS
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR" "$SINGULARITY_CACHEDIR" "$SINGULARITY_TMPDIR" "$OLLAMA_MODELS" "$DF_ROOT/results" "$DF_ROOT/runtime" "$DF_ROOT/images"

require_compute() {
  if [[ -z "${SLURM_JOB_ID:-}" || "$(hostname -s)" == speed-submit* ]]; then
    echo 'Run this script on a compute node within a SLURM allocation.' >&2
    exit 2
  fi
}

load_modules() {
  if ! type module >/dev/null 2>&1; then
    source "$MODULE_INIT"
  fi
  module load "$PYTHON_MODULE"
  module load "$SINGULARITY_MODULE"
}

