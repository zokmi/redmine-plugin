param([string]$Installer)
$ErrorActionPreference = 'Stop'
$source = Get-Content $Installer -Raw
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in 'Get-ProcessEnvironmentEntry','Restore-ProcessEnvironmentVariable','Get-ServiceEnvironmentNames','Protect-ServiceEnvironment','Enter-ServiceEnvironment','Exit-ServiceEnvironment') {
    $fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $fn) { throw "Missing function: $name" }
    . ([scriptblock]::Create($fn.Extent.Text))
}
$env:REDMINE_TEST_SECRET = 'private-test-value'
Restore-ProcessEnvironmentVariable 'REDMINE_TEST_EMPTY' ([pscustomobject]@{Value=''})
$tokenName = 'REDMINE_MCP_LOCAL_TOKEN'
$env:HOME = 'installed-home'
$saved = Protect-ServiceEnvironment
if ($saved -match 'private-test-value|installed-home') { throw 'Snapshot leaked plaintext' }
$env:HOME = 'other-shell'
$env:REDMINE_TEST_EXTRA = 'caller-only'
$previous = Enter-ServiceEnvironment $saved
try {
    $empty = Get-ProcessEnvironmentEntry 'REDMINE_TEST_EMPTY'
    if ($null -eq $empty -or $empty.Value -cne '') { throw 'Present empty variable not restored' }
    if ($env:HOME -cne 'installed-home') { throw 'HOME not restored' }
    if (Test-Path Env:REDMINE_TEST_EXTRA) { throw 'Caller-only variable survived' }
} finally { Exit-ServiceEnvironment $previous }
if ($env:HOME -cne 'other-shell' -or $env:REDMINE_TEST_EXTRA -cne 'caller-only') { throw 'Caller environment not restored' }
$failed = $false
try { Enter-ServiceEnvironment 'invalid ciphertext' } catch { $failed = $true }
if (-not $failed -or $env:HOME -cne 'other-shell') { throw 'Corrupt snapshot mutated environment' }
Write-Output 'PASS: encrypted snapshot, cross-shell restoration, caller cleanup, corrupt snapshot'
# Exercise the real doctor orchestration and fingerprint functions without touching the installed service.
foreach ($name in 'Context-Fingerprint','Assert-DoctorContext','Invoke-ServiceDoctor') {
    $fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    . ([scriptblock]::Create($fn.Extent.Text))
}
$tokenName = 'REDMINE_MCP_LOCAL_TOKEN'; $token = 'test-token-only'; $node = (Get-Command node.exe).Source
$script:configPath = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N') + '.toml')
Set-Content $script:configPath 'original configuration'
function Resolve-ServiceConfig { return $script:configPath }
function Get-UserToken { return $script:testToken }
function Find-OwnedProcess { return $null }
function Get-DoctorListeners { return @() }
function Validate-Runtime {}
function Assert-NodeVersion {}
function Validate-Configuration {}
function Validate-Storage {}
function Start-ServiceDeployment { $script:starts++; return @{ recovered = $true } }
function Save-State { $script:saved = $true }
$script:testToken = $token; $script:starts = 0; $script:saved = $false; $Port = 18765
$env:HOME = 'installation shell'
$item = [pscustomobject]@{ contextVersion=2; engine='python'; image=$node; contextSalt=('a'*32); envHash=''; contextHash=''; environmentSnapshot=(Protect-ServiceEnvironment) }
$item.envHash = Context-Fingerprint $item.contextSalt $item $true
$item.contextHash = Context-Fingerprint $item.contextSalt $item
$env:HOME = 'different shell'
if ((Invoke-ServiceDoctor $item) -ne 'RECOVERED' -or $script:starts -ne 1 -or -not $script:saved) { throw 'Doctor failed cross-shell recovery' }
if ($env:HOME -ne 'different shell') { throw 'Doctor leaked restored environment' }
Set-Content $script:configPath 'changed configuration'
$failure = ''
try { Invoke-ServiceDoctor $item } catch { $failure = $_.Exception.Message }
if ($failure -ne 'CONFIGURATION_CHANGED' -or $script:starts -ne 1) { throw 'Doctor bypassed configuration guard' }
Set-Content $script:configPath 'original configuration'
$script:testToken = 'changed token'
try { Invoke-ServiceDoctor $item } catch { $failure = $_.Exception.Message }
if ($failure -ne 'ENVIRONMENT_CHANGED' -or $script:starts -ne 1) { throw 'Doctor bypassed user token guard' }
Remove-Item $script:configPath
Write-Output 'PASS: doctor cross-shell recovery, config guard, token guard, environment cleanup'
