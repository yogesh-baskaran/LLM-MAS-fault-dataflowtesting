# Running the payment study on SPEED

These commands are for SPEED unless explicitly labeled **local transfer/viewing**.
Based on the supplied manual v7.5 and 2026 workshop slides. Confirm module names and authorization
against your current account; examples in the documents are not proof of your permissions.

## Reuse your existing salloc allocation

No account or partition needs to be entered again. Leave `SPEED_ACCOUNT=""` in `config.env`.
Edit the repository path locally in VS Code, then transfer the testing folder with that configuration.
Inside the compute-node allocation you already obtained:

```bash
cd "/speed-scratch/y_baskar/fall 2026/payment-dataflow-speed"
bash speed/setup.sh  # first-time preparation only
bash speed/job.sh    # uses your current allocation
```

Skip `speed/submit.sh` in this mode. `$SLURM_JOB_ID` is detected automatically. The compute-node
guard remains: these scripts do not bypass SLURM or run inference on submit nodes. The instructions
below for entering account details and submitting a batch job apply only when requesting a NEW allocation.

## 1. Transfer the separate testing project

**Local terminal**, while connected to Concordia VPN if off campus:

```powershell
scp "D:\fall 2026\payment-dataflow-speed.zip" "y_baskar@speed.encs.concordia.ca:/speed-scratch/y_baskar/fall 2026/"
ssh y_baskar@speed.encs.concordia.ca
```

On SPEED (default shell may be `tcsh`; switch to Bash):

```bash
/encs/bin/bash
cd "/speed-scratch/y_baskar/fall 2026"
unzip payment-dataflow-speed.zip
cd payment-dataflow-speed
cp config.env.example config.env
```

Edit `config.env` with your preferred editor. Fill the prominently marked `SPEED_ACCOUNT`,
`SPEED_PARTITION`, and `DF_REPO`. `DF_REPO` points to your existing checkout, not this testing folder.
Confirm `DF_ROOT` is where you extracted the files.

```bash
showpartitions
showavailgpus
module avail
```

If `module` is missing in Bash, use the initialization path from the workshop:

```bash
source /encs/pkg/modules-5.3.1/root/init/bash
module avail python
module avail singularity
```

The config has corresponding module settings you can adjust. No account value is guessed.

## 2. Prepare on an allocated compute node

Use your actual account and an authorized CPU partition for setup, normally `ps` if permitted.
Container conversion and environment installation belong on execution nodes, not submit nodes.

```bash
salloc --account=YOUR_SLURM_ACCOUNT --partition=YOUR_AUTHORIZED_CPU_PARTITION --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=02:00:00
```

Check `hostname`. If you are still on `speed-submit1` or `speed-submit2`, enter the allocation:

```bash
srun --pty /encs/bin/bash
```

On the compute node:

```bash
cd "/speed-scratch/y_baskar/fall 2026/payment-dataflow-speed"
bash speed/setup.sh
.venv/bin/python -B -m unittest discover -s tests -v
```

Setup creates a new Python environment, pulls Node/MongoDB/Ollama Singularity images, installs
service dependencies into a **copied testing directory**, and pulls the model. It does not run
payment tests. `node:22.16.0` is selected because the existing service depends on ESM `uuid`;
this Node version supports requiring synchronous ESM. Python must be 3.11 or newer.

If downloads are not allowed on your allocated nodes, do not move the workload to a submit node.
Use the cluster's approved download procedure or ask HPC support for access to prepared images.
Existing Ollama installations can be selected using `OLLAMA_BIN` and `OLLAMA_MODELS`.

Check readiness logs if setup fails. Ports 50061, 27027 and 11444 are test-only defaults. Change
them in `config.env` if occupied. Return to the submit node with `exit` when setup is finished.

## 3. Submit the first GPU pilot

Start with `DF_ROUNDS=1` and `DF_GENERATED_PER_ROUND=3` in `config.env` for a short pilot.
That runs 10 baseline inputs plus up to 3 generated inputs, with real LLM tool selection and analysis.

On the submit node:

```bash
cd "/speed-scratch/y_baskar/fall 2026/payment-dataflow-speed"
bash speed/submit.sh
squeue -u $USER
```

The submit script supplies the account, partition, memory, one GPU, CPU count, time limit and
working directory explicitly. The job checks that it is on a compute node in an allocation.
The experiment uses `srun` and real services on that same node. It does not launch Kubernetes
or touch your running website. Singularity's `--nv` exposes the allocated GPU to Ollama.

MongoDB uses scheduler-provided node-local temporary storage when available, with its data copied
back to the job runtime directory after shutdown. Otherwise the small pilot uses the dedicated scratch
directory. JSON evidence is written to scratch after each test so a timeout preserves completed tests.

The manual describes 20 GB A100 MIG resources on `pt`; if authorized, that is the intended starting
allocation for the 14B model at an 8192-token context. Host `--mem=32G` is **not GPU VRAM**.
The job writes `gpu-details.txt`; check model loading and GPU use in `ollama.log` before increasing
context or batch size. Temperature 0 and a seed aid repeatability but do not prove deterministic inference.

Monitor and inspect:

```bash
tail -f results/slurm-YOUR_JOB_ID.out
tail -f results/slurm-YOUR_JOB_ID.err
seff YOUR_JOB_ID
sacct -j YOUR_JOB_ID --format=JobID,State,ExitCode,Elapsed,MaxRSS
```

If the pilot completes, restore the default two rounds of six generated tests (up to 22 total).
Each job creates unique logs/data, and each run creates its own MongoDB test database.
No service is reused silently. Only processes started by the job are stopped during cleanup.

## 4. View results without running experiments locally

Choose the exact run directory printed in the SLURM output. **Local terminal**:

```powershell
scp "y_baskar@speed.encs.concordia.ca:/speed-scratch/y_baskar/fall 2026/payment-dataflow-speed/results/YOUR_RUN_DIRECTORY/dashboard.html" "D:\fall 2026\payment-dashboard.html"
```

Open that HTML file in a browser. It contains the report and source snapshots. You can also
open `dashboard/index.html` and select a downloaded `report.json`.

The dashboard supports test/status filters, definitions/use/path filters, test-specific source
highlighting, selected-obligation source locations, LKW evidence, real RPC calls, real database
verification, and model/oracle agreement. Viewing the file needs no Python, Node, model, login,
external CDN, or dashboard server.

Copy the JSON artifacts and relevant logs for research evidence. The workshop notes that scratch
data can be removed after 90 days of inactivity or account expiry; keep copies of completed runs.

## Failure triage

- Placeholder error: fill the exact named setting in `config.env`.
- `module`/module name error: confirm Bash initialization and available versions.
- Missing Python/gRPC/protobuf dependency: rerun setup inside an allocation.
- Node `require(uuid)` error: confirm the prepared Node 22.16.0 SIF is being used.
- MongoDB readiness error: inspect the job's `mongo-console.log` and `mongo.log`.
- Ollama readiness/memory error: inspect `ollama.log` and allocated GPU details.
- Service readiness error: inspect `paymentservice.log`; do not interpret as a payment assertion failure.
- LLM tool-order error: recorded as `error`; the runner does not silently substitute deterministic execution.
- Negative amount / invalid nanos accepted: recorded as a contract failure, without altering application behavior.
- Coverage truncation or observed paths missing from CFG: review analyzer scope before making coverage claims.

