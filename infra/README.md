# Narrative Intelligence Platform — Infrastructure

Bicep templates for the narrative pipeline. See:

- [docs/adr/0013-narrative-intelligence-platform.md](../docs/adr/0013-narrative-intelligence-platform.md) — platform decision
- [docs/adr/0014-narrative-cost-substitutions.md](../docs/adr/0014-narrative-cost-substitutions.md) — rightsized SKU choices
- [docs/NARRATIVE_METHODOLOGY.md](../docs/NARRATIVE_METHODOLOGY.md) — methodology

## Layout

```
infra/
  main.bicep              # subscription-scoped roll-up
  modules/
    storage.bicep         # Standard_LRS, lifecycle Hot→Cool@30d→Delete@365d
    eventhubs.bicep       # Basic, 1 TU, two topics, 1d retention
    keyvault.bicep        # Standard, RBAC, soft-delete + purge protection
    containerapps.bicep   # Consumption env + ca-ingestion always-on app
    monitoring.bicep      # Log Analytics 5GB cap + workspace-based App Insights
    # Phase 2: postgres.bicep (B1ms with pgvector + timescaledb + pg_cron)
```

## Build

```pwsh
az bicep build --file infra/main.bicep
```

## Phase 1 deploy (manual; CI follows)

```pwsh
az deployment sub create `
  --location centralus `
  --template-file infra/main.bicep `
  --parameters nameSuffix=<3-8 char suffix> keyVaultAdminObjectIds="['<your-aad-object-id>']"
```

Parameters:

| Name | Required | Notes |
|---|---|---|
| `resourceGroupName` | no | defaults to `rg-narrative` |
| `location` | no | defaults to `centralus` |
| `nameSuffix` | **yes** | lowercase alnum, 3–8 chars; appended to globally-unique names |
| `keyVaultAdminObjectIds` | no | Azure AD object IDs to grant Key Vault Secrets Officer |
| `tags` | no | merged into the default tag set |

## Cost ceiling

This stack is engineered to a $150/mo Azure ceiling. Any change that raises a
SKU tier requires a follow-up ADR with an updated budget projection. See
`ADR-0014` for the substitution table.
