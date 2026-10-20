#!/encs/bin/bash
# Existing allocations are reused; an optional first argument selects suites.
DF_SELECTED_AGENTS="${1:-}"
DF_DISPATCH_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
# Do not initialize the environment here: child jobs must see the scheduler's
# original TMPDIR before environment.sh selects scratch cache directories.
if [[ -f "$DF_DISPATCH_ROOT/config.env" ]]; then source "$DF_DISPATCH_ROOT/config.env"; fi
DF_SELECTED_AGENTS="${DF_SELECTED_AGENTS:-${DF_AGENTS:-payment}}"
if [[ "$DF_SELECTED_AGENTS" == payment ]]; then
  exec bash "$DF_DISPATCH_ROOT/speed/payment-job.sh"
fi
exec bash "$DF_DISPATCH_ROOT/speed/multi-job.sh" "$DF_SELECTED_AGENTS"
