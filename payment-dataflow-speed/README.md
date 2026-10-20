# Agent dataflow testing on Concordia SPEED

**Multi-agent update:** payment, cart, checkout, currency, emailservice and recommendation suites
are now selectable. Read [MULTI_AGENT.md](docs/MULTI_AGENT.md) for the new scope, one-time native
setup, individual SPEED pilots and combined results. Existing payment-only execution is preserved.

```bash
bash speed/setup.sh
bash speed/setup-multi.sh     # once for the additional services and native builds
bash speed/run-currency.sh    # individual pilot inside your existing allocation
bash speed/run-all.sh         # sequential combined run of all six agents
```

Individual scripts: `speed/run-payment.sh`, `speed/run-cart.sh`, `speed/run-checkout.sh`,
`speed/run-currency.sh`, `speed/run-emailservice.sh`, and `speed/run-recommendation.sh`.
Run one at a time. All reports are written inside this testing folder's `results/` directory.

Separate testing code for the existing `LLM-MAS-fault-injection` repository.
**No existing application files are edited. Payment experiments execute on SPEED compute nodes.**
The dashboard is a portable HTML report; opening it on your laptop only views results.

## What payment-only mode does

1. Runs the original `PaymentAgent.run` and `repository.save_transaction`, AST-instrumented **in memory**.
2. Rebinds the agent's `charge_payment` function in the test process to a **real gRPC call** to PaymentService.
3. Executes the original JavaScript `charge.js` logic from an instrumented **test copy**, using a separate RPC host. It remains the Online Boutique **simulated** payment implementation, not an external payment processor.
4. Persists transactions with the original repository function into **real MongoDB**, in a new test database for each run.
5. Uses `qwen2.5-coder:14b` through Ollama to choose workflow tools, generate targeted test cases, and analyze validated LKW checkpoints.
6. Computes all-definitions, all-uses and acyclic statement-level DU-path coverage independently of the LLM.
7. Produces separate JSON files and a self-contained dashboard with per-test source highlighting.

Fault mode is forced to `NONE`. Invalid cards and amounts are ordinary test inputs, not injected faults.
The workload is limited to payment; the checkout website and other agents are not exercised yet.

## Read these first

- [SPEED setup and execution](docs/SPEED_RUNBOOK.md)
- [Coverage scope and interpretation](docs/COVERAGE_SPEC.md)
- [Test adaptations and proposed production changes](docs/INTEGRATION_REVIEW.md)
- [How the provided SPEED documents informed the implementation](docs/SPEED_DOCUMENT_NOTES.md)
- [Dashboard preview](dashboard/preview.html) — static Python catalog only; **no executed tests**

## YOUR DETAILS TO FILL

You can edit the supplied `config.env` locally in VS Code before transferring it to SPEED.
If using a fresh extraction, copy `config.env.example` to `config.env`. Set the repository path.
For your existing `salloc` allocation, **leave `SPEED_ACCOUNT` blank** and run `setup.sh`/`job.sh`
directly. SLURM supplies the job ID automatically. Account/partition settings are required only
if you choose `submit.sh` to request a new batch allocation.

| Setting | Your value |
| --- | --- |
| `SPEED_ACCOUNT` | Blank for existing `salloc`; your account only for new batch submission |
| `SPEED_PARTITION` | `pt` default; used only for new batch submission |
| `DF_REPO` | **YOUR EXISTING PROJECT DIRECTORY ON SPEED** |
| `DF_ROOT` | `/speed-scratch/y_baskar/fall 2026/payment-dataflow-speed` |

Confirm the module names with `module avail`. Optional existing Ollama binary/model paths are marked too.
Do not put credentials in this file. No cluster login or job has been attempted from this development session.

## Files

```text
config.env.example           Account/path placeholders and resource settings
speed/setup.sh               Compute-node dependency/container/model preparation
speed/submit.sh              Lightweight SLURM submission
speed/job.sh                 Compute-node payment experiment
dataflow/coverage.py         Python CFG, instrumentation and coverage engine
dataflow/runner.py           LLM generation/workflow, gRPC bridge, DB oracle, artifacts
dataflow/llm.py              Ollama structured-output client
service/instrument.cjs       JavaScript CFG + instrumented test-copy generation
service/server.cjs           Test RPC host for original charge implementation
service/trace.cjs            Correlated JavaScript execution events
dashboard/index.html         Offline report viewer
tests/test_core.py           Synthetic harness unit tests (not payment experiments)
```

## Outputs per run

Under `results/run_<timestamp>_<unique_id>/`:

| Artifact | Contents |
| --- | --- |
| `generated_tests.json` | Baselines, LLM-generated cases, declared coverage targets |
| `execution_traces.json` | Executed workflow, redacted inputs, RPC evidence, LKW, DB verification, events |
| `coverage.json` | CFG nodes, definitions, c/p-use pairs, candidate DU paths and actual test evidence |
| `llm_analysis.json` | Model verdict, LKW assessment, explanation and agreement with the oracle |
| `llm_calls.json` | Structured responses, model and token/duration metadata |
| `report.json` | Combined dashboard input and tested source snapshots |
| `dashboard.html` | Combined report viewer with the run embedded; no server or CDN |

The runner snapshots after each test. A later failure preserves earlier evidence. SLURM
termination between tests leaves the most recent snapshot marked `running`, not falsely completed.
Container fingerprints, frozen Python environment, GPU evidence, service logs and MongoDB data
remain under the job's unique `runtime/job-...` directory. The database is never automatically cleared.

## Interpreting results

`pass` means the observed behavior matches the independent test policy and persistence evidence.
An expected rejection of an invalid card can pass. A valid card accepted with an invalid amount
can fail, even if its transaction was saved correctly. `error` means execution could not complete,
including infrastructure problems or invalid LLM tool sequencing.

The model's verdict is recorded separately. It never determines coverage or overrides the oracle.
Definitions reaching no use do not satisfy all-definitions. Passing tests do not imply complete coverage.
Unknown-feasibility paths and fault-only paths stay visibly uncovered.

Exit codes: `0` completed with passing tests and completed model analyses; `1` completed with
contract failures; `2` infrastructure/workflow/analysis incomplete. Contract failures are useful
test findings and are not automatically bugs in this harness.

## Verification completed during development

Synthetic harness unit tests, Python parsing/static catalog generation, and JavaScript/Bash syntax checks.
No payment experiment or LLM inference was executed locally. Live SPEED, Singularity, MongoDB,
GPU loading and model quality require the first cluster pilot. The downloaded development tools
under `.tools/` and extracted manual text under `reference/` are excluded from the distribution.

