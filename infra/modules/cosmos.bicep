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
//   signals          — partition key /ticker, no TTL (permanent record)
//   ticker_timeline  — partition key /ticker, TTL 90 days (Phase 3 aggregator)
//   narratives       — partition key /ticker, no TTL (Phase 4+)
//
// Removed: raw-posts container — ingestion writes to Blob Storage only.
//   Cosmos raw-posts was never written to (see ADR-0015).
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

// ticker_timeline: one document per (ticker, bucket_date). Written by
// job-aggregator every 15 min (upsert, id = "{ticker}_{bucket_date}").
// TTL 90 days — old snapshots auto-expire.
//
// Schema (see backend/services/narrative/types.py → TickerTimelineSnapshot):
//   Identity:    id, ticker (pk), bucket_date, computed_at
//   Volume:      mentions_7d/14d/30d
//   Persistence: decay_weighted_density_7d/14d/30d, daily_buckets[]
//   Accel:       acceleration_7d
//   Diversity:   unique_authors_14d, gini_14d
//   Depth:       avg_body_len, dd_post_ratio, financial_term_density
//   Sentiment:   bullish_ratio, bearish_ratio, avg_confidence
//   Phase 4+:    conviction_* fields (added by classifier job)
//   Phase 5+:    lifecycle_stage, stage_confidence
//   Phase 6+:    rs_14d_norm, opt_ratio_norm, institutional_13f_norm (in-memory only, not stored)
resource tickerTimelineContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'ticker_timeline'
  properties: {
    resource: {
      id: 'ticker_timeline'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 7776000  // 90 days in seconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/bucket_date/?' }
          { path: '/window_days/?' }
          { path: '/computed_at/?' }
          // Phase 6 read path: backend ORDER BY needs these indexed.
          // `cosmos_client.query_top_acs` and `query_emerging` order by `acs`;
          // `cosmos_client.query_ticker` orders by `computed_at` (system `_ts`
          // cannot be explicitly indexed when `/*` is excluded).
          { path: '/acs/?' }
          { path: '/lifecycle_stage/?' }
          // Detector write path (ADR-0030): patched onto the timeline doc
          // by write_lifecycle().  Without these in includedPaths, ORDER BY
          // / WHERE on them silently returns 0 rows because the field is not
          // indexed (the doc has the value, but no index entry exists).
          { path: '/stage_confidence/?' }
          { path: '/n_embedded/?' }
          { path: '/dominant_fraction/?' }
        ]
        excludedPaths: [{ path: '/*' }]  // daily_counts array excluded — not queried directly
      }
    }
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

// alerts: Phase 7 alert records (stage transitions, ACS spikes).
// TTL 30 days — old alerts expire automatically; no manual cleanup needed.
resource alertsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'alerts'
  properties: {
    resource: {
      id: 'alerts'
      partitionKey: {
        paths: ['/ticker']
        kind: 'Hash'
        // version intentionally omitted (= v1) to match the existing
        // production container. Cosmos rejects PK-version changes
        // in-place, so bumping to v2 would require recreating the
        // container and losing in-flight alert history.
      }
      defaultTtl: 2592000  // 30 days in seconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/ticker/?' }
          { path: '/alert_type/?' }
          { path: '/triggered_at/?' }
          { path: '/bucket_date/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// narrative_cache: pre-computed scoreboard written by the scorer worker (ADR-0028).
// One doc (id="scoreboard_v1") contains the sorted top-ACS and emerging lists.
// TTL 24 h — if the scorer misses a run the cache expires and the read service
// falls back to the cross-partition scan gracefully.
resource narrativeCacheContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'narrative_cache'
  properties: {
    resource: {
      id: 'narrative_cache'
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 86400  // 24 h
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/computed_at/?' }
        ]
        excludedPaths: [{ path: '/*' }]
      }
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

// signal_events: append-only log of lifecycle stage transitions (ADR-0030 forward-log).
// One doc per (ticker, hour, transition) emitted by narrative-detector when a
// ticker's committed stage changes. Price fields (px_at_signal, px_t5/10/20,
// SPY counterparts) are populated by a separate backfill job (next commit).
//
// Partition key /ticker — point-readable for "all transitions for X", and the
// "all recent transitions across universe" view filters by event_date range
// which scans a bounded slice of partitions.
//
// No TTL — this is validation data and must accumulate indefinitely so we
// build a real backtest sample size over time.
resource signalEventsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'signal_events'
  properties: {
    resource: {
      id: 'signal_events'
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
          { path: '/event_date/?' }
          { path: '/new_stage/?' }
          { path: '/prev_stage/?' }
          { path: '/confidence/?' }
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
