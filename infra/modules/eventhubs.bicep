// =============================================================================
// Event Hubs — Basic SKU, 1 throughput unit.
//
// Two topics:
//   - reddit-raw-events  (4 partitions, 1d retention)  ← ingestion publishes
//   - ticker-events      (4 partitions, 1d retention)  ← extractor republishes
//
// Basic SKU only allows ONE consumer group per topic. We accept this and
// substitute the second `ticker-events` topic for fanout. Blob is the durable
// backing store; if a consumer falls behind beyond 1d, replay from Blob.
//
// See ADR-0014 for the cost rationale (~$11/mo vs ~$45/mo for Standard 2 TU).
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for the Event Hubs namespace name.')
param nameSuffix string

@description('Tags applied to the namespace.')
param tags object

var namespaceName = 'evhns-narrative-${nameSuffix}'

resource ns 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: namespaceName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    isAutoInflateEnabled: false
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled' // Phase 6: tighten if needed
  }
}

resource rawEvents 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ns
  name: 'reddit-raw-events'
  properties: {
    partitionCount: 4
    messageRetentionInDays: 1 // Basic SKU max
  }
}

resource tickerEvents 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ns
  name: 'ticker-events'
  properties: {
    partitionCount: 4
    messageRetentionInDays: 1
  }
}

output namespaceName string = ns.name
output namespaceId string = ns.id
output rawEventsName string = rawEvents.name
output tickerEventsName string = tickerEvents.name
