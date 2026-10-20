# Multi-agent dataflow suites on SPEED

All changes are inside `payment-dataflow-speed`. The Boutique repository remains a read-only source.
No deployment, website fault injection, real card charge, or external email delivery is performed.

## Update the files on SPEED

Transfer the updated testing folder with WinSCP. Preserve your existing `config.env`, `.venv`,
`images`, caches, runtime data and prior results. New defaults in `speed/environment.sh` support
older configuration files. Check that `DF_REPO` and `DF_ROOT` still point to your correct paths.

Enter your existing GPU compute allocation. Because this update adds Python dependencies,
native compilers and test services, prepare them **once**:

```bash
cd "/speed-scratch/y_baskar/fall 2026/payment-dataflow-speed"
bash speed/setup.sh
bash speed/setup-multi.sh
```

`setup-multi.sh` prepares Node currency dependencies, Go 1.25 and .NET 8 Singularity images,
builds the Roslyn/Go AST instrumenters and compiles separate cart/checkout copies under
`runtime/native`. Source fingerprints are verified before native experiments; changed originals
require rerunning `setup-multi.sh` to rebuild the copies. Original source files are never written.

Do not run setup again for every login. Reuse these prepared files, obtain a new allocation, and
start the chosen job. If your allocation only has CPU resources, obtain a GPU allocation for inference.
The project continues to accept paths containing spaces and canonical SPEED scratch mount paths.

## Select suites

Dedicated entry scripts reuse your existing `salloc` allocation. Run these from the
`payment-dataflow-speed` directory after the one-time setup:

| Tests to run | Command |
| --- | --- |
| Payment | `bash speed/run-payment.sh` |
| Cart | `bash speed/run-cart.sh` |
| Checkout | `bash speed/run-checkout.sh` |
| Currency | `bash speed/run-currency.sh` |
| Emailservice | `bash speed/run-emailservice.sh` |
| Recommendation | `bash speed/run-recommendation.sh` |
| All six, sequentially | `bash speed/run-all.sh` |

Each individual entry selects its named suite regardless of `DF_AGENTS`. Dependencies
needed by that suite are started automatically; checkout also calls its dependent services.
The overall script produces per-agent results and a combined dashboard under `results/`.
Run one script at a time in an allocation: simultaneous runs share configured service ports.
These scripts propagate the underlying job's exit status and request no new allocation.

The generic entry also supports a custom subset:

```bash
bash speed/job.sh payment
bash speed/job.sh currency
bash speed/job.sh emailservice
bash speed/job.sh recommendation
bash speed/job.sh cart
bash speed/job.sh checkout
bash speed/job.sh currency,recommendation
bash speed/job.sh all
```

No `submit.sh` is needed inside `salloc`. The existing `bash speed/job.sh` defaults to payment,
or to `DF_AGENTS` if you set that in `config.env`. Default additional ports are 50062–50068;
original payment, MongoDB and Ollama ports are retained.

Start with one generated round and a small number of generated tests. A combined run makes many
LLM calls and can exceed a short allocation; snapshot reports preserve completed tests. Each suite
runs sequentially in its own OS process, preventing collisions between the original Python agents'
shared `app` module names. Services use a job-local active test ID only because execution is sequential;
parallel suites would require explicit metadata propagation instead.

The inner `srun` uses `--overlap` so it can reuse an allocation entered through an interactive
`srun --pty` shell without waiting for that shell's step to release the resources.

## What executes

| Suite | Original execution under test | Measured binding-level scope |
| --- | --- | --- |
| Payment | Existing PaymentAgent → observed real PaymentService RPC → real MongoDB | Existing Python run/save and JavaScript charge scope |
| Currency | Existing CurrencyAgent → original currency algorithms in test RPC host | `CurrencyAgent.run`, JavaScript `_carry` |
| Emailservice | Existing generation/send LangGraph with live LLM → original DummyEmailService | Generation helpers, graph nodes, client order builder and send method |
| Recommendation | Existing recommendation agent → original RecommendationService handler | `_fetch`, get/explain agent methods; production enrichment graph excluded |
| Cart | Existing C# agent and gRPC methods → live model for merge operations | Add/get/clear/merge agent methods and gRPC handlers |
| Checkout | Existing Go ReAct loop, tools, order parser and persistence wrapper | `PlaceOrder` agent/server methods, eight tool `Execute` methods, argument/order parsing |

Cart uses the source tree selected by its Docker build: `src/cart-agent/src/`. Its in-memory cart
is preserved. The test host binds loopback on a configurable port rather than using the original
hardcoded server startup. Cart is itself an agent-backed gRPC service; it does not acquire a new
production dependency on another cart service.

