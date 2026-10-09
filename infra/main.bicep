targetScope = 'resourceGroup'

@description('Azure Government POC location.')
@allowed(['usgovvirginia'])
param location string = 'usgovvirginia'

@description('Unique lowercase prefix, 3-7 alphanumeric characters.')
@minLength(3)
@maxLength(7)
param namePrefix string

@description('Microsoft 365 GCC High source tenant. Must match the managed identity tenant.')
param sourceTenantId string

var suffix = uniqueString(resourceGroup().id)
var queueName = 'callrecords'
var blobOwnerRole = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var blobContributorRole = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var queueContributorRole = '974c5e8b-45b9-467a-9b9e-5b3b70e824ec'
var serviceBusSenderRole = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
var serviceBusReceiverRole = '4f6d3b9b-027b-4f4c-9142-0e9a9964616e'

resource lake 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${namePrefix}lake${suffix}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}
resource lakeBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: lake
  name: 'default'
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in ['bronze', 'control']: {
  parent: lakeBlobs
  name: name
  properties: { publicAccess: 'None' }
}]
resource runtime 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${namePrefix}host${suffix}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}
resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${namePrefix}-bus-${suffix}'
  location: location
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: true
  }
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: bus
  name: queueName
  properties: {
    lockDuration: 'PT5M'
    maxDeliveryCount: 10
    requiresDuplicateDetection: false
    deadLetteringOnMessageExpiration: true
  }
}
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan-${suffix}'
  location: location
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic', capacity: 1 }
  properties: { reserved: true }
}
resource app 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-functions-${suffix}'
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage__blobServiceUri', value: runtime.properties.primaryEndpoints.blob }
        { name: 'AzureWebJobsStorage__queueServiceUri', value: runtime.properties.primaryEndpoints.queue }
        { name: 'AzureWebJobsStorage__tableServiceUri', value: runtime.properties.primaryEndpoints.table }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'ServiceBusConnection__fullyQualifiedNamespace', value: '${bus.name}.servicebus.usgovcloudapi.net' }
        { name: 'ServiceBusConnection__credential', value: 'managedidentity' }
        { name: 'CQD_TENANT_ID', value: sourceTenantId }
        { name: 'CQD_BLOB_URL', value: lake.properties.primaryEndpoints.blob }
        { name: 'CQD_QUEUE_NAME', value: queueName }
        { name: 'CQD_LOOKBACK_DAYS', value: '1' }
        { name: 'AzureWebJobs.reconcile.Disabled', value: 'true' }
        { name: 'AzureWebJobs.fetch_record.Disabled', value: 'true' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'ENABLE_ORYX_BUILD', value: 'true' }
      ]
    }
  }
}
resource lakeWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: lake
  name: guid(lake.id, app.id, blobContributorRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRole)
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
resource runtimeRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in [blobOwnerRole, queueContributorRole]: {
  scope: runtime
  name: guid(runtime.id, app.id, role)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}]
resource busRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in [serviceBusSenderRole, serviceBusReceiverRole]: {
  scope: queue
  name: guid(queue.id, app.id, role)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}]

output functionAppName string = app.name
output graphPermissionPrincipalId string = app.identity.principalId
output lakeDfsEndpoint string = lake.properties.primaryEndpoints.dfs
output queueResourceId string = queue.id
