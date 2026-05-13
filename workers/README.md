# Workers — Narrative Intelligence Platform

Each subdirectory is an independent container image with its own pinned
`requirements.txt`. Worker images do **not** share the FastAPI backend's
dependency closure; they are deployed separately to Azure Container Apps
(always-on apps and scale-to-zero Jobs).

| Worker | Phase | Schedule | Image |
|---|---|---|---|
| `ingestion`         | 1 | always-on (MinReplicas=1, MaxReplicas=2) | `ghcr.io/<org>/narrative-ingestion:<sha>` |
| `extractor`         | 2 | event-hub trigger or 1-min cron | `ghcr.io/<org>/narrative-extractor:<sha>` |
| `aggregator`        | 3 | every 15 min | `ghcr.io/<org>/narrative-aggregator:<sha>` |
| `classifier`        | 4 | every 30 min | `ghcr.io/<org>/narrative-classifier:<sha>` |
| `narrative-detector`| 5 | hourly | `ghcr.io/<org>/narrative-detector:<sha>` |
| `scorer`            | 6 | every 15 min | `ghcr.io/<org>/narrative-scorer:<sha>` |

See [docs/NARRATIVE_METHODOLOGY.md §8](../docs/NARRATIVE_METHODOLOGY.md#8-phasing-and-milestones)
for the full phasing.

## Building locally

```pwsh
docker build -t narrative-ingestion:dev workers/ingestion
```

## Conventions

- Python 3.12 base image (`python:3.12-slim`).
- Single `main.py` entry point per worker.
- All Azure SDK calls authenticated via `DefaultAzureCredential` (works with
  System-assigned managed identity on Container Apps; falls back to local
  Azure CLI for development).
- Secrets fetched from Key Vault at startup; never read from environment for
  anything sensitive. Env vars hold *names* only (e.g. `KEYVAULT_URI`).
- Type hints required on every public function. `from __future__ import
  annotations` at the top of every module.
- One module-level `logger = logging.getLogger(__name__)`. No `print()`.
