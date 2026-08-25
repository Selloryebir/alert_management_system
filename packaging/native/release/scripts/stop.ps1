[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

try {
    $context = Get-RuntimeContext
    Initialize-ReleaseDirectories $context
    $workingRoot = Initialize-PostgresWorkingRoot $context
    $backendJava = Join-Path $workingRoot "runtime\jre\bin\java.exe"
    $backendJar = Join-Path $workingRoot "app\core-api.jar"
    $backendExpectedExecutables = @($backendJava, $context.Java) | Select-Object -Unique

    Stop-OwnedProcess $context "backend" $backendExpectedExecutables $backendJar
    Stop-OwnedProcess $context "algorithm" $context.Algorithm

    $pgCtl = Get-PostgresExecutable $context "pg_ctl"
    if ((Test-Path -LiteralPath $pgCtl -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $context.PgData "PG_VERSION") -PathType Leaf)) {
        $postgresDataArgument = Join-Path $workingRoot $context.PgDataArgument
        $pgCtl = Get-PostgresExecutable $context "pg_ctl" $workingRoot
        $statusResult = Invoke-BundledCommandResult $pgCtl @(
            "-D", $postgresDataArgument, "status") $workingRoot
        if ($statusResult.ExitCode -eq 0) {
            Invoke-BundledCommand $pgCtl @(
                "-D", $postgresDataArgument, "-m", "fast", "-w", "stop") $workingRoot | Out-Null
        }
    }
    Remove-PidRecord $context "postgresql"
    Remove-PostgresWorkingRoot $context

    Write-Host "当前发布包服务已停止。"
    exit 0
} catch {
    Write-Error ("停止失败：" + $_.Exception.Message)
    exit 1
}
