[CmdletBinding()]
param([switch]$Restart, [switch]$Doctor, [switch]$UseNode, [ValidateRange(1,65535)][int]$Port = 18765)
$ErrorActionPreference = 'Stop'
if ($Doctor -and ($Restart -or $UseNode -or $PSBoundParameters.ContainsKey('Port'))) { throw '-Doctor uses the installed engine and port; run it on its own.' }
$serviceRoot = Join-Path $env:LOCALAPPDATA 'redmine-mcp/service'
$statePath = Join-Path $serviceRoot 'active.json'
if ($Doctor -and -not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'NOT_INSTALLED' }
# A process-held exclusive handle serializes staging, stop and manifest publication.
$null = New-Item -ItemType Directory -Path $serviceRoot -Force
try { $installLock = [IO.File]::Open((Join-Path $serviceRoot 'install.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
catch { if ($Doctor) { throw 'SERVICE_BUSY' }; throw 'Another service installation is in progress.' }
try {
$old = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    # Windows PowerShell 5 otherwise reads a BOM-less PowerShell 7 manifest as ANSI.
    try {
        $old = Get-Content -LiteralPath $statePath -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($old.deployment -cnotmatch '\A[a-f0-9]{32}\z' -or ($old.engine -and $old.engine -notin @('python','node'))) { throw 'Invalid active service manifest.' }
    } catch { if ($Doctor) { throw 'INVALID_MANIFEST' }; throw }
}
if ($Doctor) {
    if (-not $old) { throw 'NOT_INSTALLED' }
    $servicePort = 18765
    $servicePid = 0
    $serviceStart = [long]0
    if (($null -ne $old.port -and (-not [int]::TryParse([string]$old.port, [ref]$servicePort) -or $servicePort -lt 1 -or $servicePort -gt 65535)) -or
        -not [int]::TryParse([string]$old.pid, [ref]$servicePid) -or $servicePid -lt 1 -or
        -not [long]::TryParse([string]$old.startedAt, [ref]$serviceStart) -or $serviceStart -lt 1) { throw 'INVALID_MANIFEST' }
    $Port = $servicePort
    $node = if ($old.image) { [string]$old.image } else { (Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
} else { $node = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source }
function Assert-NodeVersion {
    $version = & $node --version
    if ($version -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 22) { throw 'Redmine MCP requires Node.js 22 or later.' }
}
if (-not $Doctor) { Assert-NodeVersion }
$tokenName = 'REDMINE_MCP_LOCAL_TOKEN'
function Get-UserToken { return [Environment]::GetEnvironmentVariable($tokenName, 'User') }
$token = Get-UserToken
$originalUserToken = $token
$persistToken = $false
if ($Doctor -and $token -cnotmatch '\A[A-Za-z0-9_-]{32,}\z') { throw 'TOKEN_UNAVAILABLE' }
if (-not $token) { $token = [Environment]::GetEnvironmentVariable($tokenName, 'Process'); if ($token -cmatch '\A[A-Za-z0-9_-]{32,}\z') { $persistToken = $true } }
if (-not $token) {
    $bytes = New-Object byte[] 32; $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [BitConverter]::ToString($bytes).Replace('-', '')
    $persistToken = $true
}
if ($token -cnotmatch '\A[A-Za-z0-9_-]{32,}\z') { throw "$tokenName must contain at least 32 URL-safe characters." }
function Get-ServicePaths {
    $paths = @{}
    foreach ($entry in Get-ChildItem Env:) {
        if ($entry.Name -in @('REDMINE_CONFIG','REDMINE_MCP_STORAGE_ROOT') -and $entry.Value) { $paths[$entry.Name] = $entry.Value }
    }
    return $paths
}
function Resolve-ServiceConfig($item, [bool]$legacyCombined = $false) {
    if ($env:REDMINE_CONFIG) {
        $config = $env:REDMINE_CONFIG.Trim()
        if ($config -eq '~' -or $config.StartsWith('~/') -or $config.StartsWith('~\')) { $config = Join-Path $env:USERPROFILE $config.Substring(1).TrimStart('\','/') }
        if (-not [IO.Path]::IsPathRooted($config)) { throw 'REDMINE_CONFIG must be absolute for service installation.' }
    } else {
        if ($item.engine -eq 'node' -or $legacyCombined) {
            $roaming = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE 'AppData/Roaming' }
            $config = Join-Path $roaming 'redmine-mcp/config.toml'
            $legacyConfig = if ($item.engine -eq 'node') { Join-Path $serviceRoot 'redmine-mcp/config.toml' } else { Join-Path (Join-Path $serviceRoot $item.deployment) '.venv/Lib/config.toml' }
            if (Test-Path -LiteralPath $legacyConfig -PathType Leaf) { $config = $legacyConfig }
        } else {
            $dir = Join-Path $serviceRoot $item.deployment
            $python = Join-Path $dir '.venv/Scripts/python.exe'
            $suffix = [Guid]::NewGuid().ToString('N')
            $probeOut = Join-Path $serviceRoot ('.config-path-' + $suffix + '.out')
            $probeErr = Join-Path $serviceRoot ('.config-path-' + $suffix + '.err')
            $previousPythonUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
            try {
                [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
                $arguments = '"{0}" --print-path' -f (Join-Path $PSScriptRoot 'validate-service-config.py')
                $probe = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $dir -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $probeOut -RedirectStandardError $probeErr
                $paths = @(Get-Content -LiteralPath $probeOut -Encoding UTF8)
                if ($probe.ExitCode -ne 0 -or $paths.Count -ne 1 -or -not [IO.Path]::IsPathRooted($paths[0])) { throw 'Invalid configuration path.' }
            }
            catch { throw 'Cannot determine the Python deployment configuration path.' }
            finally { [Environment]::SetEnvironmentVariable('PYTHONUTF8', $previousPythonUtf8, 'Process'); Remove-Item -LiteralPath $probeOut, $probeErr -Force -ErrorAction SilentlyContinue }
            $config = $paths[0]
        }
    }
    return [IO.Path]::GetFullPath($config)
}
function Context-Fingerprint([string]$salt, $item, [bool]$identityOnly = $false, [bool]$legacyCombined = $false) {
    $values = [ordered]@{ token = $token; node = $node }
    $names = @(Get-ChildItem Env: | Where-Object { ($_.Name -like 'REDMINE_*' -and $_.Name -ne $tokenName) -or $_.Name -in @('APPDATA','LOCALAPPDATA','USERPROFILE','HOME','HOMEDRIVE','HOMEPATH','XDG_CONFIG_HOME') -or (-not $legacyCombined -and $_.Name -in @('PYTHONPATH','PYTHONHOME','NODE_OPTIONS','NODE_PATH','NODE_EXTRA_CA_CERTS','SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE','HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY')) } | ForEach-Object { $_.Name }) | Sort-Object -Unique
    foreach ($name in $names) { $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $config = Resolve-ServiceConfig $item $legacyCombined
    $values['configPath'] = $config
    if (-not $identityOnly -and (Test-Path -LiteralPath $config -PathType Leaf)) { $values['configBytes'] = [Convert]::ToBase64String([IO.File]::ReadAllBytes($config)) }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $bytes = [Text.Encoding]::UTF8.GetBytes($salt + ($values | ConvertTo-Json -Compress)); return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
function Get-PortListeners([int]$port) {
    $lines = @(& (Join-Path $env:SystemRoot 'System32/netstat.exe') -ano)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify service port ownership.' }
    return $lines | Where-Object { $_ -match ('^\s*TCP\s+\S+:' + $port + '\s+\S+\s+LISTENING\s+\d+\s*$') }
}
function Listener-Owned([int]$port, [int]$pidValue, $listeners = @(Get-PortListeners $port)) {
    $loopback = @($listeners | Where-Object { $_ -match ('^\s*TCP\s+127\.0\.0\.1:' + $port + '\s') })
    return $loopback.Count -eq 1 -and $loopback[0] -match ('\s' + $pidValue + '\s*$')
}
function Test-Authorized([int]$port, [int]$pidValue, [ref]$startupPending, [ref]$upstreamUnavailable) {
    if (-not (Listener-Owned $port $pidValue)) {
        if ($startupPending) { $startupPending.Value = $true }
        return $false
    }
    $body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"installer-health","version":"1"}}}'
    $uri = [uri]"http://127.0.0.1:$port/mcp"
    $webOptions = @{ UseBasicParsing=$true; TimeoutSec=5; MaximumRedirection=0 }
    if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey('NoProxy')) { $webOptions['NoProxy'] = $true }
    elseif ([Net.WebRequest]::DefaultWebProxy -and -not [Net.WebRequest]::DefaultWebProxy.IsBypassed($uri)) { return $false }
    try {
        $response = Invoke-WebRequest -Uri $uri -Method Post -Headers @{ Authorization="Bearer $token"; Accept='application/json, text/event-stream' } -ContentType 'application/json' -Body $body @webOptions
        if ($response.StatusCode -ne 200) { return $false }
        $session = $response.Headers['mcp-session-id']
        if ($session -and (Listener-Owned $port $pidValue)) {
            try { $null = Invoke-WebRequest -Uri $uri -Method Delete -Headers @{ Authorization="Bearer $token"; 'mcp-session-id'=[string]$session } @webOptions } catch {}
        }
        $payload = $response.Content
        if ($payload -match '(?m)^data:\s*(.+)\s*$') { $payload = $Matches[1] }
        $result = $payload | ConvertFrom-Json
        $valid = $result.id -eq 1 -and $null -ne $result.result.serverInfo -and $null -ne $result.result.protocolVersion -and (Listener-Owned $port $pidValue)
        return $valid
    } catch {
        # Retry only proxy failures that did not return an MCP session.
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -in @(502,503)) {
            if ($startupPending) { $startupPending.Value = $true }
            if ($upstreamUnavailable) { $upstreamUnavailable.Value = $true }
        }
        return $false
    }
}
if ($Restart -and -not $old) { throw 'Install the local service once before using -Restart.' }
if ($Restart -and $UseNode) { throw '-Restart uses the installed engine; omit -UseNode.' }
if (-not $Doctor -and $old -and -not $old.contextSalt -and -not $Restart) { throw 'Legacy deployment has no saved context; first restart the installed service to adopt its current environment.' }
$oldConfigChanged = [bool]$old
if (-not $Doctor -and $old -and $old.contextSalt) {
    if ($old.contextSalt -cnotmatch '\A[a-f0-9]{32}\z' -or $old.contextHash -cnotmatch '\A[a-f0-9]{64}\z') { throw 'Invalid service context fingerprint.' }
    $oldConfigChanged = (Context-Fingerprint $old.contextSalt $old $false (-not [bool]$old.envHash)) -cne $old.contextHash
    if ($old.envHash) {
        if ($old.envHash -cnotmatch '\A[a-f0-9]{64}\z' -or (Context-Fingerprint $old.contextSalt $old $true) -cne $old.envHash) { throw 'Service environment, configuration source or effective token changed; refusing to stop the existing deployment.' }
        if ($oldConfigChanged -and -not $Restart) { throw 'Configuration changed since the service started; restart the existing deployment before upgrading.' }
    } elseif ($oldConfigChanged) { throw 'Previous combined context fingerprint cannot be verified. Restore the running configuration and restart once to adopt the new format.' }
    if (-not $old.envHash -and (Resolve-ServiceConfig $old $true) -ine (Resolve-ServiceConfig $old)) { throw 'Previous combined fingerprint used a different configuration source; verify the running configuration before migrating.' }
}
if (-not $Doctor -and $old) {
    if (-not $old.port) { $old | Add-Member -NotePropertyName port -NotePropertyValue 18765 }
    if ($old.port -and ($Restart -or -not $PSBoundParameters.ContainsKey('Port'))) { $Port = [int]$old.port }
}
$reuseDeployment = $Restart -or $Doctor
$engine = if ($reuseDeployment) { if ($old.engine) { $old.engine } else { 'python' } } elseif ($UseNode) { 'node' } else { 'python' }
$id = if ($reuseDeployment) { $old.deployment } else { [Guid]::NewGuid().ToString('N') }
$deployment = Join-Path $serviceRoot $id
function Validate-Runtime($item) {
    $dir = Join-Path $serviceRoot $item.deployment
    if ($item.engine -eq 'node') { $entry = Join-Path $dir 'dist/cli.js' } else { $entry = Join-Path $dir 'local-service.mjs'; if (-not (Test-Path -LiteralPath (Join-Path $dir '.venv/Scripts/python.exe') -PathType Leaf)) { throw 'Python runtime is incomplete.' } }
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Service runtime is incomplete.' }
}
function Validate-Storage($item) {
    if ($item.engine -ne 'node') { return }
    $dir = Join-Path $serviceRoot $item.deployment
    $storage = if ($env:REDMINE_MCP_STORAGE_ROOT) { $env:REDMINE_MCP_STORAGE_ROOT } else { Join-Path $env:LOCALAPPDATA 'redmine-mcp/attachments' }
    & $node (Join-Path $dir 'dist/storage-preflight.js') $storage
    if ($LASTEXITCODE -ne 0) { throw 'Attachment storage is incompatible; existing data and permissions were preserved.' }
}
function Validate-Configuration($item) {
    $dir = Join-Path $serviceRoot $item.deployment
    $previousPythonUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
    $suffix = [Guid]::NewGuid().ToString('N')
    $stdout = Join-Path $serviceRoot ('.validate-' + $suffix + '.out')
    $stderr = Join-Path $serviceRoot ('.validate-' + $suffix + '.err')
    try {
        if ($item.engine -eq 'node') {
            $program = $node
            $arguments = '"{0}" "{1}"' -f (Join-Path $PSScriptRoot 'validate-service-config.mjs'), (Join-Path $dir 'dist/config.js')
        } else {
            [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
            $program = Join-Path $dir '.venv/Scripts/python.exe'
            $arguments = '"{0}"' -f (Join-Path $PSScriptRoot 'validate-service-config.py')
        }
        $validator = Start-Process -FilePath $program -ArgumentList $arguments -WorkingDirectory $dir -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        if ($validator.ExitCode -ne 0) { throw 'Configuration validation failed.' }
    } catch { throw 'Configuration validation failed; existing service was not stopped.' }
    finally { [Environment]::SetEnvironmentVariable('PYTHONUTF8', $previousPythonUtf8, 'Process'); Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue }
}
function Find-OwnedProcess($item) {
    $p = Get-Process -Id ([int]$item.pid) -ErrorAction SilentlyContinue
    if (-not $p) { return }
    $expected = if ($item.image) { $item.image } else { $node }
    if ($p.Path -ne $expected -or [string]$p.StartTime.ToUniversalTime().Ticks -ne [string]$item.startedAt) { throw 'Active PID identity does not match manifest.' }
    return $p
}
function Stop-Owned($item) {
    $p = Find-OwnedProcess $item
    if (-not $p) { return $false }
    $oldPort = if ($item.port) { [int]$item.port } else { 18765 }
    if (-not (Test-Authorized $oldPort $p.Id)) { throw 'Previous service identity or authenticated health could not be verified; refusing to stop it.' }
    Stop-Process -InputObject $p
    if (-not $p.WaitForExit(5000)) { throw 'Previous service did not stop.' }
    return $true
}
function Start-ServiceDeployment($item, [ref]$processRef) {
    Validate-Runtime $item
    $dir = Join-Path $serviceRoot $item.deployment
    $out = Join-Path $dir 'service.log'; $err = Join-Path $dir 'service.err.log'
    $args = if ($item.engine -eq 'node') { '"{0}" serve --port {1}' -f (Join-Path $dir 'dist/cli.js'), $Port } else { '"{0}" "{1}" {2}' -f (Join-Path $dir 'local-service.mjs'), (Join-Path $dir '.venv/Scripts/python.exe'), $Port }
    $previousRoot = [Environment]::GetEnvironmentVariable('REDMINE_MCP_STORAGE_ROOT', 'Process')
    $previous = [Environment]::GetEnvironmentVariable($tokenName, 'Process')
    try { if ($item.engine -eq 'node' -and -not $previousRoot) { [Environment]::SetEnvironmentVariable('REDMINE_MCP_STORAGE_ROOT', (Join-Path $env:LOCALAPPDATA 'redmine-mcp/attachments'), 'Process') }; [Environment]::SetEnvironmentVariable($tokenName, $token, 'Process'); $p = Start-Process -FilePath $node -WindowStyle Hidden -PassThru -WorkingDirectory $dir -ArgumentList $args -RedirectStandardOutput $out -RedirectStandardError $err }
    finally { [Environment]::SetEnvironmentVariable($tokenName, $previous, 'Process'); [Environment]::SetEnvironmentVariable('REDMINE_MCP_STORAGE_ROOT', $previousRoot, 'Process') }
    $deadline = (Get-Date).AddSeconds(100)
    try {
        while ((Get-Date) -lt $deadline) {
            $p.Refresh(); if ($p.HasExited) { throw "Service exited; see $err" }
            if (Select-String -LiteralPath $out -SimpleMatch "Redmine MCP Node.js listening at http://127.0.0.1:$Port/mcp" -Quiet -ErrorAction SilentlyContinue) {
                $startupPending = $false
                if (Test-Authorized $Port $p.Id ([ref]$startupPending)) {
                    $salt = [Guid]::NewGuid().ToString('N')
                    if ($processRef) { $processRef.Value = $p }
                    return @{ deployment=$item.deployment; engine=$item.engine; pid=$p.Id; startedAt=[string]$p.StartTime.ToUniversalTime().Ticks; image=$node; port=$Port; contextSalt=$salt; contextHash=(Context-Fingerprint $salt $item); envHash=(Context-Fingerprint $salt $item $true); servicePaths=(Get-ServicePaths) }
                }
                if (-not $startupPending) { throw 'Service authenticated startup could not be verified.' }
            }
            Start-Sleep -Milliseconds 200
        }
        throw "Service startup timed out; see $err"
    } catch { Stop-Process -InputObject $p -ErrorAction SilentlyContinue; $null = $p.WaitForExit(5000); throw }
}
function Save-State($item) {
    $temp = Join-Path $serviceRoot ('.active-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $backup = $temp + '.bak'
    try { $item | ConvertTo-Json | Set-Content -LiteralPath $temp -Encoding UTF8; if (Test-Path -LiteralPath $statePath) { [IO.File]::Replace($temp, $statePath, $backup) } else { [IO.File]::Move($temp, $statePath) } } finally { Remove-Item -LiteralPath $temp, $backup -Force -ErrorAction SilentlyContinue }
}
function Assert-DoctorContext($item) {
    try { $identity = Context-Fingerprint $item.contextSalt $item $true } catch { throw 'ENVIRONMENT_CHANGED' }
    if ($identity -cne $item.envHash -or [string](Get-UserToken) -cne [string]$token) { throw 'ENVIRONMENT_CHANGED' }
    try { $context = Context-Fingerprint $item.contextSalt $item } catch { throw 'CONFIGURATION_CHANGED' }
    if ($context -cne $item.contextHash) { throw 'CONFIGURATION_CHANGED' }
}
function Get-DoctorListeners {
    try { $listeners = @(Get-PortListeners $Port) } catch { throw 'PORT_CHECK_FAILED' }
    return $listeners
}
function Test-DoctorHealth($process, [int]$attempts) {
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $listeners = @(Get-DoctorListeners)
        if (-not (Listener-Owned $Port $process.Id $listeners)) {
            if ($listeners.Count) { throw 'PORT_OCCUPIED' }
            return $false
        }
        $upstreamUnavailable = $false
        if (Test-Authorized $Port $process.Id $null ([ref]$upstreamUnavailable)) { return $true }
        if (-not $upstreamUnavailable) { throw 'AUTHENTICATED_PROBE_FAILED' }
        if ($attempt -lt $attempts) { Start-Sleep -Milliseconds 1000 }
    }
    return $false
}
function Invoke-ServiceDoctor($item) {
    try { $process = Find-OwnedProcess $item } catch { throw 'PID_IDENTITY_MISMATCH' }
    if ($process) {
        if (Test-DoctorHealth $process 3) { Write-Output 'HEALTHY'; return }
    } elseif (@(Get-DoctorListeners).Count) { throw 'PORT_OCCUPIED' }
    if (-not $item.envHash -or -not $item.contextSalt -or -not $item.engine -or -not $item.image) { throw 'LEGACY_MANIFEST' }
    if ($item.contextSalt -cnotmatch '\A[a-f0-9]{32}\z' -or $item.contextHash -cnotmatch '\A[a-f0-9]{64}\z' -or $item.envHash -cnotmatch '\A[a-f0-9]{64}\z') { throw 'INVALID_MANIFEST' }
    if (-not [IO.Path]::IsPathRooted($node) -or -not (Test-Path -LiteralPath $node -PathType Leaf)) { throw 'RUNTIME_UNAVAILABLE' }

    $restoredPaths = @()
    $launched = $null
    try {
        # The caller may not inherit explicit paths set by the installation shell.
        $savedPaths = if ($item.servicePaths) { $item.servicePaths } else { $item.recoveryPaths }
        foreach ($path in $savedPaths.PSObject.Properties) {
            if ($path.Name -in @('REDMINE_CONFIG','REDMINE_MCP_STORAGE_ROOT') -and $path.Value -and $null -eq [Environment]::GetEnvironmentVariable($path.Name, 'Process')) {
                [Environment]::SetEnvironmentVariable($path.Name, [string]$path.Value, 'Process')
                $restoredPaths += $path.Name
            }
        }
        try { Validate-Runtime $item } catch { throw 'RUNTIME_UNAVAILABLE' }
        Assert-DoctorContext $item
        try { Assert-NodeVersion } catch { throw 'RUNTIME_UNAVAILABLE' }
        try { Validate-Configuration $item } catch { throw 'CONFIGURATION_INVALID' }
        try { Validate-Storage $item *> $null } catch { throw 'STORAGE_UNAVAILABLE' }
        Assert-DoctorContext $item
        if ($process) {
            try { $process = Find-OwnedProcess $item } catch { throw 'PID_IDENTITY_MISMATCH' }
            if ($process) {
                if (Test-DoctorHealth $process 1) { Write-Output 'HEALTHY'; return }
                Assert-DoctorContext $item
                try { Stop-Process -InputObject $process; if (-not $process.WaitForExit(5000)) { throw 'STOP_FAILED' } } catch { throw 'STOP_FAILED' }
            }
        }
        if (@(Get-DoctorListeners).Count) { throw 'PORT_OCCUPIED' }
        try { $recovered = Start-ServiceDeployment $item ([ref]$launched) } catch { throw 'STARTUP_FAILED' }
        Assert-DoctorContext $item
        try { Save-State $recovered } catch { throw 'MANIFEST_UPDATE_FAILED' }
        Write-Output 'RECOVERED'
    } catch {
        if ($launched -and -not $launched.HasExited) { $launched.Kill() }
        throw
    } finally {
        foreach ($name in $restoredPaths) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    }
}
function Install-DoctorHelper {
    $doctorRoot = Join-Path $serviceRoot 'doctor'
    $null = New-Item -ItemType Directory -Path $doctorRoot -Force
    foreach ($name in 'validate-service-config.mjs','validate-service-config.py','install-local-service.ps1','doctor.ps1') {
        $source = Join-Path $PSScriptRoot $name
        $target = Join-Path $doctorRoot $name
        if ($source -ine $target) {
            $temp = $target + '.tmp'
            $backup = $target + '.bak'
            try {
                [IO.File]::Copy($source, $temp, $true)
                if ([IO.File]::Exists($target)) { [IO.File]::Replace($temp, $target, $backup) } else { [IO.File]::Move($temp, $target) }
            } finally { Remove-Item -LiteralPath $temp, $backup -ErrorAction SilentlyContinue }
        }
    }
}
if ($Doctor) {
    Invoke-ServiceDoctor $old
    return
}
if (-not $Restart) {
    $null = New-Item -ItemType Directory -Path $deployment -Force
    if ($engine -eq 'node') {
        $source = Join-Path $PSScriptRoot '../redmine-mcp-node'
        foreach ($name in 'package.json','package-lock.json','tsconfig.json','src') { Copy-Item -LiteralPath (Join-Path $source $name) -Destination $deployment -Recurse }
        Push-Location $deployment
        try {
            & npm.cmd ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
            & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Node build failed.' }
            & npm.cmd prune --omit=dev --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'npm prune failed.' }
        } finally { Pop-Location }
    } else {
        $source = Join-Path $PSScriptRoot '../redmine-mcp'
        foreach ($name in 'pyproject.toml','uv.lock','README.md','LICENSE','src') { Copy-Item -LiteralPath (Join-Path $source $name) -Destination $deployment -Recurse }
        $uv = (Get-Command uv.exe -CommandType Application | Select-Object -First 1).Source
        & $uv sync --project $deployment --python 3.13 --locked --no-dev; if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'local-service.mjs') -Destination $deployment
    }
}
$item = @{ deployment=$id; engine=$engine }
Validate-Runtime $item
Validate-Storage $item
if ($old) { if (-not $old.engine) { $old | Add-Member -NotePropertyName engine -NotePropertyValue 'python' }; Validate-Runtime $old }
$stopped = $false; $started = $null; $persisted = $false
try {
    $currentUserToken = Get-UserToken
    if ([string]$currentUserToken -cne [string]$originalUserToken) { throw 'User service token changed; refusing to stop the existing deployment.' }
    if ($old -and $old.contextSalt -and $old.envHash -and (Context-Fingerprint $old.contextSalt $old $true) -cne $old.envHash) { throw 'Service environment changed while preparing the deployment.' }
    if ($old -and $old.contextSalt -and -not $old.envHash -and (Context-Fingerprint $old.contextSalt $old $false $true) -cne $old.contextHash) { throw 'Previous combined context fingerprint cannot be verified.' }
    if ($old -and $old.contextSalt) {
        $oldConfigChanged = (Context-Fingerprint $old.contextSalt $old $false (-not [bool]$old.envHash)) -cne $old.contextHash
        if ($oldConfigChanged -and -not $Restart) { throw 'Configuration changed during staging; restart the existing deployment before upgrading.' }
    }
    $rollbackSalt = [Guid]::NewGuid().ToString('N')
    $rollbackHash = if ($old) { Context-Fingerprint $rollbackSalt $old } else { $null }
    $candidateSalt = [Guid]::NewGuid().ToString('N')
    $candidateHash = Context-Fingerprint $candidateSalt $item
    Validate-Configuration $item
    if ((Context-Fingerprint $candidateSalt $item) -cne $candidateHash) { throw 'Configuration changed during validation; existing service was not stopped.' }
    if ($old -and (Context-Fingerprint $rollbackSalt $old) -cne $rollbackHash) { throw 'Previous service configuration changed during validation; existing service was not stopped.' }
    if ($old -and -not $Restart -and (Resolve-ServiceConfig $old) -ine (Resolve-ServiceConfig $item)) { throw 'Configuration source differs between deployments; explicit migration is required before upgrading. Existing service was not stopped.' }
    if ($old -and $old.contextSalt) {
        $oldConfigChanged = (Context-Fingerprint $old.contextSalt $old $false (-not [bool]$old.envHash)) -cne $old.contextHash
        if ($oldConfigChanged -and (-not $Restart -or -not $old.envHash)) { throw 'Previous configuration changed during validation; existing service was not stopped.' }
    }
    if ($old) { $stopped = Stop-Owned $old }
    if ($Restart -and -not $stopped -and @(Get-PortListeners $Port).Count) { throw 'Service port is occupied by an unrecorded listener.' }
    $started = Start-ServiceDeployment $item
    if ((Context-Fingerprint $candidateSalt $item) -cne $candidateHash -or [string](Get-UserToken) -cne [string]$originalUserToken) { throw 'Service environment changed during startup.' }
    if ($persistToken) { [Environment]::SetEnvironmentVariable($tokenName, $token, 'User'); $persisted = $true }
    Save-State $started
    Write-Output "Redmine MCP started (PID $($started.pid)). Logs: $deployment"
} catch {
    $failure = $_
    if ($started) { $null = Stop-Owned $started }
    if ($stopped) { try { if ($oldConfigChanged) { throw 'The previous running configuration was changed and is unavailable; the file was preserved and the previous service was not restored.' }; $expectedUserToken = if ($persisted) { $token } else { $originalUserToken }; if ((Context-Fingerprint $rollbackSalt $old) -cne $rollbackHash -or [string](Get-UserToken) -cne [string]$expectedUserToken) { throw 'Prior environment changed; automatic rollback is unsafe.' }; if ($old.port) { $Port = [int]$old.port }; $restored = Start-ServiceDeployment $old; Save-State $restored; Write-Warning 'Previous deployment restored.' } catch { throw "Startup failed and rollback failed: $failure; $_" } }
    throw $failure
}
try { Install-DoctorHelper }
catch { throw 'The service is running, but installing the doctor helper failed.' }
} finally { $installLock.Dispose() }
