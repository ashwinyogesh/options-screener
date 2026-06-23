// =============================================================================
// Azure Cosmos DB for NoSQL — shared backing store for screener + DD Coach.
// =============================================================================
//
// Capacity: Serverless — pay-per-operation.
// Auth: managed identity via built-in Cosmos DB roles (no connection strings).
//
// Containers:
//   screener_csp     — ADR-0024 precomputed CSP scan results   (TTL 24 h)
//   screener_cc      — ADR-0024 precomputed CC scan results    (TTL 24 h)
//   screener_ditm    — ADR-0024 precomputed DITM scan results  (TTL 24 h)
//   screener_swing   — ADR-0025 precomputed Swing scan results (TTL 24 h)
//   dd_entries       — DD Coach journal entries                (no TTL)
//   dd_filings_intel — cached LLM-derived SEC filing insights  (no TTL)
//
// Account name (`cosmos-nr-${nameSuffix}`) and database name (`narrative`)
// keep their historical prefixes to avoid breaking the live deployment —
// they no longer reflect the workload scope.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for globally-unique account name.')
param nameSuffix string

@description('Tags applied to all resources.')
param tags object

@description('Principal IDs granted Cosmos DB Built-in Data Contributor.')
param dataContributorPrincipalIds array = []

var accountName = 'cosmos-nr-${nameSuffix}'
var databaseName = 'narrative'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      { name: 'EnableServerless' }
    ]
    enableFreeTier: false   // set true only on one account per subscription
    disableLocalAuth: false // keep key auth for migration tooling; MI is preferred
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous7Days'
      }
    }
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: { id: databaseName }
  }
}

// screener_csp: precomputed CSP scan results (ADR-0024).
// One doc per ticker; TTL 24 h so stale docs auto-expire if the worker goes down.
resource screenerCspContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'screener_csp'
  properties: {
    resource: {
      id: 'screener_csp'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 86400  // 24 h
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/computed_at/?' }
          { path: '/result/best_csp_score/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// screener_cc: precomputed CC scan results (ADR-0024).
resource screenerCcContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'screener_cc'
  properties: {
    resource: {
      id: 'screener_cc'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 86400  // 24 h
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/computed_at/?' }
          { path: '/result/best_cc_score/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// screener_ditm: precomputed DITM scan results (ADR-0024).
resource screenerDitmContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'screener_ditm'
  properties: {
    resource: {
      id: 'screener_ditm'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 86400  // 24 h
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/computed_at/?' }
          { path: '/result/best_ditm_score/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// screener_swing: precomputed swing scan results (ADR-0025).
resource screenerSwingContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'screener_swing'
  properties: {
    resource: {
      id: 'screener_swing'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 86400  // 24 h
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/computed_at/?' }
          { path: '/result/data/swing_score/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// dd_entries: DD Coach journal entries (V1, single-user).
// One doc per DD session: draft (in-progress wizard state) or completed
// (immutable post-save). Doc shape — see backend/services/dd_coach/models.py
// (DDEntryDoc). Partition key /ticker: list-by-ticker is the common pattern
// and single-user volume keeps per-partition skew irrelevant.
//
// No TTL — entries are the user's permanent decision record.
resource ddEntriesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'dd_entries'
  properties: {
    resource: {
      id: 'dd_entries'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/user_id/?' }
          { path: '/status/?' }
          { path: '/created_at/?' }
          { path: '/updated_at/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// dd_filings_intel: cached LLM-derived insights from SEC filings (V3).
// One doc per (ticker, accession_or_period, insight_type) tuple. Doc id
// shape: `{ticker}|{cache_key}|{insight_type}`. Cache is immutable per
// accession — when SEC publishes a new 10-K the cache_key changes and a
// fresh insight is generated on first request. Partition key /ticker so a
// single-ticker DD session reads contiguously.
//
// No TTL — insights are tied to historical filings and stay valid as long
// as the filing remains the latest of its type.
resource ddFilingsIntelContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'dd_filings_intel'
  properties: {
    resource: {
      id: 'dd_filings_intel'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/insight_type/?' }
          { path: '/cache_key/?' }
          { path: '/generated_at/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// Grant Cosmos DB Built-in Data Contributor to provided principal IDs.
// Built-in role definition ID is fixed across all accounts.
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource dataContributorAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [
  for (principalId, i) in dataContributorPrincipalIds: {
    parent: account
    name: guid(account.id, principalId, cosmosDataContributorRoleId)
    properties: {
      roleDefinitionId: '${account.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
      principalId: principalId
      scope: account.id
    }
  }
]

output accountName string = account.name
output accountEndpoint string = account.properties.documentEndpoint
output databaseName string = database.name
