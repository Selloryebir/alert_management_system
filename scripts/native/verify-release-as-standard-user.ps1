[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [string]$OutputRoot = "",
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$ReleaseVersion = "1.0.0-rc.1",
    [switch]$BusinessRelease
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$verificationScript = Join-Path $PSScriptRoot "verify-release.ps1"
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$archive = [IO.Path]::GetFullPath($ArchivePath)
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot ".runtime\native\verification"
}
elseif (-not [IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)

foreach ($required in @($verificationScript, $powerShellExe, $archive)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "标准用户验收缺少输入：$required"
    }
}

$userName = "amsci" + ([Diagnostics.Process]::GetCurrentProcess().Id)
$plainPassword = "Ams!" + [Guid]::NewGuid().ToString("N") + "a1"
$securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force
$credential = New-Object Management.Automation.PSCredential(
    ($env:COMPUTERNAME + "\" + $userName), $securePassword)
$captureRoot = Join-Path $repositoryRoot ".runtime\native\standard-user-verification"
$temporaryRoot = Join-Path $captureRoot "temp"
$stdout = Join-Path $captureRoot "stdout.log"
$stderr = Join-Path $captureRoot "stderr.log"
$userCreated = $false
$childEnvironment = @{
    TEMP = $temporaryRoot
    TMP = $temporaryRoot
}
$previousEnvironment = @{}
$principal = $null
$primaryFailure = $null
$cleanupFailure = $null

function Quote-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $verificationScript,
    "-ArchivePath", $archive, "-OutputRoot", $OutputRoot,
    "-ReleaseVersion", $ReleaseVersion
)
if ($BusinessRelease) {
    $arguments += "-BusinessRelease"
}
$argumentLine = ($arguments | ForEach-Object { Quote-NativeArgument ([string]$_) }) -join " "

function Invoke-VerificationProcess {
    param([Management.Automation.PSCredential]$RunCredential)
    New-Item -ItemType Directory -Path $captureRoot, $temporaryRoot -Force | Out-Null
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    foreach ($name in $childEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$childEnvironment[$name], "Process")
    }
    try {
        $parameters = @{
            FilePath = $powerShellExe
            ArgumentList = $argumentLine
            WorkingDirectory = $repositoryRoot
            Wait = $true
            PassThru = $true
            RedirectStandardOutput = $stdout
            RedirectStandardError = $stderr
        }
        if ($null -ne $RunCredential) {
            $parameters["Credential"] = $RunCredential
            $parameters["LoadUserProfile"] = $true
        }
        $process = Start-Process @parameters
    } finally {
        foreach ($name in $childEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
        }
    }
    foreach ($capture in @($stdout, $stderr)) {
        if (Test-Path -LiteralPath $capture -PathType Leaf) {
            Get-Content -LiteralPath $capture -Encoding UTF8 | ForEach-Object { Write-Host $_ }
        }
    }
    return $process
}

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
$currentIsAdministrator = $currentPrincipal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $currentIsAdministrator) {
    Write-Host "当前 Windows 身份 $($currentIdentity.Name) 为标准用户，直接执行发布验收。"
    $process = Invoke-VerificationProcess
    if ($process.ExitCode -ne 0) {
        throw "标准用户 Windows 原生验收失败，退出码：$($process.ExitCode)"
    }
    return
}

try {
    New-LocalUser -Name $userName -Password $securePassword -AccountNeverExpires `
        -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
    $userCreated = $true

    $principal = $env:COMPUTERNAME + "\" + $userName
    $aclProcess = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\icacls.exe") `
        -ArgumentList ((Quote-NativeArgument $repositoryRoot) + " /grant " +
            (Quote-NativeArgument ($principal + ":(OI)(CI)M")) + " /Q") `
        -Wait -PassThru -WindowStyle Hidden
    if ($aclProcess.ExitCode -ne 0) {
        throw "无法授予标准验收用户仓库目录权限，icacls 退出码：$($aclProcess.ExitCode)"
    }

    $process = Invoke-VerificationProcess $credential
    if ($process.ExitCode -ne 0) {
        throw "标准用户 Windows 原生验收失败，退出码：$($process.ExitCode)"
    }
}
catch {
    $primaryFailure = $_.Exception
}
finally {
    $cleanupFailure = $null
    if (-not [string]::IsNullOrWhiteSpace($principal)) {
        try {
            $aclRemove = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\icacls.exe") `
                -ArgumentList ((Quote-NativeArgument $repositoryRoot) + " /remove:g " +
                    (Quote-NativeArgument $principal) + " /Q") `
                -Wait -PassThru -WindowStyle Hidden
            if ($aclRemove.ExitCode -ne 0) {
                $cleanupFailure = "无法删除临时标准用户的仓库 ACL，icacls 退出码：$($aclRemove.ExitCode)"
            } else {
                $remainingRules = @((Get-Acl -LiteralPath $repositoryRoot).Access |
                    Where-Object { $_.IdentityReference.Value -eq $principal })
                if ($remainingRules.Count -ne 0) {
                    $cleanupFailure = "临时标准用户的仓库 ACL 仍然存在。"
                }
            }
        } catch {
            $cleanupFailure = "删除临时标准用户的仓库 ACL 失败：$($_.Exception.Message)"
        }
    }
    if ($userCreated) {
        try {
            Remove-LocalUser -Name $userName -ErrorAction Stop
        } catch {
            $cleanupFailure = (($cleanupFailure, "删除临时标准用户失败：$($_.Exception.Message)") `
                | Where-Object { $_ }) -join "；"
        }
    }
}

$failures = @()
if ($null -ne $primaryFailure) { $failures += $primaryFailure.Message }
if (-not [string]::IsNullOrWhiteSpace($cleanupFailure)) { $failures += $cleanupFailure }
if ($failures.Count -gt 0) { throw ($failures -join "；") }
