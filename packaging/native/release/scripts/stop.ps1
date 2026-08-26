[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    [void](Assert-InstanceIdentity $context)
    Initialize-ReleaseDirectories $context
    $workingRoot = Initialize-PostgresWorkingRoot $context
    $backendJava = Join-Path $workingRoot "runtime\jre\bin\java.exe"
    $backendJar = Join-Path $workingRoot "app\core-api.jar"
    $backendExpectedExecutables = @($backendJava, $context.Java) | Select-Object -Unique

    Stop-OwnedProcess $context "backend" $backendExpectedExecutables $backendJar
    Stop-OwnedProcess $context "algorithm" $context.Algorithm

    $pgCtl = Get-PostgresExecutable $context "pg_ctl"
    $postgresRecord = Join-Path $context.Pids "postgresql.json"
    $postgresDataArgument = Join-Path $workingRoot $context.PgDataArgument
    $pgCtl = Get-PostgresExecutable $context "pg_ctl" $workingRoot
    $hasCluster = Test-Path -LiteralPath (Join-Path $context.PgData "PG_VERSION") -PathType Leaf
    $hasRecord = Test-Path -LiteralPath $postgresRecord -PathType Leaf
    $record = if ($hasRecord) { Get-OwnedPidRecord $context "postgresql" } else { $null }
    $statusResult = if ($hasCluster) {
        Invoke-BundledCommandResult $pgCtl @("-D", $postgresDataArgument, "status") $workingRoot
    } else { $null }
    if ($hasCluster -and $statusResult.ExitCode -eq 0) {
        if (-not $hasRecord) {
            throw "PostgreSQL 正在运行但缺少 PID 身份记录，拒绝停止未知进程或继续清理。"
        }
        $postgresProcess = Assert-OwnedProcess $context "postgresql" `
            (Get-PostgresExpectedExecutables $context $workingRoot "postgres") $postgresDataArgument
        Invoke-BundledCommand $pgCtl @(
            "-D", $postgresDataArgument, "-m", "fast", "-w", "stop") $workingRoot | Out-Null
        if ($null -ne (Get-CimProcess ([int]$postgresProcess.ProcessId))) {
            throw "PostgreSQL 停止后进程仍存在，拒绝删除 PID 身份记录。"
        }
        Remove-PidRecord $context "postgresql"
    } elseif ($hasCluster -and $statusResult.ExitCode -ne 3) {
        throw "pg_ctl 无法确定当前 PostgreSQL 实例是否停止，拒绝删除 PID 记录或继续清理：$($statusResult.Output)"
    } elseif ($hasRecord) {
        $recordedProcess = Get-CimProcess ([int]$record.pid)
        if ($null -ne $recordedProcess -and (Test-PidRecordProcessMatch $record $recordedProcess)) {
            throw "PostgreSQL 进程仍与当前实例记录匹配，但数据目录未报告运行，拒绝清理。"
        }
        Remove-PidRecord $context "postgresql"
    }
    Remove-PostgresWorkingRoot $context

    Write-Host "当前发布包服务已停止。"
    exit 0
} catch {
    Write-Error ("停止失败：" + $_.Exception.Message)
    exit 1
}
