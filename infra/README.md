# Options Screener — Shared Azure Infrastructure

Bicep templates that provision the shared Azure resources used by the
screener backend and DD Coach. The frontend (Azure Static Web Apps) and
backend (Azure Web App) are deployed by separate workflows.

## Layout

```
infra/
  main.bicep                # subscription-scoped roll-up
  modules/
    monitoring.bicep        # Log Analytics 5GB cap + workspace-based App Insights
    containerapps.bicep     # Consumption env + 4 screener precomputation jobs
    cosmos.bicep            # Cosmos DB serverless — screener_* + dd_* containers
    cosmos-roles.bicep      # Data-plane RBAC for screener job MIs + backend reader
```

What's provisioned:

- Log Analytics + App Insights — required by the Container Apps environment
  for log aggregation.
- Container Apps environment + four screener jobs (`job-screener-{csp,cc,ditm,swing}`)
  per [ADR-0024](../docs/adr/0024-screener-precomputation.md) and
  [ADR-0025](../docs/adr/0025-swing-precomputation.md). All four share the
  same image; `STRATEGY` env selects which screener runs.
- Cosmos DB for NoSQL serverless with the four `screener_*` containers and
  the two DD Coach containers (`dd_entries`, `dd_filings_intel`).

Historical names (`cae-narrative-*`, `cosmos-nr-*`, database `narrative`) are
preserved to avoid breaking the live deployment — they no longer reflect the
workload scope.

## Build

```pwsh
az bicep build --file infra/main.bicep
```

## Deploy

```pwsh
az deployment sub create \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters nameSuffix=<3-9 char suffix>
```
