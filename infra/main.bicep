// =============================================================================
// Narrative Intelligence Platform — root deployment
// =============================================================================
//
// Subscription-scoped (creates/uses the resource group), region: centralus.
// Wires together: Storage, Event Hubs (Basic), Key Vault, Container Apps env,
// App Insights, and Cosmos DB for NoSQL serverless (Phase 2).
//
// See docs/NARRATIVE_METHODOLOGY.md §8 for phasing. This file is safe to
// `bicep build` today but the full Phase 1 deployment requires the parameters
// listed at the bottom.
//
// Cost ceiling: $150/mo. See docs/adr/0014-narrative-cost-substitutions.md for
// the rightsized SKU table this file enforces.
// =============================================================================

targetScope = 'subscription'

@description('Name of the resource group hosting all narrative platform resources.')
param resourceGroupName string = 'options-rg'

@description('Azure region for all resources. Existing stack lives in centralus.')
param location string = 'eastus'

@description('Short suffix appended to globally-unique resource names. Lowercase alnum, 3-9 chars.')
@minLength(3)
@maxLength(9)
param nameSuffix string

@description('Azure AD principal IDs (object IDs) that should receive Key Vault Secrets Officer.')
param keyVaultAdminObjectIds array = []

@description('Region for Cosmos DB account. Defaults to westus2 (eastus has capacity constraints).')
param cosmosLocation string = 'westus2'

@description('Principal IDs granted Cosmos DB Built-in Data Contributor.')
param cosmosDataContributorPrincipalIds array = []

@description('Container image for the ingestion worker. Infra deploy preserves the live image; only falls back to placeholder on first deploy.')
param ingestionImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Container image for job-extractor. Preserved by infra workflow.')
param extractorImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for job-aggregator. Preserved by infra workflow.')
param aggregatorImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for job-classifier. Preserved by infra workflow.')
param classifierImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Tag map applied to every resource.')
param tags object = {
  workload: 'narrative-intelligence'
  costCenter: 'options-screener'
  budget: '150usd-month'
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module storage 'modules/storage.bicep' = {
  scope: rg
  name: 'storage'
  params: {
    location: location
    nameSuffix: nameSuffix
    tags: tags
  }
}

module eventhubs 'modules/eventhubs.bicep' = {
  scope: rg
  name: 'eventhubs'
  params: {
    location: location
    nameSuffix: nameSuffix
    tags: tags
  }
}

module keyvault 'modules/keyvault.bicep' = {
  scope: rg
  name: 'keyvault'
  params: {
    location: location
    nameSuffix: nameSuffix
    tags: tags
    adminObjectIds: keyVaultAdminObjectIds
  }
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    location: location
    nameSuffix: nameSuffix
    tags: tags
  }
}

module containerapps 'modules/containerapps.bicep' = {
  scope: rg
  name: 'containerapps'
  params: {
    location: location
    nameSuffix: nameSuffix
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    keyVaultUri: keyvault.outputs.keyVaultUri
    keyVaultId: keyvault.outputs.keyVaultId
    eventHubNamespaceFqdn: '${eventhubs.outputs.namespaceName}.servicebus.windows.net'
    eventHubNamespaceId: eventhubs.outputs.namespaceId
    cosmosEndpoint: cosmos.outputs.accountEndpoint
    blobAccountName: storage.outputs.storageAccountName
    blobStorageId: storage.outputs.storageAccountId
    ingestionImage: ingestionImage
    extractorImage: extractorImage
    aggregatorImage: aggregatorImage
    classifierImage: classifierImage
  }
}

// Phase 2 — Cosmos DB for NoSQL serverless (replaces Postgres; no region restrictions)
module cosmos 'modules/cosmos.bicep' = {
  scope: rg
  name: 'cosmos'
  params: {
    location: cosmosLocation
    nameSuffix: nameSuffix
    tags: tags
    dataContributorPrincipalIds: cosmosDataContributorPrincipalIds
  }
}

output storageAccountName string = storage.outputs.storageAccountName
output eventHubsNamespace string = eventhubs.outputs.namespaceName
output keyVaultName string = keyvault.outputs.keyVaultName
output containerAppsEnvId string = containerapps.outputs.envId
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output extractorJobPrincipalId string = containerapps.outputs.extractorJobPrincipalId
output aggregatorJobPrincipalId string = containerapps.outputs.aggregatorJobPrincipalId
output classifierJobPrincipalId string = containerapps.outputs.classifierJobPrincipalId
output cosmosEndpoint string = cosmos.outputs.accountEndpoint
