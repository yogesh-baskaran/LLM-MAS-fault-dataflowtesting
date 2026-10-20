#!/encs/bin/bash
set -euo pipefail
# Run inside your existing SPEED compute allocation.
DF_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$DF_SCRIPT_DIR/job.sh" recommendation
