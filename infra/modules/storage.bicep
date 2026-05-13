// =============================================================================
// Storage account for raw Reddit events and backtest artifacts.
//
// Standard_LRS, Hot tier, hierarchical namespace OFF (cheaper, no need for
// ADLS semantics). Lifecycle: Hot → Cool @ 30d, Delete @ 365d on the
// reddit-raw container. backtest-results is left at Hot indefinitely.
//
// See ADR-0014: Blob is the durable source of truth because Event Hubs Basic
// only retains 1 day. Ingestion writes Blob first, then publishes to EH.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for globally-unique storage account name.')
param nameSuffix string

@description('Tags applied to the storage account.')
param tags object

var storageAccountName = toLower('stnarrative${nameSuffix}')

resource sa 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    isHnsEnabled: false
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow' // Phase 6: tighten to VNet
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: sa
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource redditRawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'reddit-raw'
  properties: { publicAccess: 'None' }
}

resource backtestResultsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'backtest-results'
  properties: { publicAccess: 'None' }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: sa
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'reddit-raw-lifecycle'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [ 'blockBlob' ]
              prefixMatch: [ 'reddit-raw/' ]
            }
            actions: {
              baseBlob: {
                tierToCool: { daysAfterModificationGreaterThan: 30 }
                delete:     { daysAfterModificationGreaterThan: 365 }
              }
            }
          }
        }
      ]
    }
  }
}

output storageAccountName string = sa.name
output storageAccountId string = sa.id
output redditRawContainerName string = redditRawContainer.name
output backtestResultsContainerName string = backtestResultsContainer.name
