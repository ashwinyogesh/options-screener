// =============================================================================
// Monitoring — Log Analytics workspace + App Insights (workspace-based).
//
// Required by the Container Apps environment for log aggregation.
// PerGB2018 / 5GB daily cap to honor the platform cost target. Sampling
// kicks in on burst — accepted tradeoff.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for workspace + component names.')
param nameSuffix string

@description('Tags applied to all resources.')
param tags object

var workspaceName = 'log-narrative-${nameSuffix}'
var appInsightsName = 'appi-narrative-${nameSuffix}'

resource law 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 5 }
    features: { searchVersion: 1 }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
    SamplingPercentage: 100
  }
}

output logAnalyticsWorkspaceId string = law.id
output appInsightsId string = appi.id
output appInsightsConnectionString string = appi.properties.ConnectionString
output appInsightsInstrumentationKey string = appi.properties.InstrumentationKey
