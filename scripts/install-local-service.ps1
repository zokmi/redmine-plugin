[CmdletBinding()]
param([switch]$Restart, [switch]$UseNode, [ValidateRange(1,65535)][int]$Port = 18765)
$ErrorActionPreference = 'Stop'
$serviceRoot = Join-Path $env:LOCALAPPDATA 'redmine-mcp/service'
$statePath = Join-Path $serviceRoot 'active.json'
$node = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
$version = & $node --version
if ($version -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 22) { throw 'Redmine MCP requires Node.js 22 or later.' }
$tokenName = 'REDMINE_MCP_LOCAL_TOKEN'
$token = [Environment]::GetEnvironmentVariable($tokenName, 'Process')
if (-not $token) { $token = [Environment]::GetEnvironmentVariable($tokenName, 'User') }
if (-not $token) {
    $bytes = New-Object byte[] 32; $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [BitConverter]::ToString($bytes).Replace('-', '')
    [Environment]::SetEnvironmentVariable($tokenName, $token, 'User')
}
if ($token -cnotmatch '\A[A-Za-z0-9_-]{32,}\z') { throw "$tokenName must contain at least 32 URL-safe characters." }
$old = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $old = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($old.deployment -cnotmatch '\A[a-f0-9]{32}\z' -or ($old.engine -and $old.engine -notin @('python','node'))) { throw 'Invalid active service manifest.' }
}
if ($Restart -and -not $old) { throw 'Install the local service once before using -Restart.' }
if ($Restart -and $UseNode) { throw '-Restart uses the installed engine; omit -UseNode.' }
if ($old -and $old.port -and ($Restart -or -not $PSBoundParameters.ContainsKey('Port'))) { $Port = [int]$old.port }
$engine = if ($Restart) { if ($old.engine) { $old.engine } else { 'python' } } elseif ($UseNode) { 'node' } else { 'python' }
$id = if ($Restart) { $old.deployment } else { [Guid]::NewGuid().ToString('N') }
$deployment = Join-Path $serviceRoot $id
function Validate-Runtime($item) {
    $dir = Join-Path $serviceRoot $item.deployment
    if ($item.engine -eq 'node') { $entry = Join-Path $dir 'dist/cli.js' } else { $entry = Join-Path $dir 'local-service.mjs'; if (-not (Test-Path -LiteralPath (Join-Path $dir '.venv/Scripts/python.exe') -PathType Leaf)) { throw 'Python runtime is incomplete.' } }
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Service runtime is incomplete.' }
}
function Stop-Owned($item) {
    if (-not $item) { return $false }
    $p = Get-Process -Id ([int]$item.pid) -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    $expected = if ($item.image) { $item.image } else { $node }
    if ($p.Path -ne $expected -or [string]$p.StartTime.ToUniversalTime().Ticks -ne [string]$item.startedAt) { throw 'Active PID identity does not match manifest; refusing to stop it.' }
    $oldPort = if ($item.port) { [int]$item.port } else { $Port }
    $healthy = $false
    try { $null = Invoke-WebRequest -Uri "http://127.0.0.1:$oldPort/mcp" -UseBasicParsing -TimeoutSec 2 } catch { $healthy = $_.Exception.Response.StatusCode.value__ -eq 401 }
    if (-not $healthy) { throw 'Previous service health could not be verified; refusing to stop it.' }
    Stop-Process -InputObject $p
    if (-not $p.WaitForExit(5000)) { throw 'Previous service did not stop.' }
    return $true
}
function Start-ServiceDeployment($item) {
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
                $healthy = $false
                try { $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -UseBasicParsing -TimeoutSec 2; $healthy = $response.StatusCode -eq 401 }
                catch { $healthy = $_.Exception.Response.StatusCode.value__ -eq 401 }
                if ($healthy) { return @{ deployment=$item.deployment; engine=$item.engine; pid=$p.Id; startedAt=[string]$p.StartTime.ToUniversalTime().Ticks; image=$node; port=$Port } }
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
if ($engine -eq 'node') {
    $storage = if ($env:REDMINE_MCP_STORAGE_ROOT) { $env:REDMINE_MCP_STORAGE_ROOT } else { Join-Path $env:LOCALAPPDATA 'redmine-mcp/attachments' }
    & $node (Join-Path $deployment 'dist/storage-preflight.js') $storage
    if ($LASTEXITCODE -ne 0) { throw 'Attachment storage is incompatible; existing data and permissions were preserved.' }
}
if ($old) { if (-not $old.engine) { $old | Add-Member -NotePropertyName engine -NotePropertyValue 'python' }; Validate-Runtime $old }
$stopped = $false; $started = $null
try {
    if ($old) { $stopped = Stop-Owned $old }
    $started = Start-ServiceDeployment $item
    Save-State $started
    Write-Output "Redmine MCP started (PID $($started.pid)). Logs: $deployment"
} catch {
    $failure = $_
    if ($started) { $null = Stop-Owned $started }
    if ($stopped) { try { if ($old.port) { $Port = [int]$old.port }; $restored = Start-ServiceDeployment $old; Save-State $restored; Write-Warning 'Previous deployment restored.' } catch { throw "Startup failed and rollback failed: $failure; $_" } }
    throw $failure
}
