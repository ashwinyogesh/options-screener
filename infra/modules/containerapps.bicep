// =============================================================================
// Container Apps environment + ingestion app + scheduled batch jobs.
//
// One Consumption-plan environment hosts:
//   job-ingestor           — always-on ingestion worker
//   job-extractor          — scheduled every 5 min (EH → GPT-4o-mini → Cosmos signals)
//   job-aggregator         — scheduled every 15 min (Cosmos signals → ticker_timeline)
//   job-classifier         — scheduled every 30 min (conviction-state classification + embeddings)
//   job-narrative-detector — scheduled hourly (HDBSCAN clustering → lifecycle stage assignment)
//   job-acs-scorer         — scheduled every 20 min (ACS components A–D → ticker_timeline)
//
// Jobs are provisioned here as stubs with a placeholder image; CI workflows
// update the image on every push to main via `az containerapp job update`.
//
// Key Vault Secrets User role assignments are created here (next to the consumer)
// per the convention stated in modules/keyvault.bicep.
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

@description('Minimum replicas for job-ingestor. Defaults to 0 so infra deploy never auto-starts it; the ingestion CI workflow sets this to 1 when deploying the real image.')
@minValue(0)
@maxValue(2)
param ingestionMinReplicas int = 1

@description('Container image for job-extractor. Preserved from live deployment by infra workflow.')
param extractorImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for job-aggregator. Preserved from live deployment by infra workflow.')
param aggregatorImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for job-classifier. Preserved from live deployment by infra workflow.')
param classifierImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for job-narrative-detector. Preserved from live deployment by infra workflow.')
param detectorImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for job-acs-scorer. Preserved from live deployment by infra workflow.')
param scorerImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('GHCR username for pulling worker images. Leave empty to skip registry binding (placeholder/public images only).')
param ghcrUsername string = ''

@description('GHCR password / PAT with read:packages. Provided by infra workflow via secret param.')
@secure()
param ghcrPassword string = ''

@description('Key Vault URI passed to workers as KEYVAULT_URI.')
param keyVaultUri string = ''

@description('Event Hubs FQDN passed to workers as EVENT_HUB_NAMESPACE.')
param eventHubNamespaceFqdn string = ''

@description('Storage account name passed to workers as BLOB_ACCOUNT_NAME.')
param blobAccountName string = ''

@description('Cosmos DB account endpoint passed to extractor and aggregator workers as COSMOS_ENDPOINT.')
param cosmosEndpoint string = ''

@description('Resource ID of the Key Vault. Used to assign Key Vault Secrets User to worker managed identities.')
param keyVaultId string = ''

@description('Resource ID of the Blob storage account. Used to assign Storage Blob Data Contributor to job-ingestor.')
param blobStorageId string = ''

@description('Resource ID of the Event Hubs namespace. Used to assign EH Sender/Receiver roles.')
param eventHubNamespaceId string = ''

// Built-in: Key Vault Secrets User
var roleSecretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
// Built-in: Storage Blob Data Contributor
var roleBlobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
// Built-in: Azure Event Hubs Data Sender
var roleEhSender = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2b629674-e913-4c01-ae53-ef4638d8f975')
// Built-in: Azure Event Hubs Data Receiver
var roleEhReceiver = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde')

// GHCR pull-credential blocks. When ghcrUsername is non-empty the deployment
// is authoritative for these registry credentials — declaring them once here
// replaces the older "set via az CLI side-channel" pattern (which broke when
// the worker job had never been deployed). When the params are empty we omit
// the blocks entirely so existing credentials are preserved across deploys.
var ghcrConfigured = !empty(ghcrUsername)
var ghcrSecretName = 'ghcr-password'
var ghcrSecrets = ghcrConfigured ? [
  {
    name: ghcrSecretName
    value: ghcrPassword
  }
] : []
var ghcrRegistries = ghcrConfigured ? [
  {
    server: 'ghcr.io'
    username: ghcrUsername
    passwordSecretRef: ghcrSecretName
  }
] : []

