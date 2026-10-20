#!/encs/bin/bash
set -euo pipefail
# Run all six suites sequentially inside your existing SPEED allocation.
DF_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$DF_SCRIPT_DIR/job.sh" all
