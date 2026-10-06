param location string = resourceGroup().location
param vmName string = 'vm-reviewlens'
param vmSize string = 'Standard_B2s_v2'
param dnsLabel string = 'reviewlens-yassir'
param adminSshPublicKey string
param operatorIp string
param bootstrap string

var tags = { app: 'reviewlens', purpose: 'portfolio-demo' }
var storageName = 'rlbackup${uniqueString(resourceGroup().id)}'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-reviewlens'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.50.0.0/16'] }
    subnets: [{ name: 'app', properties: { addressPrefix: '10.50.1.0/24' } }]
  }
}
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-reviewlens'
  location: location
  tags: tags
  properties: {
    securityRules: [
      { name: 'ssh-owner', properties: { priority: 100, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: '${operatorIp}/32', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '22' } }
      { name: 'https', properties: { priority: 110, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '443' } }
      { name: 'http-certificate', properties: { priority: 120, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '80' } }
    ]
  }
}
resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-reviewlens'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static', publicIPAddressVersion: 'IPv4', dnsSettings: { domainNameLabel: dnsLabel } }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-reviewlens'
  location: location
  tags: tags
  properties: {
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [{ name: 'primary', properties: { privateIPAllocationMethod: 'Dynamic', subnet: { id: '${vnet.id}/subnets/app' }, publicIPAddress: { id: publicIp.id } } }]
  }
}
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', diskSizeGB: 64, managedDisk: { storageAccountType: 'StandardSSD_LRS' } }
      dataDisks: [{ name: 'disk-reviewlens-data', lun: 0, createOption: 'Empty', diskSizeGB: 64, managedDisk: { storageAccountType: 'StandardSSD_LRS' } }]
    }
    osProfile: {
      computerName: vmName
      adminUsername: 'reviewlens'
      customData: base64(bootstrap)
      linuxConfiguration: { disablePasswordAuthentication: true, ssh: { publicKeys: [{ path: '/home/reviewlens/.ssh/authorized_keys', keyData: adminSshPublicKey }] } }
    }
    networkProfile: { networkInterfaces: [{ id: nic.id, properties: { primary: true } }] }
  }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { supportsHttpsTrafficOnly: true, minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false, allowSharedKeyAccess: false }
}
resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: { deleteRetentionPolicy: { enabled: true, days: 14 } }
}
resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'backups'
  properties: { publicAccess: 'None' }
}
resource backupRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(container.id, vm.id, 'backup-writer')
  scope: container
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output hostname string = publicIp.properties.dnsSettings.fqdn
output publicIpAddress string = publicIp.properties.ipAddress
output backupStorageAccount string = storage.name
output virtualMachineName string = vm.name
