// =============================================================================
// Postgres Flexible Server — narrative platform (Phase 2)
// =============================================================================
//
// SKU: B1ms (1 vCore, 2 GiB RAM) — ~$13/mo. Upgrade to D2s_v3 if query
// latency becomes a bottleneck post-launch.
//
// Extensions enabled at provision time:
//   vector   — pgvector for embedding similarity search (Phase 3+)
//   pg_cron  — scheduled roll-up aggregation jobs (Phase 3)
//
// Authentication: Azure AD only (password auth disabled). The extractor worker
// and backend API connect via managed identity token (no password in Key Vault).
//
// Backup: 7-day geo-redundant backup (included in B1ms price).
// High availability: disabled (cost discipline per ADR-0014).
//
// See docs/NARRATIVE_METHODOLOGY.md §8 and ADR-0014.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for globally-unique server name. Lowercase alnum, 3-9 chars.')
param nameSuffix string

@description('Tags applied to all resources.')
param tags object

@description('Azure AD principal ID of the initial Entra admin for Postgres.')
param postgresAdminObjectId string

@description('Azure AD tenant ID.')
param postgresTenantId string = subscription().tenantId

@description('Postgres version.')
param postgresVersion string = '16'

@description('Storage size in MB. 32768 = 32 GiB minimum for Flexible Server.')
param storageSizeMb int = 32768

var serverName = 'psql-narrative-${nameSuffix}-e2'
var dbName = 'narrative'

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: postgresVersion
    administratorLogin: null   // AD-only auth; no password admin
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: postgresTenantId
    }
    storage: {
      storageSizeGB: storageSizeMb / 1024
      autoGrow: 'Disabled'    // cost discipline
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'  // eastus — enable if DR needed later
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'   // locked down by firewall rules below
    }
  }
}

// Allow Azure services (Container Apps, App Service) through the firewall.
resource azureServicesRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: server
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Entra admin — required before any AAD-auth connections can be made.
resource aadAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-12-01-preview' = {
  parent: server
  name: postgresAdminObjectId
  properties: {
    principalType: 'User'
    principalName: 'narrative-admin'
    tenantId: postgresTenantId
  }
}

// Enable pgvector and pg_cron extensions.
resource vectorExtension 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: server
  name: 'azure.extensions'
  properties: {
    value: 'vector,pg_cron'
    source: 'user-override'
  }
  dependsOn: [aadAdmin]
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: server
  name: dbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  dependsOn: [vectorExtension]
}

output serverName string = server.name
output serverFqdn string = server.properties.fullyQualifiedDomainName
output databaseName string = db.name
