param([string]$Installer, [ValidateSet('write','read')][string]$Mode, [string]$SnapshotPath)
$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath $Installer -Raw
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
foreach ($name in 'Get-ProcessEnvironmentEntry','Restore-ProcessEnvironmentVariable','Get-ServiceEnvironmentNames','Protect-ServiceEnvironment','Enter-ServiceEnvironment','Exit-ServiceEnvironment','Context-Fingerprint') {
    $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    . ([scriptblock]::Create($fn.Extent.Text))
}
function Resolve-ServiceConfig { return 'C:\fixture-config.toml' }
$tokenName = 'REDMINE_MCP_LOCAL_TOKEN'; $token = 'fixture-token'; $node = 'C:\fixture-node.exe'
$item = @{ contextVersion=2 }; $salt = 'a'*32
if ($Mode -eq 'write') {
    Restore-ProcessEnvironmentVariable 'REDMINE_TEST_EMPTY' ([pscustomobject]@{Value=''})
    $env:HOME = 'installation shell'
    @{ snapshot=(Protect-ServiceEnvironment); hash=(Context-Fingerprint $salt $item $true) } | ConvertTo-Json | Set-Content -LiteralPath $SnapshotPath -Encoding UTF8
} else {
    $saved = Get-Content -LiteralPath $SnapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $env:HOME = 'other shell'
    $previous = Enter-ServiceEnvironment $saved.snapshot
    try {
        if ((Context-Fingerprint $salt $item $true) -cne $saved.hash) { throw 'Cross-PowerShell fingerprint mismatch' }
    } finally { Exit-ServiceEnvironment $previous }
    if ($env:HOME -cne 'other shell') { throw 'Reader environment was not restored' }
}
