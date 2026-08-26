[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$templateRoot = Join-Path $repositoryRoot "packaging\native\release"
$powerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("ams-instance-maintenance-" + [Guid]::NewGuid().ToString("N"))
$taskName = $null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-PowerShellScript {
    param([string]$Path, [string[]]$Arguments = @())
    $argumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Path) + $Arguments
    $capture = Join-Path ([IO.Path]::GetTempPath()) ("ams-maintenance-capture-" + [Guid]::NewGuid().ToString("N"))
    $stdout = $capture + ".out"
    $stderr = $capture + ".err"
    try {
        $process = Start-Process -FilePath $powerShell -ArgumentList $argumentList `
            -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        if ($process.ExitCode -ne 0) {
            Write-Host ([string](Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue))
        }
        return [int]$process.ExitCode
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

function New-TestRelease {
    param([string]$Root, [string]$RecordedRoot)
    New-Item -ItemType Directory -Path $Root | Out-Null
    Copy-Item -LiteralPath (Join-Path $templateRoot "scripts") -Destination $Root -Recurse
    Copy-Item -LiteralPath (Join-Path $templateRoot "config") -Destination $Root -Recurse
    foreach ($directory in @("data\postgresql", "data\secrets", "logs", "pids", "backups")) {
        New-Item -ItemType Directory -Path (Join-Path $Root $directory) -Force | Out-Null
    }
    $commit = "a" * 40
    $instanceId = [Guid]::NewGuid().ToString("N")
    [ordered]@{
        schema_version = 1
        product = "alert-management-system"
        target = "windows-x64"
        source_commit = $commit
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root "release-manifest.json") -Encoding UTF8
    [ordered]@{
        schema_version = 1
        product = "alert-management-system"
        instance_id = $instanceId
        release_root = [IO.Path]::GetFullPath($RecordedRoot).TrimEnd("\")
        source_commit = $commit
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root "data\instance.json") -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $Root "data\postgresql\sentinel.txt") -Value "owned"
    Set-Content -LiteralPath (Join-Path $Root "logs\sentinel.txt") -Value "owned"
    New-TestRecoveryPoint $Root "keep.dump" "backup-one" ([DateTimeOffset]::UtcNow.AddMinutes(-3))
    return $instanceId
}

function New-TestRecoveryPoint {
    param([string]$Root, [string]$Name, [string]$Content, [DateTimeOffset]$CreatedAt)
    $dumpPath = Join-Path $Root ("backups\" + $Name)
    [IO.File]::WriteAllText($dumpPath, $Content, (New-Object Text.UTF8Encoding($false)))
    $identity = Get-Content -LiteralPath (Join-Path $Root "data\instance.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath (Join-Path $Root "release-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    [ordered]@{
        schema_version = 1
        product = "alert-management-system-recovery-point"
        origin_instance_id = [string]$identity.instance_id
        origin_source_commit = [string]$manifest.source_commit
        created_at = $CreatedAt.ToString("o")
        database = "alert_management"
        backup_file = $Name
        size_bytes = [Int64](Get-Item -LiteralPath $dumpPath).Length
        sha256 = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
        pg_restore_list_verified = $true
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath ($dumpPath + ".meta.json") -Encoding UTF8
}

try {
    Assert-True ((Invoke-PowerShellScript (Join-Path $templateRoot "scripts\self-check.ps1")) -eq 0) `
        "发布运行模板静态自检失败。"
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $outsideSentinel = Join-Path $testRoot "outside-sentinel.txt"
    Set-Content -LiteralPath $outsideSentinel -Value "outside"

    $validRoot = Join-Path $testRoot "valid-release"
    $validInstanceId = New-TestRelease $validRoot $validRoot
    $backupStatus = Join-Path $validRoot "scripts\backup-status.ps1"
    Assert-True ((Invoke-PowerShellScript $backupStatus) -eq 0) "有效恢复点状态检查失败。"
    $scheduledBackup = Join-Path $validRoot "scripts\backup.ps1"
    Assert-True ((Invoke-PowerShellScript $scheduledBackup @(
        "-Scheduled", "-RetentionCount", "2")) -ne 0) "缺少运行实例的计划备份本应失败。"
    $scheduledLog = Join-Path $validRoot "logs\scheduled-backup.log"
    Assert-True ((Test-Path -LiteralPath $scheduledLog -PathType Leaf) -and
        (Get-Content -LiteralPath $scheduledLog -Raw -Encoding UTF8) -match 'status=FAILED') `
        "计划备份失败没有写入受控日志。"

    $realBackups = Join-Path $validRoot "backups-real"
    $junctionTarget = Join-Path $testRoot "outside-backups"
    New-Item -ItemType Directory -Path $junctionTarget | Out-Null
    [IO.Directory]::Move((Join-Path $validRoot "backups"), $realBackups)
    New-Item -ItemType Junction -Path (Join-Path $validRoot "backups") -Target $junctionTarget | Out-Null
    try {
        Assert-True ((Invoke-PowerShellScript $backupStatus) -ne 0) `
            "backups 路径存在 reparse 边界时状态检查本应失败。"
    } finally {
        [IO.Directory]::Delete((Join-Path $validRoot "backups"), $false)
        [IO.Directory]::Move($realBackups, (Join-Path $validRoot "backups"))
    }

    New-TestRecoveryPoint $validRoot "second.dump" "backup-two" ([DateTimeOffset]::UtcNow.AddMinutes(-2))
    New-TestRecoveryPoint $validRoot "third.dump" "backup-three" ([DateTimeOffset]::UtcNow.AddMinutes(-1))
    Set-Content -LiteralPath (Join-Path $validRoot "backups\foreign.dump") -Value "foreign"
    $retentionHarness = Join-Path $validRoot "scripts\retention-test.ps1"
@'
param([int]$RetentionCount = 2)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$context = Get-RuntimeContext
Invoke-RecoveryPointRetention $context $RetentionCount
'@ | Set-Content -LiteralPath $retentionHarness -Encoding UTF8
    Assert-True ((Invoke-PowerShellScript $retentionHarness) -eq 0) "恢复点保留策略执行失败。"
    Assert-True (@(Get-ChildItem -LiteralPath (Join-Path $validRoot "backups") `
        -Filter "*.dump.meta.json" -File).Count -eq 2) "保留策略没有精确保留两个有效恢复点。"
    Assert-True (Test-Path -LiteralPath (Join-Path $validRoot "backups\foreign.dump") -PathType Leaf) `
        "保留策略错误删除了无归属文件。"
    Remove-Item -LiteralPath (Join-Path $validRoot "backups\foreign.dump") -Force

    $validPoint = @(Get-ChildItem -LiteralPath (Join-Path $validRoot "backups") -Filter "*.dump" -File)[0]
    $tamperedDump = Join-Path $validRoot "backups\tampered.dump"
    Copy-Item -LiteralPath $validPoint.FullName -Destination $tamperedDump
    $tamperedMetadata = Get-Content -LiteralPath ($validPoint.FullName + ".meta.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $tamperedMetadata.backup_file = "tampered.dump"
    $tamperedMetadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath ($tamperedDump + ".meta.json") -Encoding UTF8
    Add-Content -LiteralPath $tamperedDump -Value "tampered"
    Assert-True ((Invoke-PowerShellScript $backupStatus) -ne 0) "SHA-256 损坏恢复点本应使状态检查失败。"
    Assert-True ((Invoke-PowerShellScript $retentionHarness) -eq 0) "含损坏恢复点时保留策略执行失败。"
    Assert-True (Test-Path -LiteralPath $tamperedDump -PathType Leaf) `
        "保留策略错误删除了未通过哈希验证的恢复点。"
    Remove-Item -LiteralPath $tamperedDump, ($tamperedDump + ".meta.json") -Force
    Assert-True ((Invoke-PowerShellScript $backupStatus) -eq 0) "移除损坏样例后状态检查仍失败。"

    $lockPath = Join-Path $validRoot (".instance-maintenance-" + $validInstanceId + ".lock")
    $heldLock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $validCleanup = Join-Path $validRoot "scripts\cleanup-instance.ps1"
        Assert-True ((Invoke-PowerShellScript $validCleanup @("-Force")) -ne 0) `
            "维护互斥锁被占用时实例清理本应失败。"
        Assert-True (Test-Path -LiteralPath (Join-Path $validRoot "data\instance.json") -PathType Leaf) `
            "锁获取失败后仍清理了实例数据。"
    } finally {
        $heldLock.Dispose()
    }

    $backupSchedule = Join-Path $validRoot "scripts\backup-schedule.ps1"
    $taskName = "AlertManagementSystem-Backup-" + $validInstanceId
    Assert-True ((Invoke-PowerShellScript $backupSchedule @(
        "-Action", "Configure", "-DailyAt", "23:58", "-RetentionCount", "2")) -eq 0) `
        "当前用户每日备份任务配置失败。"
    Assert-True ($null -ne (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) `
        "配置后找不到绑定实例的计划任务。"
    Assert-True ((Invoke-PowerShellScript $validCleanup @("-Force")) -eq 0) `
        "合法实例的精确清理失败。"
    foreach ($directory in @("data", "logs", "pids")) {
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $validRoot $directory))) `
            "合法实例清理后仍存在 $directory。"
    }
    Assert-True (@(Get-ChildItem -LiteralPath (Join-Path $validRoot "backups") -Filter "*.dump" -File).Count -eq 2) `
        "默认清理错误删除了备份。"
    Assert-True ($null -eq (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) `
        "实例清理没有先移除本实例计划任务。"
    Assert-True (Test-Path -LiteralPath $outsideSentinel -PathType Leaf) `
        "实例清理越界删除了外部文件。"

    $newInstanceId = [Guid]::NewGuid().ToString("N")
    New-Item -ItemType Directory -Path (Join-Path $validRoot "data") | Out-Null
    [ordered]@{
        schema_version = 1
        product = "alert-management-system"
        instance_id = $newInstanceId
        release_root = [IO.Path]::GetFullPath($validRoot).TrimEnd("\")
        source_commit = "a" * 40
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $validRoot "data\instance.json") -Encoding UTF8
    Assert-True ((Invoke-PowerShellScript $backupStatus) -eq 0) `
        "重建实例后无法读取保留的外来来源恢复点。"
    Assert-True ((Invoke-PowerShellScript $retentionHarness @("-RetentionCount", "1")) -eq 0) `
        "重建实例执行保留策略失败。"
    Assert-True (@(Get-ChildItem -LiteralPath (Join-Path $validRoot "backups") `
        -Filter "*.dump.meta.json" -File).Count -eq 2) `
        "新实例保留策略错误删除了外来来源恢复点。"

    $spoofRoot = Join-Path $testRoot "spoof-release"
    [void](New-TestRelease $spoofRoot (Join-Path $testRoot "different-release"))
    $spoofCleanup = Join-Path $spoofRoot "scripts\cleanup-instance.ps1"
    Assert-True ((Invoke-PowerShellScript $spoofCleanup @("-Force")) -ne 0) `
        "身份目录不一致的实例清理本应失败。"
    Assert-True (Test-Path -LiteralPath (Join-Path $spoofRoot "data\postgresql\sentinel.txt") -PathType Leaf) `
        "身份核对失败后仍删除了实例数据。"
    Assert-True (Test-Path -LiteralPath $outsideSentinel -PathType Leaf) `
        "失败清理越界删除了外部文件。"

    Write-Host "实例维护定向验证通过：静态语法、精确清理、默认保留备份、身份不符拒绝和越界保护均符合预期。"
    exit 0
} finally {
    if (-not [string]::IsNullOrWhiteSpace($taskName)) {
        $leftoverTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -ne $leftoverTask -and
                [string]$leftoverTask.Description -like ("*release_root=" + $testRoot + "*")) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    if (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
