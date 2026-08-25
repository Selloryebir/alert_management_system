[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [string]$OutputRoot = ""
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

function Quote-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

try {
    New-Item -ItemType Directory -Path $captureRoot, $temporaryRoot -Force | Out-Null
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
        "-ArchivePath", $archive, "-OutputRoot", $OutputRoot
    )
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
            Get-Content -LiteralPath $capture | ForEach-Object { Write-Host $_ }
        }
    }
    if ($process.ExitCode -ne 0) {
        throw "标准用户 Windows 原生验收失败，退出码：$($process.ExitCode)"
    }
}
finally {
    if ($userCreated) {
        Remove-LocalUser -Name $userName -ErrorAction SilentlyContinue
    }
}
