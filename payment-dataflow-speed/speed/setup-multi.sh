#!/encs/bin/bash
source "$(dirname -- "$0")/environment.sh"
require_compute
load_modules
cd "$DF_ROOT"
for file in dataflow/prepare_native.py instrumentation/go/instrument.go instrumentation/go/trace.go instrumentation/csharp/Instrument.cs instrumentation/csharp/Program.cs instrumentation/javascript/currency.cjs; do
 [[ -f "$DF_ROOT/$file" ]] || { printf 'Missing update file: %s\nTransfer the complete testing update before setup.\n' "$DF_ROOT/$file" >&2; exit 2; }
done
export SINGULARITYENV_GOPATH="$DF_ROOT/cache/go"
export SINGULARITYENV_GOCACHE="$DF_ROOT/cache/go-build"
export SINGULARITYENV_CGO_ENABLED=0
export SINGULARITYENV_DOTNET_CLI_HOME="$DF_ROOT/cache/dotnet"
export SINGULARITYENV_NUGET_PACKAGES="$DF_ROOT/cache/nuget"
[[ -f images/go.sif ]] || singularity pull images/go.sif docker://golang:1.25
[[ -f images/dotnet.sif ]] || singularity pull images/dotnet.sif docker://mcr.microsoft.com/dotnet/sdk:8.0
mkdir -p runtime/currency-source
cp "$DF_REPO/src/currencyservice/package.json" runtime/currency-source/
cp "$DF_REPO/src/currencyservice/package-lock.json" runtime/currency-source/
cp "$DF_REPO/src/currencyservice/server.js" runtime/currency-source/
cp -R "$DF_REPO/src/currencyservice/proto" runtime/currency-source/
cp -R "$DF_REPO/src/currencyservice/data" runtime/currency-source/
SINGULARITYENV_npm_config_cache="$DF_ROOT/cache/npm" singularity exec --bind "$DF_ROOT:$DF_ROOT" \
 --pwd "$DF_ROOT/runtime/currency-source" images/node.sif npm ci --ignore-scripts
"$DF_ROOT/.venv/bin/python" -B -m dataflow.prepare_native --root "$DF_ROOT" --repo "$DF_REPO"
sha256sum images/*.sif > runtime/container-sha256.txt
echo 'Multi-agent test services are prepared. Run: bash speed/job.sh all'
