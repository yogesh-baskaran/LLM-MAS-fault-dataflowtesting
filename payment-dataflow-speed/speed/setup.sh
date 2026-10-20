#!/usr/bin/env bash
# Run ONCE on an allocated compute node, not the submit node.
source "$(dirname -- "$0")/environment.sh"
require_compute
for file in requirements.txt package.json service/instrument.cjs service/trace.cjs service/server.cjs tests/test-service.cjs dataflow/preflight.py; do
  if [[ ! -f "$DF_ROOT/$file" ]]; then
    printf 'Missing testing-project file: %s\nTransfer this file from your local payment-dataflow-speed folder to the same location on SPEED, then rerun setup.\n' "$DF_ROOT/$file" >&2
    exit 2
  fi
done
load_modules
cd "$DF_ROOT"
python3 -m venv "$DF_ROOT/.venv"
"$DF_ROOT/.venv/bin/python" -m pip install -r "$DF_ROOT/requirements.txt"
"$DF_ROOT/.venv/bin/python" -m pip freeze > runtime/python-environment.txt

# HPC containers; no Docker daemon or root access needed on SPEED.
[[ -f images/node.sif ]] || singularity pull images/node.sif docker://node:22.16.0-bookworm-slim
[[ -f images/mongo.sif ]] || singularity pull images/mongo.sif docker://mongo:7.0
if [[ -z "$OLLAMA_BIN" ]]; then
  [[ -f images/ollama.sif ]] || singularity pull images/ollama.sif docker://ollama/ollama:latest
fi

# Copy the service dependencies to the testing project before installing anything.
mkdir -p runtime/payment-source
cp "$DF_REPO/src/paymentservice/package.json" runtime/payment-source/package.json
cp "$DF_REPO/src/paymentservice/package-lock.json" runtime/payment-source/package-lock.json
cp -R "$DF_REPO/src/paymentservice/proto" runtime/payment-source/
export SINGULARITYENV_npm_config_cache="$DF_ROOT/cache/npm"
singularity exec --bind "$DF_ROOT:$DF_ROOT" --pwd "$DF_ROOT/runtime/payment-source" images/node.sif npm ci --ignore-scripts
singularity exec --bind "$DF_ROOT:$DF_ROOT" --pwd "$DF_ROOT" images/node.sif npm install --ignore-scripts
singularity exec --bind "$DF_ROOT:$DF_ROOT","$DF_REPO:$DF_REPO:ro" --pwd "$DF_ROOT" images/node.sif node tests/test-service.cjs "$DF_REPO"
sha256sum images/*.sif > runtime/container-sha256.txt

# Pull model inside an allocation. No GPU inference or payment tests in setup.
export OLLAMA_HOST="127.0.0.1:$DF_OLLAMA_PORT"
export SINGULARITYENV_OLLAMA_HOST="$OLLAMA_HOST"
export SINGULARITYENV_OLLAMA_MODELS="$OLLAMA_MODELS"
"$DF_ROOT/.venv/bin/python" -m dataflow.preflight --ports "$DF_OLLAMA_PORT"
if [[ -n "$OLLAMA_BIN" ]]; then
  "$OLLAMA_BIN" serve > runtime/ollama-setup.log 2>&1 &
else
  singularity exec --bind "/speed-scratch/$USER:/speed-scratch/$USER" images/ollama.sif /bin/ollama serve > runtime/ollama-setup.log 2>&1 &
fi
DF_SETUP_PID=$!
trap 'kill "$DF_SETUP_PID" 2>/dev/null || true; wait "$DF_SETUP_PID" 2>/dev/null || true' EXIT
"$DF_ROOT/.venv/bin/python" -m dataflow.preflight --wait-url "http://$OLLAMA_HOST/api/tags" --attempts 120 --pid "$DF_SETUP_PID"
if [[ -n "$OLLAMA_BIN" ]]; then
  "$OLLAMA_BIN" pull "$DF_MODEL"
else
  singularity exec --bind "/speed-scratch/$USER:/speed-scratch/$USER" images/ollama.sif /bin/ollama pull "$DF_MODEL"
fi
echo 'Setup complete. In your existing compute allocation run: bash speed/job.sh'
echo 'For the additional agent suites, prepare once with: bash speed/setup-multi.sh'

