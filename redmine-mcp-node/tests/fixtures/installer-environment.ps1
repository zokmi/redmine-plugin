param([string]$Installer, [ValidateSet('absent','empty','nonempty','launch-restore-failure')][string]$Scenario)
$ErrorActionPreference = 'Stop'

function Assert-EnvironmentState([string]$phase) {
    $environment = [Environment]::GetEnvironmentVariables('Process')
    $actualState = if (-not $environment.Contains('PYTHONUTF8')) { 'absent' }
        elseif ($environment['PYTHONUTF8'] -ceq '') { 'empty' } else { 'nonempty' }
    if ($actualState -cne $Scenario -or ($Scenario -eq 'nonempty' -and $environment['PYTHONUTF8'] -cne '0')) {
        throw "PYTHONUTF8 $phase should preserve its $Scenario state, but was $actualState."
    }
}

function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$Wait, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    if ($Scenario -eq 'launch-restore-failure') {
        $script:fixtureLaunched = $true
        return $script:fixtureProcess
    }
    $script:childPythonUtf8 = $env:PYTHONUTF8
    Set-Content -LiteralPath $RedirectStandardOutput -Value $config -Encoding UTF8
    $null = New-Item -ItemType File -Path $RedirectStandardError
    return @{ ExitCode = $script:probeExitCode }
}

function Stop-Process {
    param($InputObject, $ErrorAction)
    if ($InputObject -ne $script:fixtureProcess) { throw 'The installer did not stop the process it launched.' }
    $script:fixtureStopped = $true
}

$serviceRoot = Join-Path ([IO.Path]::GetTempPath()) ('redmine-installer-env-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $serviceRoot
try {
    # Load the actual installer functions without initializing a service or reading user credentials.
    $installerAst = [Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$null, [ref]$null)
    $functionsFile = Join-Path $serviceRoot 'installer-functions.ps1'
    $installerAst.FindAll({ param($ast) $ast -is [Management.Automation.Language.FunctionDefinitionAst] }, $true) |
        ForEach-Object { $_.Extent.Text } | Set-Content -LiteralPath $functionsFile -Encoding UTF8
    . $functionsFile

    if ($Scenario -eq 'launch-restore-failure') {
        $node = 'fixture-node.exe'
        $tokenName = 'REDMINE_MCP_LOCAL_TOKEN'
        $token = 'a' * 32
        $Port = 18765
        Remove-Item -LiteralPath Env:REDMINE_MCP_STORAGE_ROOT -ErrorAction SilentlyContinue
        $null = New-Item -ItemType Directory -Path (Join-Path $serviceRoot 'fixture/dist')
        $null = New-Item -ItemType File -Path (Join-Path $serviceRoot 'fixture/dist/cli.js')
        $script:fixtureProcess = [pscustomobject]@{}
        Add-Member -InputObject $script:fixtureProcess -MemberType ScriptMethod -Name WaitForExit -Value {
            param([int]$timeout)
            $script:fixtureWaited = $true
            return $true
        }
        $restore = (Get-Command Restore-ProcessEnvironmentVariable).ScriptBlock
        function Restore-ProcessEnvironmentVariable([string]$name, $previous) {
            & $restore $name $previous
            if ($name -eq $tokenName) { throw 'fixture token restoration failure' }
        }

        $failure = try { Start-ServiceDeployment @{ engine='node'; deployment='fixture' } } catch { $_.Exception.Message }
        if ($failure -cne 'fixture token restoration failure' -or -not $script:fixtureLaunched) {
            throw 'The test did not reach restoration after process launch.'
        }
        if (-not $script:fixtureStopped -or -not $script:fixtureWaited) {
            throw 'The installer did not stop and wait for a process after restoration failed.'
        }
        if (Test-Path -LiteralPath Env:REDMINE_MCP_STORAGE_ROOT) {
            throw 'The installer did not try to remove the temporary storage root after token restoration failed.'
        }
        return
    }

    Assert-EnvironmentState 'at fixture startup'
    $config = Join-Path $serviceRoot 'fixture.toml'
    $item = @{ engine='python'; deployment='fixture' }
    $script:probeExitCode = 0
    if ((Resolve-ServiceConfig $item) -cne $config -or $script:childPythonUtf8 -cne '1') {
        throw 'The configuration probe did not discover the path with UTF-8 enabled.'
    }
    Assert-EnvironmentState 'after successful path resolution'

    Validate-Configuration $item
    if ($script:childPythonUtf8 -cne '1') { throw 'Python validation did not enable UTF-8.' }
    Assert-EnvironmentState 'after successful validation'

    $script:probeExitCode = 1
    $failure = try { Resolve-ServiceConfig $item } catch { $_.Exception.Message }
    if ($failure -cne 'Cannot determine the Python deployment configuration path.') { throw 'Path resolution did not reject the failing probe.' }
    Assert-EnvironmentState 'after failed path resolution'

    $failure = try { Validate-Configuration $item } catch { $_.Exception.Message }
    if ($failure -cne 'Configuration validation failed; existing service was not stopped.') { throw 'Configuration validation did not reject the failing probe.' }
    Assert-EnvironmentState 'after failed validation'
} finally {
    Remove-Item -LiteralPath $serviceRoot -Recurse -Force
}
