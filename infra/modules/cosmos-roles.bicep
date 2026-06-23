// =============================================================================
// Cosmos DB data-plane RBAC for the screener Container Apps jobs.
// =============================================================================
//
// Grants Cosmos DB Built-in Data Contributor to a list of principal IDs on an
// EXISTING Cosmos account. Decoupled from cosmos.bicep so it can consume the
// containerapps module's principalId outputs without a circular dependency
// (containerapps already consumes cosmos.outputs.accountEndpoint).
//
// One assignment per principal; resource name is deterministic via
// guid(account.id, principalId, roleId), so re-running this module is idempotent
// and will not duplicate existing assignments created by cosmos.bicep's own
// dataContributorAssignments loop (the names will collide-and-converge).
//
// Caller (main.bicep) is expected to pass the screener job principal IDs.
// External admin object IDs are still passed through cosmos.bicep via
// cosmosDataContributorPrincipalIds.
// =============================================================================

@description('Existing Cosmos DB account name (no globally-unique check; must already exist).')
param cosmosAccountName string

@description('Principal IDs (managed-identity object IDs) to grant Cosmos Data Contributor.')
param principalIds array

@description('Principal IDs (managed-identity object IDs) to grant Cosmos Data Reader. Used by the read-only App Service backend (optionsapi) which only needs to query the screener_* containers.')
param dataReaderPrincipalIds array = []

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

// Built-in Cosmos DB role definition IDs are fixed across all accounts.
//   00000000-...-0001  Cosmos DB Built-in Data Reader        ← read-only App Service backend
//   00000000-...-0002  Cosmos DB Built-in Data Contributor   ← what workers need
var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'
var dataReaderRoleId = '00000000-0000-0000-0000-000000000001'

resource workerAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [
  for (principalId, i) in principalIds: if (!empty(principalId)) {
    parent: account
    name: guid(account.id, principalId, dataContributorRoleId)
    properties: {
      roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleId}'
      principalId: principalId
      scope: account.id
    }
  }
]

// Read-only assignments. The App Service backend (`optionsapi`) is deployed by
// a separate workflow (`deploy-backend.yml`) but its system-assigned MI must be
// granted data-plane read on this Cosmos account so the screener routes can
// serve precomputed results from the `screener_*` containers. The MI is
// enabled once via `az webapp identity assign` (out-of-band; persists across
// redeploys), and its principalId is then added to the
// `NARRATIVE_COSMOS_READER_PRINCIPAL_IDS` secret so this module reconciles the
// role assignment on every infra deploy.
resource readerAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [
  for (principalId, i) in dataReaderPrincipalIds: if (!empty(principalId)) {
    parent: account
    name: guid(account.id, principalId, dataReaderRoleId)
    properties: {
      roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataReaderRoleId}'
      principalId: principalId
      scope: account.id
    }
  }
]