// Placeholder used only if individual image params are not supplied (should not happen after first deploy).

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
  name: 'job-ingestor'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: null // background worker; no HTTP
      secrets: ghcrSecrets
      registries: ghcrRegistries
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
        minReplicas: ingestionMinReplicas
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
      secrets: ghcrSecrets
      registries: ghcrRegistries
    }
    template: {
      containers: [
        {
          name: 'extractor'
          image: extractorImage
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
      secrets: ghcrSecrets
      registries: ghcrRegistries
    }
    template: {
      containers: [
        {
          name: 'aggregator'
          image: aggregatorImage
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

resource classifierJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-classifier'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1500  // 25 min — fits inside 30-min cron window with buffer
      scheduleTriggerConfig: {
        cronExpression: '*/30 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: ghcrSecrets
      registries: ghcrRegistries
    }
    template: {
      containers: [
        {
          name: 'classifier'
          image: classifierImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'KEYVAULT_URI',    value: keyVaultUri }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'LOG_LEVEL',       value: 'INFO' }
          ]
        }
      ]
    }
  }
}

resource detectorJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-narrative-detector'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 3300  // 55 min — fits inside hourly cron window with buffer
      scheduleTriggerConfig: {
        cronExpression: '0 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: ghcrSecrets
      registries: ghcrRegistries
    }
    template: {
      containers: [
        {
          name: 'narrative-detector'
          image: detectorImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'KEYVAULT_URI',    value: keyVaultUri }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'LOG_LEVEL',       value: 'INFO' }
          ]
        }
      ]
    }
  }
}

resource scorerJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-acs-scorer'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 840  // 14 min — fits inside 20-min cron window with 6-min buffer (§6.3)
      scheduleTriggerConfig: {
        cronExpression: '*/20 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: ghcrSecrets
      registries: ghcrRegistries
    }
    template: {
      containers: [
        {
          name: 'acs-scorer'
          image: scorerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'KEYVAULT_URI',    value: keyVaultUri }
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
output classifierJobPrincipalId string = classifierJob.identity.principalId
output detectorJobPrincipalId string = detectorJob.identity.principalId
output scorerJobPrincipalId string = scorerJob.identity.principalId

resource detectorKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultId)) {
  name: guid(keyVaultId, detectorJob.name, roleSecretsUser)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleSecretsUser
    principalId: detectorJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource scorerKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultId)) {
  name: guid(keyVaultId, scorerJob.name, roleSecretsUser)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleSecretsUser
    principalId: scorerJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Key Vault Secrets User role assignments for workers that read KV secrets.
// Aggregator reads only Cosmos (no KV); no assignment needed for it.
// ---------------------------------------------------------------------------

resource ingestionKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultId)) {
  name: guid(keyVaultId, ingestion.name, roleSecretsUser)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleSecretsUser
    principalId: ingestion.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource ingestionBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(blobStorageId)) {
  name: guid(blobStorageId, ingestion.name, roleBlobContributor)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleBlobContributor
    principalId: ingestion.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource extractorKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultId)) {
  name: guid(keyVaultId, extractorJob.name, roleSecretsUser)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleSecretsUser
    principalId: extractorJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource classifierKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultId)) {
  name: guid(keyVaultId, classifierJob.name, roleSecretsUser)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleSecretsUser
    principalId: classifierJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Event Hubs role assignments.
// job-ingestor: Sender on reddit-raw-events (publishes ingested posts)
// job-extractor: Receiver on reddit-raw-events (consumes for extraction)
// ---------------------------------------------------------------------------

resource ingestionEhSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(eventHubNamespaceId)) {
  name: guid(eventHubNamespaceId, ingestion.name, roleEhSender)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleEhSender
    principalId: ingestion.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource extractorEhReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(eventHubNamespaceId)) {
  name: guid(eventHubNamespaceId, extractorJob.name, roleEhReceiver)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: roleEhReceiver
    principalId: extractorJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
