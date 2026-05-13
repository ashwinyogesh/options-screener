// =============================================================================
// Key Vault — Standard SKU, RBAC authorization.
//
// Holds:
//   - reddit-client-id, reddit-client-secret, reddit-user-agent, reddit-author-salt
//   - postgres-conn, redis-conn (Phase 6)
//   - openai-endpoint, openai-key
//   - acs-component-weights (JSON, hot-reloaded by scorer Job at startup)
//   - conviction-prompt-v1, openai-ticker-disambiguation-prompt
//
// Worker managed identities are granted Key Vault Secrets User by their
// owning Container App / Job module (not here) so the role assignment lives
// next to the consumer.
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for the Key Vault name.')
param nameSuffix string

@description('Tags applied to the vault.')
param tags object

@description('Object IDs of admins receiving Key Vault Secrets Officer.')
param adminObjectIds array = []

var keyVaultName = 'kv-narrative-${nameSuffix}'

// Built-in role: Key Vault Secrets Officer
var roleSecretsOfficer = '/providers/Microsoft.Authorization/roleDefinitions/b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled' // Phase 6: tighten to private endpoint
  }
}

resource adminAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (objectId, idx) in adminObjectIds: {
  name: guid(kv.id, objectId, 'secrets-officer')
  scope: kv
  properties: {
    roleDefinitionId: roleSecretsOfficer
    principalId: objectId
    principalType: 'User'
  }
}]

output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri
output keyVaultId string = kv.id
