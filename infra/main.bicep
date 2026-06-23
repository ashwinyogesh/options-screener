// =============================================================================
// Options Screener — shared Azure infrastructure (Cosmos + Container Apps env)
// =============================================================================
//
// Subscription-scoped (creates/uses the resource group).
//
// Wires together:
//   - Log Analytics + App Insights (required by Container Apps env logging)
//   - Container Apps environment + screener precomputation jobs (ADR-0024,
//     ADR-0025): job-screener-csp / -cc / -ditm / -swing
//   - Cosmos DB for NoSQL serverless — screener_* containers (ADR-0024/25)
//     and DD Coach containers (dd_entries, dd_filings_intel)
//
// Resource names use the `narrative` / `cosmos-nr-` / `cae-narrative-`
// historical prefixes to avoid breaking the existing live deployment. They
// no longer reflect the workload scope.
// =============================================================================

targetScope = 'subscription'

@description('Name of the resource group hosting all platform resources.')
param resourceGroupName string = 'options-rg'

@description('Azure region for the resource group, Container Apps env, and monitoring resources.')
param location string = 'eastus'

@description('Short suffix appended to globally-unique resource names. Lowercase alnum, 3-9 chars.')
@minLength(3)
@maxLength(9)
param nameSuffix string

@description('Region for the Cosmos DB account. Defaults to westus2 (eastus has serverless capacity constraints).')
param cosmosLocation string = 'westus2'

@description('Principal IDs granted Cosmos DB Built-in Data Contributor (admins + screener job MIs).')
param cosmosDataContributorPrincipalIds array = []

@description('Principal IDs granted Cosmos DB Built-in Data Reader. Used by the App Service backend (optionsapi) MI for read-only access.')
param cosmosDataReaderPrincipalIds array = []

@description('Container image for job-screener-csp (ADR-0024). Preserved by CI workflow.')
param screenerCspImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('Container image for job-screener-cc (ADR-0024). Preserved by CI workflow.')
param screenerCcImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('Container image for job-screener-ditm (ADR-0024). Preserved by CI workflow.')
param screenerDitmImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('Container image for job-screener-swing (ADR-0025). Preserved by CI workflow.')
param screenerSwingImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('GHCR username for pulling worker images. Leave empty to skip registry binding.')
param ghcrUsername string = ''

@description('GHCR PAT (read:packages). Passed through to Container Apps as a registry secret.')
@secure()
param ghcrPassword string = ''

@description('Tag map applied to every resource.')
param tags object = {
  workload: 'options-screener'
  costCenter: 'options-screener'
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
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
    cosmosEndpoint: cosmos.outputs.accountEndpoint
    screenerCspImage: screenerCspImage
    screenerCcImage: screenerCcImage
    screenerDitmImage: screenerDitmImage
    screenerSwingImage: screenerSwingImage
    ghcrUsername: ghcrUsername
    ghcrPassword: ghcrPassword
  }
}

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

// Auto-grant Cosmos Data Contributor to screener worker MIs. Decoupled from
// the cosmos module to break the circular dependency (containerapps depends
// on cosmos endpoint; cosmos roles depend on containerapps principalIds).
module cosmosRoles 'modules/cosmos-roles.bicep' = {
  scope: rg
  name: 'cosmos-roles'
  params: {
    cosmosAccountName: cosmos.outputs.accountName
    principalIds: [
      containerapps.outputs.screenerCspJobPrincipalId
      containerapps.outputs.screenerCcJobPrincipalId
      containerapps.outputs.screenerDitmJobPrincipalId
      containerapps.outputs.screenerSwingJobPrincipalId
    ]
    dataReaderPrincipalIds: cosmosDataReaderPrincipalIds
  }
}

output containerAppsEnvId string = containerapps.outputs.envId
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output cosmosEndpoint string = cosmos.outputs.accountEndpoint
output screenerCspJobPrincipalId string = containerapps.outputs.screenerCspJobPrincipalId
output screenerCcJobPrincipalId string = containerapps.outputs.screenerCcJobPrincipalId
output screenerDitmJobPrincipalId string = containerapps.outputs.screenerDitmJobPrincipalId
output screenerSwingJobPrincipalId string = containerapps.outputs.screenerSwingJobPrincipalId
