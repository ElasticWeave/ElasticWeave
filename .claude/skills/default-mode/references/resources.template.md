# resources.md — declared compute / infra resources (READ before allocating or running heavy work)

Populated during **default-mode startup** from the user's brief + discovery. **Globally visible:** the top coordinator,
workers, reviewers, and workflow nodes consult this *before* assuming work must run locally,
*before* allocating compute, and *before* submitting a job.
A bounded project-state worker keeps it current; the top session only authorizes and inspects
updates. **No secrets here** — point to *where* creds live, never inline them.

## Compute / servers
- `<host / cluster name>` — access: `<ssh alias / how to reach>` · specs: `<cpu / gpu / mem>` · use for: `<what>`
- (e.g. login node vs compute nodes; how to get an interactive session; this box's own specs)

## GPU allocation
- Allocate GPUs: `<e.g. srun --gpus=N ... / a custom allocator / a queue>` · limits: `<max GPUs, max time>`
- Check availability / current usage: `<command>`

## Job / task submission
- Submit a job: `<SLURM sbatch / an internal queue / a script / an API endpoint>` · check status: `<command>`
- Conventions: `<where logs land, run-naming, artifact/output dir>`

## Internal pipelines / services
- `<data pipeline / artifact store / model registry / internal API / dataset mount>` — how to use: `<command / endpoint>`

## Access / credentials (pointers ONLY — no secrets)
- `<service>` creds: `<where they live / how to auth>` (e.g. a path under `~/.config/...`, an env var, a vault/secret-manager)

## Quotas / limits / etiquette
- Budget · concurrency caps · time limits · shared-resource etiquette (don't hog the cluster, clean up jobs).

---
*If a category doesn't apply (e.g. CPU-only local box, no scheduler), say so explicitly — "none; runs locally on
this box" — so agents don't go hunting for infra that isn't there.*
