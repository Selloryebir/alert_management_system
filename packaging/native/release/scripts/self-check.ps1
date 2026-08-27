[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

try {
    $releaseRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $configPath = Join-Path $releaseRoot "config\runtime.json"
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$config.schema_version -ne 2 -or $config.deployment_mode -ne "LOCAL_NATIVE" -or
            $config.PSObject.Properties.Name -contains "password" -or
            $config.database.PSObject.Properties.Name -contains "password" -or
            [int]$config.ports.postgres -ne 55432 -or [int]$config.ports.algorithm -ne 8001 -or
            [int]$config.ports.backend -ne 8080) {
        throw "runtime.json 固定端口不符合 M6 契约。"
    }

    $required = @("common.ps1", "preflight.ps1", "start.ps1", "stop.ps1", "backup.ps1",
        "backup-status.ps1", "backup-schedule.ps1", "restore-verify.ps1", "cleanup-instance.ps1",
        "reset-demo.ps1", "self-check.ps1")
    foreach ($name in $required) {
        $path = Join-Path $PSScriptRoot $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "缺少发布脚本：$name"
        }
        $bytes = [IO.File]::ReadAllBytes($path)
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
            throw "$name 必须使用 UTF-8 BOM，确保 Windows PowerShell 5.1 正确解析中文。"
        }
        [void][scriptblock]::Create([IO.File]::ReadAllText($path, [Text.Encoding]::UTF8))
        $source = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
        if ($name -ne "self-check.ps1" -and
                $source -match '(?i)wsl\.exe|docker|scripts[\\/]dev|/mnt/[a-z]/|[A-Za-z]:\\Code\\') {
            throw "$name 引用了发布包边界外的开发运行路径。"
        }
    }

    $start = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "start.ps1"), [Text.Encoding]::UTF8)
    foreach ($marker in @("initdb", "pg_ctl", "algorithm-service", "api/v1/health", "Save-RunningProcess",
            "SERVER_ADDRESS", "APP_BOOTSTRAP_ADMIN_PASSWORD_FILE", "ALGORITHM_MODEL_FILE",
            "ALGORITHM_MODEL_KEY_FILE", "Initialize-InstanceIdentity",
            "Initialize-InstanceSecrets", "Initialize-AlgorithmModel")) {
        if ($start.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "start.ps1 缺少必要运行标记：$marker"
        }
    }
    $restore = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "restore-verify.ps1"), [Text.Encoding]::UTF8)
    foreach ($marker in @("initdb", "pg_ctl", "pg_restore", "127.0.0.1", "criticalTables",
            "Get-DatabaseFacts", "Assert-FactsEqual", "RequireCurrentMatch", "database_facts",
            "Enter-InstanceMaintenanceLock", "restore-verification")) {
        if ($restore.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "restore-verify.ps1 缺少隔离恢复或逐表对账标记：$marker"
        }
    }
    $cleanup = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "cleanup-instance.ps1"), [Text.Encoding]::UTF8)
    foreach ($marker in @("Assert-InstanceIdentity", "Assert-OwnedMutableDirectory", "Assert-NoReparsePoints",
            "RemoveBackups", "backup-schedule.ps1", "stop.ps1")) {
        if ($cleanup.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "cleanup-instance.ps1 缺少精确实例清理标记：$marker"
        }
    }
    $backup = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "backup.ps1"), [Text.Encoding]::UTF8)
    foreach ($marker in @("--format=custom", "pg_restore", "[IO.File]::Move", "RetentionCount",
            "sha256", "origin_instance_id", "Enter-InstanceMaintenanceLock",
            "Invoke-RecoveryPointRetention")) {
        if ($backup.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "backup.ps1 缺少必要备份标记：$marker"
        }
    }
    $backupStatus = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "backup-status.ps1"), [Text.Encoding]::UTF8)
    foreach ($marker in @("Get-RecoveryPoint", "VerifyHash", "total_dump_bytes", "latest_success_at")) {
        if ($backupStatus.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "backup-status.ps1 缺少恢复点状态标记：$marker"
        }
    }
    $backupSchedule = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "backup-schedule.ps1"), [Text.Encoding]::UTF8)
    foreach ($marker in @("Get-InstanceBackupTaskName", "Register-ScheduledTask", "Unregister-ScheduledTask",
            "Interactive", "-Scheduled")) {
        if ($backupSchedule.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "backup-schedule.ps1 缺少当前用户计划任务标记：$marker"
        }
    }
    $reset = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "reset-demo.ps1"), [Text.Encoding]::UTF8)
    if ($reset -notmatch '/api/v1/demo/reset' -or $reset -notmatch '/api/v1/auth/login' -or
            $reset -notmatch '/api/v1/auth/csrf' -or $reset -notmatch 'WebSession' -or
            $reset -match '(?i)truncate|remove-item.+PgData') {
        throw "reset-demo.ps1 必须只调用复位 API，不能复制数据库清理逻辑。"
    }

    Write-Host "发布运行模板静态自检通过。"
    exit 0
} catch {
    Write-Error ("发布运行模板静态自检失败：" + $_.Exception.Message)
    exit 1
}
