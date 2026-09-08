targetScope = 'resourceGroup'

param location string
param certificateName string
param serverFarmId string
@secure()
param certificatePfx string
@secure()
param certificatePassword string
@description('Bind after DNS ownership verification. Traffic moves only when DNS is changed; apps in the same deployment unit require releasing the old binding first.')
param hostnames { siteName: string, hostname: string }[] = []

resource certificate 'Microsoft.Web/certificates@2023-01-01' = {
  name: certificateName
  location: location
  properties: {
    serverFarmId: serverFarmId
    pfxBlob: certificatePfx
    password: certificatePassword
  }
}

// App Service locks a site during hostname changes. Serial deployment also
// handles multiple hostnames on the same site without conflicting operations.
@batchSize(1)
resource bindings 'Microsoft.Web/sites/hostNameBindings@2023-01-01' = [for binding in hostnames: {
  name: '${binding.siteName}/${binding.hostname}'
  properties: {
    siteName: binding.siteName
    hostNameType: 'Verified'
    sslState: 'SniEnabled'
    thumbprint: certificate.properties.thumbprint
  }
}]

output thumbprint string = certificate.properties.thumbprint
