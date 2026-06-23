// =============================================================================
// Container Apps environment + screener precomputation jobs (ADR-0024/0025).
//
// One Consumption-plan environment hosts the four screener jobs:
//   job-screener-csp    — */15 * * * *   STRATEGY=csp
//   job-screener-cc     — */15 * * * *   STRATEGY=cc
//   job-screener-ditm   — */15 * * * *   STRATEGY=ditm
//   job-screener-swing  — */15 * * * *   STRATEGY=swing
//
// All four jobs share the same image and select their behaviour via STRATEGY.
// CI workflows update the image on every push to main via
// `az containerapp job update`.
//
// The environment name keeps the historical `cae-narrative-` prefix to avoid
// recreating the env in the live deployment.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for environment + app names.')
param nameSuffix string

@description('Tags applied to all resources.')
param tags object

@description('Resource ID of the Log Analytics workspace from the monitoring module.')
param logAnalyticsWorkspaceId string

@description('Container image for job-screener-csp (ADR-0024). Preserved from live deployment by CI workflow.')
param screenerCspImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('Container image for job-screener-cc (ADR-0024). Preserved from live deployment by CI workflow.')
param screenerCcImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('Container image for job-screener-swing (ADR-0025). Preserved from live deployment by CI workflow.')
param screenerSwingImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('Container image for job-screener-ditm (ADR-0024). Preserved from live deployment by CI workflow.')
param screenerDitmImage string = 'ghcr.io/ashwinchandlapur/options-screener-worker:latest'

@description('GHCR username for pulling worker images. Leave empty to skip registry binding (placeholder/public images only).')
param ghcrUsername string = ''

@description('GHCR password / PAT with read:packages. Provided by CI workflow via secret param.')
@secure()
param ghcrPassword string = ''

@description('Cosmos DB account endpoint passed to workers as COSMOS_ENDPOINT.')
param cosmosEndpoint string = ''

// GHCR pull-credential blocks. When ghcrUsername is non-empty the deployment
// is authoritative for these registry credentials. When the params are empty
// we omit the blocks entirely so existing credentials are preserved across
// deploys.
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

// Historical name — kept to avoid recreating the env in the live deployment.
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

// ADR-0024 / ADR-0025: four screener precomputation jobs (CSP, CC, DITM, Swing).
// All four share the same image (STRATEGY env selects which screener to run).
// Cron: */15 * * * * — 14-min replica timeout fits inside the 15-min window.

resource screenerCspJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-screener-csp'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 840
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
          name: 'screener-csp'
          image: screenerCspImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'COSMOS_ENDPOINT',              value: cosmosEndpoint }
            { name: 'STRATEGY',                     value: 'csp' }
            { name: 'LOG_LEVEL',                    value: 'INFO' }
            { name: 'MIN_REFRESH_SECONDS_MARKET',   value: '900' }
            { name: 'MIN_REFRESH_SECONDS_OFF',      value: '14400' }
          ]
        }
      ]
    }
  }
}

resource screenerCcJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-screener-cc'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 840
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
          name: 'screener-cc'
          image: screenerCcImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'COSMOS_ENDPOINT',              value: cosmosEndpoint }
            { name: 'STRATEGY',                     value: 'cc' }
            { name: 'LOG_LEVEL',                    value: 'INFO' }
            { name: 'MIN_REFRESH_SECONDS_MARKET',   value: '900' }
            { name: 'MIN_REFRESH_SECONDS_OFF',      value: '14400' }
          ]
        }
      ]
    }
  }
}

resource screenerDitmJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-screener-ditm'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 840
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
          name: 'screener-ditm'
          image: screenerDitmImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'COSMOS_ENDPOINT',              value: cosmosEndpoint }
            { name: 'STRATEGY',                     value: 'ditm' }
            { name: 'LOG_LEVEL',                    value: 'INFO' }
            { name: 'MIN_REFRESH_SECONDS_MARKET',   value: '900' }
            { name: 'MIN_REFRESH_SECONDS_OFF',      value: '14400' }
          ]
        }
      ]
    }
  }
}

resource screenerSwingJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-screener-swing'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 840
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
          name: 'screener-swing'
          image: screenerSwingImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'COSMOS_ENDPOINT',              value: cosmosEndpoint }
            { name: 'STRATEGY',                     value: 'swing' }
            { name: 'LOG_LEVEL',                    value: 'INFO' }
            { name: 'MIN_REFRESH_SECONDS_MARKET',   value: '900' }
            { name: 'MIN_REFRESH_SECONDS_OFF',      value: '14400' }
          ]
        }
      ]
    }
  }
}

output envId string = env.id
output envName string = env.name
output screenerCspJobPrincipalId string = screenerCspJob.identity.principalId
output screenerCcJobPrincipalId string = screenerCcJob.identity.principalId
output screenerDitmJobPrincipalId string = screenerDitmJob.identity.principalId
output screenerSwingJobPrincipalId string = screenerSwingJob.identity.principalId
