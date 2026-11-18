// offpeak on Azure Container Apps + Postgres Flexible Server (B1ms).
// Images come from GHCR, so they must be public packages (or add registry credentials).
param location string = resourceGroup().location
param apiImage string
param webImage string
@secure()
param pgPassword string = newGuid() // ponytail: rotates every deploy; move to Key Vault or Entra auth if that bites

var name = 'offpeak'
var dbUrl = 'postgresql://offpeak:${pgPassword}@${pg.properties.fullyQualifiedDomainName}:5432/offpeak?sslmode=require'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${name}-${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: 'offpeak'
    administratorLoginPassword: pgPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: 'offpeak'
}

// lets Azure services (the container apps) reach the server
resource pgFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'azure-services'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

var secrets = [{ name: 'database-url', value: dbUrl }]
var dbEnv = [{ name: 'DATABASE_URL', secretRef: 'database-url' }]
var small = { cpu: json('0.25'), memory: '0.5Gi' }

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${name}-api'
  location: location
  dependsOn: [pgDb, pgFirewall]
  properties: {
    managedEnvironmentId: env.id
    configuration: { secrets: secrets, ingress: { external: false, targetPort: 8000 } }
    template: {
      containers: [{ name: 'api', image: apiImage, env: dbEnv, resources: small }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${name}-worker'
  location: location
  dependsOn: [pgDb, pgFirewall]
  properties: {
    managedEnvironmentId: env.id
    configuration: { secrets: secrets }
    template: {
      containers: [{ name: 'worker', image: apiImage, command: ['python', 'worker.py'], env: dbEnv, resources: small }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${name}-web'
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: { ingress: { external: true, targetPort: 80 } }
    template: {
      containers: [{ name: 'web', image: webImage, env: [{ name: 'API_URL', value: 'http://${api.name}' }], resources: small }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

output url string = 'https://${web.properties.configuration.ingress.fqdn}'
