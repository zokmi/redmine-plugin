param([string]$Installer, [int]$Port, [int]$OwnerPid)
$ErrorActionPreference = 'Stop'
$tree = [Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
foreach ($fn in $tree.FindAll({ param($ast) $ast -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    . ([scriptblock]::Create($fn.Extent.Text))
}

function Assert-Probe([string]$scenario, [bool]$valid, [bool]$retry, [bool]$proxyFailure, [int]$expectedOwner = $OwnerPid) {
    $token = $scenario
    $pending = $false
    $unavailable = $false
    $actual = Test-Authorized $Port $expectedOwner ([ref]$pending) ([ref]$unavailable)
    if ($actual -ne $valid -or $pending -ne $retry -or $unavailable -ne $proxyFailure) {
        throw "$scenario got valid=$actual, pending=$pending, upstream=$unavailable."
    }
}

Assert-Probe 'delayed-once' $false $true $false
Assert-Probe 'delayed-once' $true $false $false
Assert-Probe 'unauthorized' $false $false $false
Assert-Probe 'forbidden' $false $false $false
Assert-Probe 'malformed' $false $false $false
Assert-Probe 'incomplete' $false $false $false
Assert-Probe 'unavailable' $false $true $true
Assert-Probe 'wrong-owner' $false $false $false $PID

# The shared deployment budget must not start a request when less than one second remains.
$token = 'expired'
$pending = $false
if ((Test-Authorized $Port $OwnerPid ([ref]$pending) $null ([datetime]::UtcNow)) -or -not $pending) {
    throw 'An expired startup budget must not authorize a deployment.'
}

# A timeout during an established service's doctor check must not authorize a restart.
$token = 'doctor-timeout'
$unavailable = $false
if ((Test-Authorized $Port $OwnerPid $null ([ref]$unavailable)) -or $unavailable) {
    throw 'A steady-state timeout was mistaken for a recoverable upstream failure.'
}
