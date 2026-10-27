#!/encs/bin/bash
DF_ALLOCATED_TMP="${TMPDIR:-}"
source "$(dirname -- "$0")/environment.sh"
require_compute
load_modules
cd "$DF_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export FAULT_MODE=NONE
export BOUNDARY_EVENTS_ENABLED=0
unset BOUNDARY_EVENTS_FILE
DF_PYTHON="$DF_ROOT/.venv/bin/python"
DF_JOB_RUNTIME="$DF_ROOT/runtime/job-$SLURM_JOB_ID-$(date -u +%Y%m%dT%H%M%SZ)-$$"
export DF_ACTIVE_TEST="$DF_JOB_RUNTIME/active-test.txt"
export SINGULARITYENV_DF_ACTIVE_TEST="$DF_ACTIVE_TEST"
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
"$DF_PYTHON" -m dataflow.preflight --ports "$DF_PAYMENT_PORT" "$DF_MONGO_PORT" "$DF_OLLAMA_PORT" "$DF_CART_PORT" "$DF_CURRENCY_PORT" "$DF_EMAIL_PORT" "$DF_RECOMMENDATION_PORT" "$DF_CHECKOUT_PORT" "$DF_CATALOG_PORT" "$DF_SHIPPING_PORT"
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

# Additional services are job-local and sequentially correlated by test ID.
DF_SELECTED_AGENTS="${1:-all}"
"$DF_PYTHON" -c 'from dataflow.suite import select_agents; import sys; select_agents(sys.argv[1])' "$DF_SELECTED_AGENTS"
export DF_ACTIVE_TEST="$DF_JOB_RUNTIME/active-test.txt"
printf 'setup\n' > "$DF_ACTIVE_TEST"
export SINGULARITYENV_DF_ACTIVE_TEST="$DF_ACTIVE_TEST"
mkdir -p "$DF_JOB_RUNTIME/currency" "$DF_JOB_RUNTIME/cart" "$DF_JOB_RUNTIME/checkout"
export DF_CURRENCY_SOURCE="$DF_ROOT/runtime/currency-source"
for file in server.js package.json package-lock.json data/currency_conversion.json proto/demo.proto; do
 cmp "$DF_REPO/src/currencyservice/$file" "$DF_CURRENCY_SOURCE/$file" || { echo 'Currency source/dependencies changed; rerun setup-multi.sh.' >&2; exit 2; }
done
export DF_CURRENCY_ORIGINAL="$DF_CURRENCY_SOURCE/server.js"
export DF_CURRENCY_CATALOG="$DF_JOB_RUNTIME/currency/catalog.json"
export DF_CURRENCY_PORT
SINGULARITYENV_NODE_PATH="$DF_CURRENCY_SOURCE/node_modules" \
SINGULARITYENV_DF_SERVICE_EVENTS="$DF_JOB_RUNTIME/currency/events.jsonl" \
singularity exec --bind "$DF_ROOT:$DF_ROOT" --pwd "$DF_ROOT" images/node.sif \
 node instrumentation/javascript/currency.cjs > "$DF_JOB_RUNTIME/currency.log" 2>&1 &
DF_OWNED_PIDS+=("$!")
"$DF_PYTHON" -m dataflow.services --repo "$DF_REPO" --active-test "$DF_ACTIVE_TEST" \
 --events "$DF_JOB_RUNTIME/rpc.jsonl" --catalog-port "$DF_CATALOG_PORT" --shipping-port "$DF_SHIPPING_PORT" \
 --email-port "$DF_EMAIL_PORT" --recommendation-port "$DF_RECOMMENDATION_PORT" > "$DF_JOB_RUNTIME/services.log" 2>&1 &
DF_OWNED_PIDS+=("$!")
for port in "$DF_CURRENCY_PORT" "$DF_CATALOG_PORT" "$DF_SHIPPING_PORT" "$DF_EMAIL_PORT" "$DF_RECOMMENDATION_PORT"; do
 "$DF_PYTHON" -m dataflow.preflight --wait-port "$port" --attempts 60
done
case ",$DF_SELECTED_AGENTS," in
 *,cart,*|*,checkout,*|,all,)
  "$DF_PYTHON" -m dataflow.verify_native --root "$DF_ROOT" --repo "$DF_REPO"
  cp "$DF_ROOT/runtime/native/cart/catalog.json" "$DF_JOB_RUNTIME/cart/catalog.json"
  SINGULARITYENV_DF_NATIVE_EVENTS="$DF_JOB_RUNTIME/cart/events.jsonl" \
  SINGULARITYENV_DF_CART_PORT="$DF_CART_PORT" SINGULARITYENV_OLLAMA_URL="http://$OLLAMA_HOST" \
  SINGULARITYENV_OLLAMA_MODEL="$DF_MODEL" singularity exec --bind "$DF_ROOT:$DF_ROOT" \
    --pwd "$DF_ROOT/runtime/native/cart" images/dotnet.sif dotnet out/cartservice.dll > "$DF_JOB_RUNTIME/cart.log" 2>&1 &
  DF_OWNED_PIDS+=("$!")
  "$DF_PYTHON" -m dataflow.preflight --wait-port "$DF_CART_PORT" --attempts 90
 ;;
esac
case ",$DF_SELECTED_AGENTS," in
 *,checkout,*|,all,)
  cp "$DF_ROOT/runtime/native/checkout/catalog.json" "$DF_JOB_RUNTIME/checkout/catalog.json"
  pushd "$DF_JOB_RUNTIME/checkout" >/dev/null
  DF_NATIVE_EVENTS="$DF_JOB_RUNTIME/checkout/events.jsonl" DF_CHECKOUT_PORT="$DF_CHECKOUT_PORT" \
  CART_SERVICE_ADDR="127.0.0.1:$DF_CART_PORT" PRODUCT_CATALOG_SERVICE_ADDR="127.0.0.1:$DF_CATALOG_PORT" \
  CURRENCY_SERVICE_ADDR="127.0.0.1:$DF_CURRENCY_PORT" SHIPPING_SERVICE_ADDR="127.0.0.1:$DF_SHIPPING_PORT" \
  PAYMENT_SERVICE_ADDR="127.0.0.1:$DF_PAYMENT_PORT" EMAIL_SERVICE_ADDR="127.0.0.1:$DF_EMAIL_PORT" \
  OLLAMA_ADDR="http://$OLLAMA_HOST" OLLAMA_MODEL="$DF_MODEL" MONGO_URI="mongodb://127.0.0.1:$DF_MONGO_PORT" \
    "$DF_ROOT/runtime/native/checkout/checkout-agent" > "$DF_JOB_RUNTIME/checkout.log" 2>&1 &
  DF_OWNED_PIDS+=("$!")
  popd >/dev/null
  "$DF_PYTHON" -m dataflow.preflight --wait-port "$DF_CHECKOUT_PORT" --attempts 90
 ;;
esac
export DF_CART_PORT DF_CURRENCY_PORT DF_EMAIL_PORT DF_RECOMMENDATION_PORT DF_CHECKOUT_PORT DF_MONGO_PORT DF_OLLAMA_PORT DF_PAYMENT_PORT DF_MODEL DF_CONTEXT DF_ROUNDS DF_GENERATED_PER_ROUND DF_MAX_NODE_VISITS DF_MAX_PATHS
srun --overlap --ntasks=1 "$DF_PYTHON" -B -m dataflow.suite --root "$DF_ROOT" --repo "$DF_REPO" --runtime "$DF_JOB_RUNTIME" --agents "$DF_SELECTED_AGENTS"
