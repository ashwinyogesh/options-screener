// =============================================================================
// Container Apps environment + ingestion app.
//
// One Consumption-plan environment hosts the always-on `ca-ingestion` worker
// plus all batch Container Apps Jobs (provisioned in their owning workflows).
//
// The ingestion app is the only thing that runs continuously. Everything else
// is a Job (scale-to-zero) for cost.
//
// Image is pulled from ghcr.io. The pull credential lives in a separate
// secret-named registry on the Container App (set by the deploy workflow).
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for environment + app names.')
param nameSuffix string

@description('Tags applied to all resources.')
param tags object

@description('Resource ID of the Log Analytics workspace from the monitoring module.')
param logAnalyticsWorkspaceId string

@description('Container image for the ingestion worker. Defaults to a placeholder; CI deploy overrides.')
param ingestionImage string = 'ghcr.io/placeholder/narrative-ingestion:latest'

@description('Key Vault URI passed to workers as KEYVAULT_URI.')
param keyVaultUri string = ''

@description('Event Hubs FQDN passed to workers as EVENT_HUB_NAMESPACE.')
param eventHubNamespaceFqdn string = ''

@description('Storage account name passed to workers as BLOB_ACCOUNT_NAME.')
param blobAccountName string = ''

var envName = 'cae-narrative-${nameSuffix}'

resource laWorkspaceRef 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: split(logAnalyticsWorkspaceId, '/')[8]
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: laWorkspaceRef.properties.customerId
        sharedKey: laWorkspaceRef.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource ingestion 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-ingestion'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: null // background worker; no HTTP
      registries: [] // CI workflow patches in ghcr.io credentials
    }
    template: {
      containers: [
        {
          name: 'ingestion'
          image: ingestionImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'KEYVAULT_URI',         value: keyVaultUri }
            { name: 'EVENT_HUB_NAMESPACE',  value: eventHubNamespaceFqdn }
            { name: 'BLOB_ACCOUNT_NAME',    value: blobAccountName }
            { name: 'LOG_LEVEL',            value: 'INFO' }
            // SUBREDDIT_TIERS_JSON is fetched from Key Vault at runtime by the worker.
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2 // hard cap per ADR-0014 cost discipline
      }
    }
  }
}

output envId string = env.id
output envName string = env.name
output ingestionAppName string = ingestion.name
output ingestionPrincipalId string = ingestion.identity.principalId
