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
$repositoryAclBackup = Join-Path $captureRoot "repository-acl.txt"

function Quote-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

try {
    New-Item -ItemType Directory -Path $captureRoot, $temporaryRoot -Force | Out-Null
    $aclSave = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\icacls.exe") `
        -ArgumentList ((Quote-NativeArgument $repositoryRoot) + " /save " +
            (Quote-NativeArgument $repositoryAclBackup) + " /Q") `
        -Wait -PassThru -WindowStyle Hidden
    if ($aclSave.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $repositoryAclBackup -PathType Leaf)) {
        throw "无法保存仓库目录原始 ACL，拒绝创建临时标准用户。"
    }
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

    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $verificationScript,
        "-ArchivePath", $archive, "-OutputRoot", $OutputRoot,
        "-ReleaseVersion", $ReleaseVersion
    )
    if ($BusinessRelease) {
        $arguments += "-BusinessRelease"
    }
    $argumentLine = ($arguments | ForEach-Object { Quote-NativeArgument ([string]$_) }) -join " "
    foreach ($name in $childEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$childEnvironment[$name], "Process")
    }
    try {
        $process = Start-Process -FilePath $powerShellExe -Credential $credential -LoadUserProfile `
            -ArgumentList $argumentLine -WorkingDirectory $repositoryRoot -Wait -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }
    finally {
        foreach ($name in $childEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
        }
    }

    foreach ($capture in @($stdout, $stderr)) {
        if (Test-Path -LiteralPath $capture -PathType Leaf) {
            Get-Content -LiteralPath $capture -Encoding UTF8 | ForEach-Object { Write-Host $_ }
        }
    }
    if ($process.ExitCode -ne 0) {
        throw "标准用户 Windows 原生验收失败，退出码：$($process.ExitCode)"
    }
}
finally {
    $cleanupFailure = $null
    try {
        if (Test-Path -LiteralPath $repositoryAclBackup -PathType Leaf) {
            $repositoryParent = Split-Path -Parent $repositoryRoot
            $aclRestore = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\icacls.exe") `
                -ArgumentList ((Quote-NativeArgument $repositoryParent) + " /restore " +
                    (Quote-NativeArgument $repositoryAclBackup) + " /Q") `
                -Wait -PassThru -WindowStyle Hidden
            if ($aclRestore.ExitCode -ne 0) {
                $cleanupFailure = "无法恢复仓库 ACL，icacls 退出码：$($aclRestore.ExitCode)"
            }
        }
    } catch {
        $cleanupFailure = "恢复仓库 ACL 失败：$($_.Exception.Message)"
    }
    if ($userCreated) {
        try {
            Remove-LocalUser -Name $userName -ErrorAction Stop
        } catch {
            $cleanupFailure = (($cleanupFailure, "删除临时标准用户失败：$($_.Exception.Message)") `
                | Where-Object { $_ }) -join "；"
        }
    }
    Remove-Item -LiteralPath $repositoryAclBackup -Force -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrWhiteSpace($cleanupFailure)) {
        throw $cleanupFailure
    }
}
