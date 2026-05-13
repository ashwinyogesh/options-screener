// =============================================================================
// Azure Cosmos DB for NoSQL — narrative platform (Phase 2)
// =============================================================================
//
// Replaces the Postgres Flexible Server plan (ADR-0014 amendment: Postgres
// Flexible Server is subscription-restricted in all available regions).
//
// Capacity: Serverless — pay-per-operation, ~$0 at startup volume.
// Vector search: DiskANN index on the signals container (Phase 3+).
// Free tier applied if available on the subscription (1000 RU/s + 25 GiB).
//
// Containers:
//   raw-posts       — partition key /subreddit, TTL 90 days
//   signals         — partition key /ticker, no TTL (permanent record)
//   narratives      — partition key /ticker, no TTL (Phase 4+)
//
// Auth: managed identity via built-in Cosmos DB roles (no connection strings).
// See docs/NARRATIVE_METHODOLOGY.md §8.
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

// raw-posts: Blob-level dedup source, 90-day TTL.
resource rawPostsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'raw-posts'
  properties: {
    resource: {
      id: 'raw-posts'
      partitionKey: {
        paths: ['/subreddit']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 7776000  // 90 days in seconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [{ path: '/*' }]
        excludedPaths: [{ path: '/body/?' }]  // body is large; exclude from index
      }
    }
  }
}

// signals: extracted ticker + sentiment records, permanent.
resource signalsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'signals'
  properties: {
    resource: {
      id: 'signals'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [{ path: '/*' }]
        excludedPaths: [{ path: '/embedding/?' }]  // exclude vector blob from standard index
      }
      // Note: vectorEmbeddingPolicy + vectorIndexes are enabled post-deploy via
      // az cosmosdb sql container update once the preview feature is registered.
    }
  }
}

// narratives: aggregated narrative clusters (Phase 4+).
resource narrativesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'narratives'
  properties: {
    resource: {
      id: 'narratives'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [{ path: '/*' }]
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
