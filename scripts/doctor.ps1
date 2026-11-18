[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$messages = @{
    HEALTHY = 'The installed Redmine MCP service answered an authenticated initialize request.'
    RECOVERED = 'Redmine MCP restarted the installed deployment and verified authenticated initialization.'
    NOT_INSTALLED = 'Install the local Redmine MCP service before running doctor.'
    SERVICE_BUSY = 'Doctor could not acquire the service lock. Retry after other service commands finish.'
    INVALID_MANIFEST = 'The active service manifest is invalid. Check the local installation before restarting.'
    TOKEN_UNAVAILABLE = 'The installed user service token is missing or invalid. Check the local service and connector configuration.'
    PID_IDENTITY_MISMATCH = 'The recorded PID does not match the installed process identity. Check the manifest and running processes.'
    PORT_CHECK_FAILED = 'Doctor could not verify local port ownership. Check that Windows netstat is available.'
    PORT_OCCUPIED = 'A different listener holds the configured port. Check port ownership before retrying.'
    AUTHENTICATED_PROBE_FAILED = 'The installed listener failed authenticated MCP initialization. Check the connector token and service logs.'
    LEGACY_MANIFEST = 'The service needs repair and its launch context is unverified. Run install-local-service.ps1 -Restart from the original installation environment.'
    RUNTIME_UNAVAILABLE = 'The installed service runtime is missing or incompatible. Check the recorded deployment and Node.js executable.'
    ENVIRONMENT_CHANGED = 'The service environment, configuration source, or user token changed. Restore the installation environment and retry.'
    CONFIGURATION_CHANGED = 'The service configuration changed. Run install-local-service.ps1 -Restart to validate and adopt it.'
    CONFIGURATION_INVALID = 'The installed deployment could not validate its configuration. Check the service settings.'
    STORAGE_UNAVAILABLE = 'The installed deployment could not validate its attachment storage. Check that its configured storage is available.'
    STOP_FAILED = 'Doctor could not stop the identified unhealthy service process. Check its process state and logs.'
    STARTUP_FAILED = 'The installed deployment failed to start or authenticate. Check its service logs.'
    MANIFEST_UPDATE_FAILED = 'Doctor could not publish the new active manifest. Check permissions in the local service directory.'
    DOCTOR_FAILED = 'Doctor could not finish. Check the local service installation and PowerShell availability.'
}
try {
    $status = & (Join-Path $PSScriptRoot 'install-local-service.ps1') -Doctor 2>$null 3>$null 4>$null 5>$null 6>$null
} catch {
    $status = $_.Exception.Message
}
if ($status -isnot [string] -or -not $messages.ContainsKey($status)) { $status = 'DOCTOR_FAILED' }
Write-Output "$($status): $($messages[$status])"
if ($status -in @('HEALTHY','RECOVERED')) { exit 0 }
exit 1
