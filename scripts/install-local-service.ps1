[CmdletBinding()]
param([switch]$Restart)

$ErrorActionPreference = 'Stop'

$port = 18765
$package = Join-Path $PSScriptRoot '../redmine-mcp'
$serviceRoot = Join-Path $env:LOCALAPPDATA 'redmine-mcp/service'
$statePath = Join-Path $serviceRoot 'active.json'
$node = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
$nodeVersion = & $node --version
if ($nodeVersion -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 22) {
    throw 'Redmine MCP requires Node.js 22 or later.'
}
if ($Restart) {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw 'Install the local service once before using -Restart.'
    }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.deployment -cnotmatch '\A[a-f0-9]{32}\z') {
        throw "Invalid service deployment in $statePath."
    }
    $deploymentId = $state.deployment
} else {
    $deploymentId = [Guid]::NewGuid().ToString('N')
}
$deployment = Join-Path $serviceRoot $deploymentId

$tokenName = 'REDMINE_MCP_LOCAL_TOKEN'
$token = [Environment]::GetEnvironmentVariable($tokenName, 'User')
if (-not $token) {
    $token = [Environment]::GetEnvironmentVariable($tokenName, 'Process')
    if (-not $token) {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $token = [BitConverter]::ToString($bytes).Replace('-', '')
    }
    if ($token -cmatch '\A[A-Za-z0-9_-]{32,}\z') {
        [Environment]::SetEnvironmentVariable($tokenName, $token, 'User')
    }
}
if ($token -cnotmatch '\A[A-Za-z0-9_-]{32,}\z') {
    throw "$tokenName must contain at least 32 URL-safe characters."
}

$runner = Join-Path $deployment 'local-service.mjs'
$python = Join-Path $deployment '.venv/Scripts/python.exe'
if ($Restart) {
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf) -or
        -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "The installed runtime is incomplete. Reinstall it from the plugin."
    }
    $previousService = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($previousService -and $previousService.Path -eq $node -and
        [string]$previousService.StartTime.ToUniversalTime().Ticks -eq $state.startedAt) {
        Stop-Process -InputObject $previousService
        if (-not $previousService.WaitForExit(5000)) {
            throw "Redmine MCP did not stop (PID $($state.pid))."
        }
    }
}
if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port is in use. Stop the existing listener before starting Redmine MCP."
}

if (-not $Restart) {
    # Keep the runtime independent of the versioned plugin cache.
    $uv = (Get-Command uv.exe -CommandType Application | Select-Object -First 1).Source
    $null = New-Item -ItemType Directory -Path $deployment -Force
    foreach ($name in 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'src') {
        Copy-Item -LiteralPath (Join-Path $package $name) -Destination $deployment -Recurse
    }
    & $uv sync --project $deployment --python 3.13 --locked --no-dev
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed (exit $LASTEXITCODE)."
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'local-service.mjs') -Destination $runner
}
$stderr = Join-Path $deployment 'service.err.log'
$stdout = Join-Path $deployment 'service.log'
$previousToken = [Environment]::GetEnvironmentVariable($tokenName, 'Process')
try {
    [Environment]::SetEnvironmentVariable($tokenName, $token, 'Process')
    $service = Start-Process -FilePath $node -WindowStyle Hidden -PassThru `
        -WorkingDirectory $deployment -ArgumentList ('"{0}" "{1}" {2}' -f $runner, $python, $port) `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
} finally {
    [Environment]::SetEnvironmentVariable($tokenName, $previousToken, 'Process')
}

$ready = "Redmine MCP Node.js listening at http://127.0.0.1:$port/mcp"
$deadline = (Get-Date).AddSeconds(100)
while ((Get-Date) -lt $deadline) {
    $service.Refresh()
    if ($service.HasExited) {
        throw "Redmine MCP exited with code $($service.ExitCode). See $stderr"
    }
    if (Select-String -LiteralPath $stdout -SimpleMatch $ready -Quiet -ErrorAction SilentlyContinue) {
        @{
            deployment = $deploymentId
            pid = $service.Id
            startedAt = [string]$service.StartTime.ToUniversalTime().Ticks
        } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
        Write-Output "$ready (PID $($service.Id))"
        Write-Output "Logs: $deployment"
        return
    }
    Start-Sleep -Milliseconds 200
}
Stop-Process -InputObject $service -ErrorAction SilentlyContinue
throw "Redmine MCP startup timed out. See $stderr"
