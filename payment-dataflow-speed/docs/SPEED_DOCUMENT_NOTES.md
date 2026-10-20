# Notes from the supplied SPEED documents

Read from the user's PDFs:

- `speed-manual.pdf`: 65 pages, manual version 7.5.
- `HPC Workshop Slides.pdf`: 33 pages, 2026 workshop.

These documents were treated as reference material. Their examples do not authorize account access,
service deployment, sending messages, or changing the existing project.

| Document reference | Relevant guidance | Implementation |
| --- | --- | --- |
| Workshop pp. 5–7; manual sections 1.6, 2.1 | Submit nodes are for job submission; SSH/VPN access; workloads on execution nodes | Compute-node/SLURM guards; transfer instructions; no local payment execution |
| Workshop pp. 8–11; manual section 1.9 | Use `/speed-scratch/$USER`; scratch retention and home quota | Separate scratch root, caches/models/results there; archive finished runs |
| Workshop pp. 12–15; manual sections 2.2–2.5 | Explicit SLURM account, partition, memory, CPU/GPU resources and walltime; inspect efficiency | Marked account/partition placeholders, submit wrapper, `srun`, `seff`/`sacct` instructions |
| Workshop pp. 16–17, 22–23; manual section 2.11 | Initialize Bash modules; use dedicated Python environments; redirect cache/tmp | Configurable module initialization, isolated venv, scratch cache directories |
| Workshop p. 26 | Ollama launched through an allocated job; node-specific connection | Job-local Ollama; no externally exposed inference listener |
| Manual section 2.16, pp. 34–35 | A100 80 GB cards partitioned into 20 GB MIG resources; authorization matters | One GPU requested in user's chosen authorized partition; context limit and GPU logs |
| Manual section 2.17, pp. 37–38 | Singularity without root; OCI/Docker image conversion within allocation; `--nv` for GPUs | Prepared SIF images and `singularity exec --nv`; no Docker daemon on SPEED |

All cluster accounts, permissions, module availability, and allocation behavior remain to be verified
on the user's current SPEED account. The documents describe capabilities, not the live state of the cluster.

API references used for the new harness:

- [Ollama chat API](https://docs.ollama.com/api/chat)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Babel parser](https://babeljs.io/docs/babel-parser)
- [Babel traversal](https://babeljs.io/docs/babel-traverse)

