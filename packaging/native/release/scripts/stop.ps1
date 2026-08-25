[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

try {
    $context = Get-RuntimeContext
    Initialize-ReleaseDirectories $context

    Stop-OwnedProcess $context "backend" $context.Java $context.BackendJar
    Stop-OwnedProcess $context "algorithm" $context.Algorithm

    $pgCtl = Get-PostgresExecutable $context "pg_ctl"
    if ((Test-Path -LiteralPath $pgCtl -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $context.PgData "PG_VERSION") -PathType Leaf)) {
        $postgresRoot = Initialize-PostgresWorkingRoot $context
        $postgresDataArgument = Join-Path $postgresRoot $context.PgDataArgument
        $pgCtl = Get-PostgresExecutable $context "pg_ctl" $postgresRoot
        $statusResult = Invoke-BundledCommandResult $pgCtl @(
            "-D", $postgresDataArgument, "status") $postgresRoot
        if ($statusResult.ExitCode -eq 0) {
            Invoke-BundledCommand $pgCtl @(
                "-D", $postgresDataArgument, "-m", "fast", "-w", "stop") $postgresRoot | Out-Null
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
