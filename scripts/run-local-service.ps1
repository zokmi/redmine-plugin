$ErrorActionPreference = 'Stop'

$env:REDMINE_MCP_LOCAL_TOKEN = [Environment]::GetEnvironmentVariable('REDMINE_MCP_LOCAL_TOKEN', 'User')
$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'environment.json') -Raw |
    ConvertFrom-Json
foreach ($name in 'REDMINE_CONFIG', 'XDG_CONFIG_HOME') {
    if ($settings.$name) {
        [Environment]::SetEnvironmentVariable($name, $settings.$name, 'Process')
    }
}

$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $python -m redmine_mcp serve
exit $LASTEXITCODE
