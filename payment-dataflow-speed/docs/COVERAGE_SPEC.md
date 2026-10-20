# Payment definition–use coverage specification

This page describes the original payment-only scope. The additional agent scopes and current
bounded-loop support are documented in [MULTI_AGENT.md](MULTI_AGENT.md). Python loops and context
managers are now supported; the older unsupported-loop statement below applies to the original version.

## Measured scope

Three functions are instrumented:

| Function | Source | Execution |
| --- | --- | --- |
| `PaymentAgent.run` | `src/paymentagent/app/agent.py` | Existing source compiled with added AST events in memory |
| `save_transaction` | `src/paymentagent/app/repository.py` | Existing source compiled with added AST events in memory |
| `charge` | `src/paymentservice/charge.js` | Existing source transformed into a separate test copy using Babel |

The analysis is **intraprocedural, local-binding, statement-level**. A binding such as `request`
is tracked as a local variable; its individual dictionary/protobuf fields are checked by the
payment contract oracle but do not each become independently analyzed heap-field DU obligations.
`self`, the instrumentation variables and the LKW logger object are excluded from DU tracking.
Original line numbers and source fingerprints are retained in the catalog and report.

## Criteria

- **All-definitions:** each candidate local definition reaches at least one observed use on a
  definition-clear path. Assignment execution alone does not satisfy this criterion.
- **All-uses:** each candidate reachable definition–use pair is observed. Computational uses
  and predicate uses are distinguished. True and false outcomes of statement-level predicates
  have separate p-use obligations.
- **All-DU-paths:** each candidate acyclic definition-clear CFG path to each use is observed.
  Definitions kill the prior binding after RHS uses, so `x = x + 1` uses the incoming definition
  before creating the new definition.

Instrumented events identify the function invocation, test ID, node and phase (`node`, `use`, `def`).
The coverage engine compares **actual path sequences**, not just endpoint pairs or checkpoint names.
JavaScript event files and Python events share test IDs; frames separate individual invocations.
Interprocedural links are verified as payment handoff contracts but are not counted as intraprocedural
DU paths. System-wide coverage is future work.

## Feasibility and completeness

The CFG includes conservative exception edges in Python `try` blocks. Their existence does not
prove that a particular exception can arise with valid inputs. Fault-only branches remain in the
catalog and remain uncovered because this experiment sets `FAULT_MODE=NONE`.

Unobserved paths are marked `unknown`, not `infeasible`. Observed paths are `observed_feasible`.
Percentages are explicitly **candidate percentages**, not certified coverage of all feasible paths.
`complete_feasible_coverage_claim` is always false in this first version. Review or stronger static
analysis is needed before excluding infeasible obligations from a research denominator.

The selected payment functions contain no statement-level loops. Other suites now support Python
loops and context managers, with bounded path enumeration. `match`, async loops and `try/finally`
still fail clearly. `max_paths` bounds enumeration; truncation is displayed. Definitions and DU-pair
denominators remain independent of that path cap. See MULTI_AGENT.md for native-language limits.

Not modeled: heap aliasing/field mutations, called library bodies, comprehension-internal bindings,
expression-internal ternary/short-circuit paths, expression evaluation order under exceptions, or
cross-function reaching definitions. Uses are emitted at statement entry (or selected branch entry),
so evidence is at that declared granularity, not bytecode-level individual operand evaluation.
To meet a requirement for unrestricted expression/heap/all-feasible-path coverage, extend the analyzer
and scope; do not describe this version as exhaustive whole-program dataflow coverage.

## Independent behavioral oracle

The initial policy is explicit and reviewable:

- Accept the provided valid VISA/MasterCard fixtures when not expired.
- Reject invalid, empty and unsupported AMEX fixtures and expired fixtures.
- `units >= 0`, `0 <= nanos < 1_000_000_000`, uppercase three-letter currency; zero amount is allowed.
- On success, one real service result with a UUID and exactly one matching MongoDB transaction.
- On expected rejection, no database insertion.
- Preserve currency and amount between fixture, RPC request, service result and saved record.

Currency validation here checks syntax, not ISO membership or exchange rates. Fixture oracle labels
are independent of the LLM and service implementation. The policy may expose existing validation
gaps. It is not a claim about a live financial processor.

## LKW

Original checkpoints are preserved: `TASK_START`, `CARD_VALIDATED`, `CHARGE_DONE`, `SAVE_DONE`,
`FINAL_ANSWER`. Each checkpoint receives an independent validation result. Last known well is the
last validated checkpoint **before the first invalid checkpoint**. A later clean checkpoint does not
erase an earlier deviation. An expected rejection may have a valid `FINAL_ANSWER` checkpoint.

The current original agent emits `CARD_VALIDATED` after its charge call returns; thus the service
trace supplies the real card-validation branch evidence. The label is not treated as proof of where
the validation statement executed. Missing checkpoints alone are not faults when an input is legitimately rejected.

The LLM gets redacted evidence without injected fault labels (none exist here). Model verdict and
LKW attribution are compared separately with the deterministic oracle. Errors/missing execution
are not treated as clean traces.

