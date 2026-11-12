$ErrorActionPreference = 'Stop'

$port = 18765
$package = Join-Path $PSScriptRoot '../redmine-mcp'
$serviceRoot = Join-Path $env:LOCALAPPDATA 'redmine-mcp/service'
$deployment = Join-Path $serviceRoot ([Guid]::NewGuid().ToString('N'))
$uv = (Get-Command uv.exe -CommandType Application | Select-Object -First 1).Source
$node = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
$nodeVersion = & $node --version
if ($nodeVersion -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 22) {
    throw 'Redmine MCP requires Node.js 22 or later.'
}
if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port is in use. Stop the existing Redmine MCP process before installing."
}

# Keep the runtime independent of the versioned plugin cache.
$null = New-Item -ItemType Directory -Path $deployment -Force
foreach ($name in 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'src') {
    Copy-Item -LiteralPath (Join-Path $package $name) -Destination $deployment -Recurse
}
& $uv sync --project $deployment --python 3.13 --locked --no-dev
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed (exit $LASTEXITCODE)."
}

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
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'local-service.mjs') -Destination $runner
$python = Join-Path $deployment '.venv/Scripts/python.exe'
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
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    $service.Refresh()
    if ($service.HasExited) {
        throw "Redmine MCP exited with code $($service.ExitCode). See $stderr"
    }
    if (Select-String -LiteralPath $stdout -SimpleMatch $ready -Quiet -ErrorAction SilentlyContinue) {
        Write-Output "$ready (PID $($service.Id))"
        Write-Output "Logs: $deployment"
        return
    }
    Start-Sleep -Milliseconds 200
}
Stop-Process -InputObject $service -ErrorAction SilentlyContinue
throw "Redmine MCP startup timed out. See $stderr"