Checkout retains its native LLM tool loop and its 20-iteration execution cap. The test copy changes
only startup/correlation/instrumentation: a test listener, a mandatory local Mongo URI/default,
RPC correlation interceptor and trace events. Its hardcoded database name remains safe inside
the dedicated job MongoDB instance. Its original persistence wrapper executes.

Checkout calls original cart, payment, currency and demo-email implementations in isolated hosts.
**Catalog and shipping are deterministic fixture services**, using repository catalog products and a
fixed USD 5 quote/tracking ID. These fixture boundaries are reported; this is checkout agent-level
testing, not full-system dataflow coverage. Recommendation's catalog dependency uses that fixture
catalog and the original service's random selection algorithm. Assertions check valid sets/counts
and exclusion, not a fixed random ordering.

The outer LLM test dispatcher generates inputs, chooses execute/inspect/finish and analyzes evidence.
Cart, checkout and email also execute their original internal LLM paths. Currency and recommendation
use the separate dispatcher rather than claim production HTTP/router graphs were tested. No silent
mock execution substitutes for an unavailable LLM. Original internal fallbacks remain observable.

## Known findings the tests should expose

- Email's generated subject/body are not forwarded by its existing send client/protobuf interface.
- Email's existing order builder drops items and fractional totals. These are reported as contract
  failures; the testing project does not fix those production behaviors.
- Checkout may return an apparent order without completing all required service calls. Native tool
  and service evidence are checked independently of the final LLM JSON.
- Cart/model quantity merges may disagree with arithmetic, or invalid quantities may be accepted.
- Invalid money/currency inputs may expose validation gaps in the existing service.

Expected application rejections can pass. Infrastructure errors and incomplete model workflows
are `error`, not passing tests. The model's analysis is stored separately from the independent oracle.

## Coverage interpretation

Python AST, JavaScript/Babel, Go AST and C#/Roslyn instrumenters emit the same node/use/definition
event format, with original source locations and function invocation IDs. The shared engine tracks
local bindings at statement granularity. Go names include declaration lines to separate shadowed
bindings. C# scope is limited to the selected methods and local names; heap aliases and nested
expression evaluation are not full-program reaching-definition analysis.

Python `for`, `while`, `with`, `break` and `continue` are now supported. Unsupported async-loop,
`match` and `try/finally` constructs fail explicitly. Go/C# transformers likewise reject unsupported
selected statements rather than invent coverage. C# selected foreach loops have no break/continue;
broader language constructs require extending the transformer before adding their scope.

`DF_MAX_NODE_VISITS=3` bounds how often a node may appear on an enumerated DU path. This is a
node-visit bound, **not three complete iterations of every nested loop**. Actual execution is not
truncated to satisfy the analysis bound. Longer observed paths are counted separately. A global
`DF_MAX_PATHS=10000` enumeration limit is explicit. Definition and DU-pair denominators are computed
independently, so the path cap does not silently remove later definitions/pairs.

Uncovered paths have unknown feasibility, including fault-only branches that remain inactive.
Full feasible all-DU-path coverage is not certified. Generated coverage targets are intentions;
only actual events count as execution evidence. LKW points added by adapters are labeled
`source: test_boundary`; original LKW traces are preserved separately where available.

## Results and dashboard

For combined/new-agent runs:

```text
results/run_<timestamp>_<id>/
  manifest.json
  report.json
  dashboard.html
  currency/
    generated_tests.json
    execution_traces.json
    coverage.json
    llm_analysis.json
    llm_calls.json
    report.json
    dashboard.html
  ...other selected agents...
  payment/run_<payment_timestamp>_<id>/...  # legacy payment layout preserved
```

Worker logs sit next to the combined report. The combined dashboard has an agent selector and
per-test source highlighting. Failed workers are recorded in the manifest, and remaining suites
continue. Do not treat an empty or incomplete suite as a successful coverage result.

Payment-only execution retains its existing output layout. JSON outputs and native service logs
remain on SPEED; view downloaded `dashboard.html` in a browser without running any local experiment.

## Verification and pilot boundary

Development checks use synthetic framework functions, standalone contract fixtures, native compilation,
source transformation and syntax checks. They do not execute original agent workflows or inference
locally. Successful local compilation does not verify Linux/Singularity, cluster permissions, GPU
memory or model tool quality. Those require the individual-agent SPEED pilots above.

Original source audit and artifact packaging exclude `.tools`, runtime builds, downloaded SDKs,
actual config, caches and generated result data from the code ZIP. Existing result exclusion from
Git remains; selected research evidence can be copied into an explicitly reviewed tracked folder.

Implementation references: [Go AST](https://pkg.go.dev/go/ast),
[Roslyn syntax transformation](https://learn.microsoft.com/en-us/dotnet/csharp/roslyn-sdk/get-started/syntax-transformation),
[SLURM srun overlap](https://slurm.schedmd.com/srun.html#OPT_overlap).

