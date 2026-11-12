$ErrorActionPreference = 'Stop'

$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName = "Redmine MCP ($userSid)"
$package = Join-Path $PSScriptRoot '../redmine-mcp'
$serviceRoot = Join-Path $env:LOCALAPPDATA 'redmine-mcp/service'
$deployment = Join-Path $serviceRoot ([Guid]::NewGuid().ToString('N'))
$uv = (Get-Command uv.exe -CommandType Application | Select-Object -First 1).Source

# Prepare the next runtime before changing the task. The running process keeps its files.
$null = New-Item -ItemType Directory -Path $deployment -Force
foreach ($name in 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'src') {
    Copy-Item -LiteralPath (Join-Path $package $name) -Destination $deployment -Recurse
}
& $uv sync --project $deployment --python 3.13 --locked --no-dev
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed (exit $LASTEXITCODE). The scheduled task was not changed."
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

Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-local-service.ps1') -Destination $deployment
@{
    REDMINE_CONFIG = $env:REDMINE_CONFIG
    XDG_CONFIG_HOME = $env:XDG_CONFIG_HOME
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $deployment 'environment.json') -Encoding UTF8

$runner = Join-Path $deployment 'run-local-service.ps1'
$powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$action = New-ScheduledTaskAction -Execute $powershell -WorkingDirectory $deployment `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File `"$runner`""
$logon = New-ScheduledTaskTrigger -AtLogOn -User $userSid
# An indefinite repeat also recovers a graceful or externally forced process exit.
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $userSid -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

$null = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $retry) `
    -Principal $principal -Settings $settings -Force
Start-ScheduledTask -TaskName $taskName
Write-Output "Scheduled task registered: $taskName"
