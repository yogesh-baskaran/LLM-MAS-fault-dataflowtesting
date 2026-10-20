# Integration review — existing project files remain unchanged

## Already implemented in the separate harness

The testing runner reads the original source and inserts coverage events at runtime, using an
AST-compiled in-memory Python module and a JavaScript test copy. It replaces only the test-process
binding `app.agent.charge_payment` with `Bridge.charge`, which sends real gRPC requests. No mock
payment response or mocked database is used by the SPEED experiment.

The LLM tool workflow is a separate test driver around the existing `PaymentAgent.run`. It allows
one charge attempt, persistence inspection and completion. Requests are immutable synthetic fixtures;
an LLM cannot choose arbitrary service addresses or execute shell commands.

The original Python repository uses the configured real MongoDB database. Its code and interface
are unchanged. A new test database per run prevents tests from clearing or writing the application DB.

The test RPC host calls the instrumented original JavaScript charge function. It does **not** run
the original `server.js` request handler, which logs full card request payloads. The new host omits
that logging. Application exception behavior is preserved; the bridge recognizes the original known
card-rejection messages and records the gRPC status separately from the application outcome.

## Proposed changes if you later want production integration

**These changes have NOT been applied and require your approval.**

| Existing file | Proposed change | Why |
| --- | --- | --- |
| `src/paymentagent/app/tools.py` or a new production gRPC-client module | Add a configured PaymentService client with deadlines and explicit error mapping | Make the deployed agent call PaymentService instead of its local simulated charge tool |
| `src/paymentagent/app/config.py` | Add PaymentService address and Ollama base URL configuration | Support cluster/service endpoints explicitly |
| `src/paymentagent/app/agent.py` | Use the approved client, preserve LKW semantics and distinguish service/persistence failures | Align production payment behavior with the test integration |
| `src/paymentagent/app/graph.py` | Use the configured model endpoint and meaningful validated routing/tool workflow | Its current classifier routes both outcomes to charge; this harness does not claim to test that graph |
| `src/paymentservice/server.js` | Redact PAN/CVV from request logging and normalize application errors to standard gRPC codes | Current request logging includes card fields; custom error code 400 is not a valid gRPC status |
| Deployment manifest actually used on SPEED | Add service endpoints, model configuration and test-independent database configuration | Only if deploying the new production integration |

No production patch is necessary to run the isolated study. The resulting evidence must identify
the test-process bridge and separate workflow as adaptations, not claim an unchanged production
entry point was tested end to end. No website fault injection, deployment, cluster login, or production
file change has been performed by this work.

