#!/encs/bin/bash
DF_ALLOCATED_TMP="${TMPDIR:-}"
source "$(dirname -- "$0")/environment.sh"
require_compute
load_modules
cd "$DF_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export FAULT_MODE=NONE
DF_PYTHON="$DF_ROOT/.venv/bin/python"
DF_JOB_RUNTIME="$DF_ROOT/runtime/job-$SLURM_JOB_ID-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$DF_JOB_RUNTIME/service" "$DF_JOB_RUNTIME/mongo"
DF_MONGO_DATA="$DF_JOB_RUNTIME/mongo"
if [[ -n "$DF_ALLOCATED_TMP" && -d "$DF_ALLOCATED_TMP" && -w "$DF_ALLOCATED_TMP" && "$DF_ALLOCATED_TMP" != /speed-scratch/* ]]; then
  DF_MONGO_DATA="$DF_ALLOCATED_TMP/payment-dataflow-$SLURM_JOB_ID-$$/mongo"
  mkdir -p "$DF_MONGO_DATA"
fi
export DF_PAYMENT_SOURCE="$DF_ROOT/runtime/payment-source"
export DF_SERVICE_EVENTS="$DF_JOB_RUNTIME/service-events.jsonl"
export DF_PAYMENT_PORT
export OLLAMA_HOST="127.0.0.1:$DF_OLLAMA_PORT"
export OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1
export SINGULARITYENV_OLLAMA_HOST="$OLLAMA_HOST"
export SINGULARITYENV_OLLAMA_MODELS="$OLLAMA_MODELS"
export SINGULARITYENV_OLLAMA_NUM_PARALLEL=1
export SINGULARITYENV_OLLAMA_MAX_LOADED_MODELS=1
export SINGULARITYENV_NODE_PATH="$DF_PAYMENT_SOURCE/node_modules"
export SINGULARITYENV_DF_PAYMENT_SOURCE="$DF_PAYMENT_SOURCE"
export SINGULARITYENV_DF_SERVICE_EVENTS="$DF_SERVICE_EVENTS"
export SINGULARITYENV_DF_PAYMENT_PORT="$DF_PAYMENT_PORT"

# Each job owns its listeners. A collision fails before any service starts.
"$DF_PYTHON" -m dataflow.preflight --ports "$DF_PAYMENT_PORT" "$DF_MONGO_PORT" "$DF_OLLAMA_PORT"
nvidia-smi -L > "$DF_JOB_RUNTIME/gpu.txt"
nvidia-smi > "$DF_JOB_RUNTIME/gpu-details.txt"
cp runtime/container-sha256.txt runtime/python-environment.txt "$DF_JOB_RUNTIME/"
cmp "$DF_REPO/src/paymentservice/package.json" "$DF_PAYMENT_SOURCE/package.json" || { echo 'Service dependencies changed; rerun setup.sh.' >&2; exit 2; }
cmp "$DF_REPO/src/paymentservice/proto/demo.proto" "$DF_PAYMENT_SOURCE/proto/demo.proto" || { echo 'Service proto changed; rerun setup.sh.' >&2; exit 2; }
singularity exec --bind "$DF_ROOT:$DF_ROOT:rw","$DF_REPO/src:$DF_REPO/src:ro" --pwd "$DF_ROOT" images/node.sif \
  node service/instrument.cjs "$DF_REPO/src/paymentservice/charge.js" "$DF_JOB_RUNTIME/service"

DF_OWNED_PIDS=()
cleanup() {
  for pid in "${DF_OWNED_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${DF_OWNED_PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  if [[ "$DF_MONGO_DATA" != "$DF_JOB_RUNTIME/mongo" ]]; then
    cp -a "$DF_MONGO_DATA/." "$DF_JOB_RUNTIME/mongo/" || echo 'MongoDB archival failed; JSON results remain available.' >&2
  fi
}
trap cleanup EXIT
singularity exec --bind "$DF_ROOT:$DF_ROOT","$DF_MONGO_DATA:$DF_MONGO_DATA" images/mongo.sif mongod \
  --dbpath "$DF_MONGO_DATA" --bind_ip 127.0.0.1 --port "$DF_MONGO_PORT" \
  --logpath "$DF_JOB_RUNTIME/mongo.log" > "$DF_JOB_RUNTIME/mongo-console.log" 2>&1 &
DF_OWNED_PIDS+=("$!")
if [[ -n "$OLLAMA_BIN" ]]; then
  "$OLLAMA_BIN" serve > "$DF_JOB_RUNTIME/ollama.log" 2>&1 &
else
  singularity exec --nv --bind "/speed-scratch/$USER:/speed-scratch/$USER" images/ollama.sif /bin/ollama serve > "$DF_JOB_RUNTIME/ollama.log" 2>&1 &
fi
DF_OLLAMA_PID=$!
DF_OWNED_PIDS+=("$DF_OLLAMA_PID")
singularity exec --bind "$DF_ROOT:$DF_ROOT" --pwd "$DF_JOB_RUNTIME/service" images/node.sif \
  node server.cjs > "$DF_JOB_RUNTIME/paymentservice.log" 2>&1 &
DF_SERVICE_PID=$!
DF_OWNED_PIDS+=("$DF_SERVICE_PID")
"$DF_PYTHON" -m dataflow.preflight --wait-url "http://$OLLAMA_HOST/api/tags" --attempts 120 --pid "$DF_OLLAMA_PID"
"$DF_PYTHON" -m dataflow.preflight --wait-port "$DF_PAYMENT_PORT" --attempts 60 --pid "$DF_SERVICE_PID"
"$DF_PYTHON" -m dataflow.preflight --wait-port "$DF_MONGO_PORT" --attempts 60

# No dashboard server is needed: each run exports a self-contained dashboard.html.
srun --overlap --ntasks=1 "$DF_PYTHON" -m dataflow.runner --repo "$DF_REPO" --output "$DF_ROOT/results" \
  --service-catalog "$DF_JOB_RUNTIME/service/service-catalog.json" \
  --service-events "$DF_SERVICE_EVENTS" --payment-target "127.0.0.1:$DF_PAYMENT_PORT" \
  --mongo-uri "mongodb://127.0.0.1:$DF_MONGO_PORT" --ollama-url "http://$OLLAMA_HOST" \
  --model "$DF_MODEL" --context "$DF_CONTEXT" --rounds "$DF_ROUNDS" \
  --generated-per-round "$DF_GENERATED_PER_ROUND"

