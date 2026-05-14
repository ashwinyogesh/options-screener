// =============================================================================
// Container Apps environment + ingestion app + scheduled batch jobs.
//
// One Consumption-plan environment hosts:
//   ca-ingestion  — always-on ingestion worker
//   job-extractor — scheduled every 5 min (EH → GPT-4o-mini → Cosmos signals)
//   job-aggregator— scheduled every 15 min (Cosmos signals → ticker_timeline)
//
// Jobs are provisioned here as stubs with a placeholder image; CI workflows
// (narrative-extractor.yml / narrative-aggregator.yml) update the image on
// every push to main.
//
// Pull credentials for ghcr.io are patched in by the CI workflow via
// `az containerapp job registry set`.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for environment + app names.')
param nameSuffix string

@description('Tags applied to all resources.')
param tags object

@description('Resource ID of the Log Analytics workspace from the monitoring module.')
param logAnalyticsWorkspaceId string

@description('Container image for the ingestion worker. Defaults to a public MCR placeholder; CI deploy overrides with the real ghcr.io image.')
param ingestionImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Key Vault URI passed to workers as KEYVAULT_URI.')
param keyVaultUri string = ''

@description('Event Hubs FQDN passed to workers as EVENT_HUB_NAMESPACE.')
param eventHubNamespaceFqdn string = ''

@description('Storage account name passed to workers as BLOB_ACCOUNT_NAME.')
param blobAccountName string = ''

@description('Cosmos DB account endpoint passed to extractor and aggregator workers as COSMOS_ENDPOINT.')
param cosmosEndpoint string = ''

// Placeholder image for jobs. CI deploy overwrites with the real ghcr.io image.
var placeholderJobImage = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

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

resource extractorJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-extractor'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 120
      scheduleTriggerConfig: {
        cronExpression: '*/5 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [] // CI workflow patches in ghcr.io credentials
    }
    template: {
      containers: [
        {
          name: 'extractor'
          image: placeholderJobImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'KEYVAULT_URI',        value: keyVaultUri }
            { name: 'EVENT_HUB_NAMESPACE', value: eventHubNamespaceFqdn }
            { name: 'COSMOS_ENDPOINT',     value: cosmosEndpoint }
            { name: 'LOG_LEVEL',           value: 'INFO' }
          ]
        }
      ]
    }
  }
}

resource aggregatorJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-aggregator'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 120
      scheduleTriggerConfig: {
        cronExpression: '*/15 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [] // CI workflow patches in ghcr.io credentials
    }
    template: {
      containers: [
        {
          name: 'aggregator'
          image: placeholderJobImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'LOG_LEVEL',       value: 'INFO' }
          ]
        }
      ]
    }
  }
}

output envId string = env.id
output envName string = env.name
output ingestionAppName string = ingestion.name
output ingestionPrincipalId string = ingestion.identity.principalId
output extractorJobPrincipalId string = extractorJob.identity.principalId
output aggregatorJobPrincipalId string = aggregatorJob.identity.principalId
