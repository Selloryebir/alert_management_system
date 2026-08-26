[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [switch]$RequireCurrentMatch
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$criticalTables = @(
    "flyway_schema_history",
    "app_metadata",
    "business_project",
    "user_account",
    "project_membership",
    "import_batch",
    "import_staging",
    "alarm_record",
    "analysis_run",
    "analysis_result",
    "event_chain",
    "event_chain_member",
    "alarm_disposition",
    "disposition_history",
    "analysis_result_override",
    "audit_event"
)
$criticalSequences = @("disposition_history_history_id_seq")

function Resolve-BackupFile {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$RequestedPath
    )
    [void](Assert-OwnedMutableDirectory $Context $Context.Backups `
        (Join-ReleasePath $Context.Root "backups"))
    $resolved = $RequestedPath
    if (-not [IO.Path]::IsPathRooted($resolved)) {
        $resolved = Join-Path $Context.Root $resolved
    }
    $resolved = [IO.Path]::GetFullPath($resolved)
    $backupRoot = Normalize-DirectoryPath $Context.Backups
    if (-not (Normalize-DirectoryPath (Split-Path $resolved -Parent)).Equals(
            $backupRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetExtension($resolved) -ne ".dump" -or
            -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "备份必须是当前发布实例 backups 目录中的 .dump 文件：$resolved"
    }
    $point = Get-RecoveryPoint $Context ($resolved + ".meta.json") -VerifyHash
    return $point
}

function Get-FreeLoopbackPort {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return [int]$listener.LocalEndpoint.Port
    } finally {
        $listener.Stop()
    }
}

function Get-DatabaseFacts {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Database
    )
    $queries = @()
    foreach ($table in $criticalTables) {
        $queries += 'SELECT ''table:' + $table + '|'' || count(*)::text || ''|'' || COALESCE(' +
            'md5(string_agg(row_hash, '''' ORDER BY row_hash)), md5('''')) ' +
            'FROM (SELECT md5(row_to_json(verification_row_ams)::text) AS row_hash ' +
            'FROM public."' + $table + '" AS verification_row_ams) AS table_rows'
    }
    foreach ($sequence in $criticalSequences) {
        $queries += 'SELECT ''sequence:' + $sequence + '|'' || last_value::text || ''|'' || ' +
            'is_called::text FROM public."' +
            $sequence + '"'
    }

    $sql = $queries -join "`nUNION ALL`n"
    $output = Invoke-BundledCommand (Get-PostgresExecutable $Context "psql" $WorkingRoot) @(
        "-X", "-h", "127.0.0.1", "-p", [string]$Port,
        "-U", [string]$Context.Config.database.user, "-d", $Database,
        "-v", "ON_ERROR_STOP=1", "-A", "-t", "-c", $sql) $WorkingRoot
    $facts = [ordered]@{}
    foreach ($line in @([Regex]::Split($output.Trim(), '\r?\n'))) {
        if ($line -match '^(table:[a-z_]+)\|(\d+\|[0-9a-f]{32})$') {
            $facts[$Matches[1]] = $Matches[2]
        } elseif ($line -match '^(sequence:[a-z_]+)\|(\d+\|(true|false))$') {
            $facts[$Matches[1]] = $Matches[2]
        } else {
            throw "数据库事实输出格式无效：$line"
        }
    }
    $expectedKeys = @($criticalTables | ForEach-Object { "table:$_" }) +
        @($criticalSequences | ForEach-Object { "sequence:$_" })
    foreach ($key in $expectedKeys) {
        if (-not $facts.Contains($key)) {
            throw "数据库事实输出缺少：$key"
        }
    }
    return $facts
}

function Assert-FactsEqual {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)]$Actual,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $differences = @()
    foreach ($key in @($Expected.Keys)) {
        if ([string]$Expected[$key] -ne [string]$Actual[$key]) {
            $differences += "$key：预期 $($Expected[$key])，实际 $($Actual[$key])"
        }
    }
    if ($differences.Count -gt 0) {
        throw "$Description`n$($differences -join "`n")"
    }
}

$context = $null
$workingRoot = $null
$verificationCase = $null
$verificationClusterArgument = $null
$temporaryStarted = $false
$maintenanceLock = $null
$oldPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    $identity = Assert-InstanceIdentity $context
    Initialize-ReleaseDirectories $context
    Assert-NoReleasePathReparseBoundary $context $context.Backups
    $maintenanceLock = Enter-InstanceMaintenanceLock $context
    $workingRoot = Initialize-PostgresWorkingRoot $context
    $postgresDataArgument = Join-Path $workingRoot $context.PgDataArgument
    Assert-OwnedProcess $context "postgresql" `
        (Get-PostgresExpectedExecutables $context $workingRoot "postgres") `
        $postgresDataArgument | Out-Null

    $recoveryPoint = Resolve-BackupFile $context $BackupPath
    $backup = $recoveryPoint.BackupPath
    $backupArgument = Join-Path $workingRoot ("backups\" + [IO.Path]::GetFileName($backup))
    Invoke-BundledCommand (Get-PostgresExecutable $context "pg_restore" $workingRoot) @(
        "--list", $backupArgument) $workingRoot | Out-Null

    $caseId = [Guid]::NewGuid().ToString("N")
    if (-not (Test-Path -LiteralPath $context.RestoreVerificationRoot)) {
        New-Item -ItemType Directory -Path $context.RestoreVerificationRoot | Out-Null
    }
    [void](Assert-OwnedMutableDirectory $context $context.RestoreVerificationRoot `
        (Join-ReleasePath $context.Root "data/restore-verification"))
    $verificationCase = Join-Path $context.RestoreVerificationRoot $caseId
    New-Item -ItemType Directory -Path $verificationCase | Out-Null
    $marker = [ordered]@{
        schema_version = 1
        product = "alert-management-system-restore-verification"
        instance_id = [string]$identity.instance_id
        case_id = $caseId
        release_root = Normalize-DirectoryPath $context.Root
    }
    $markerPath = Join-Path $verificationCase "case.json"
    [IO.File]::WriteAllText($markerPath, (($marker | ConvertTo-Json -Depth 3) + "`n"),
        (New-Object Text.UTF8Encoding($false)))

    $verificationRelative = "data\restore-verification\$caseId"
    $verificationClusterArgument = Join-Path $workingRoot ($verificationRelative + "\cluster")
    $passwordArgument = Join-Path $workingRoot "data\secrets\database-password.txt"
    [Environment]::SetEnvironmentVariable("PGPASSWORD",
        (Get-SecretValue $context.DatabasePasswordFile), "Process")
    Invoke-BundledCommand (Get-PostgresExecutable $context "initdb" $workingRoot) @(
        "-D", $verificationClusterArgument,
        "-U", [string]$context.Config.database.user,
        "--encoding=UTF8", "--locale=C", "--auth-local=trust",
        "--auth-host=scram-sha-256", "--pwfile=$passwordArgument") $workingRoot | Out-Null

    $temporaryPort = Get-FreeLoopbackPort
    if ($temporaryPort -eq [int]$context.Config.ports.postgres) {
        throw "隔离恢复端口意外等于正式实例端口，拒绝启动。"
    }
    $temporaryLog = Join-Path $workingRoot ($verificationRelative + "\postgresql.log")
    Invoke-BundledCommandWithoutCapture (Get-PostgresExecutable $context "pg_ctl" $workingRoot) @(
        "-D", $verificationClusterArgument, "-l", $temporaryLog,
        "-o", "-p $temporaryPort -h 127.0.0.1", "-w", "-t", "30", "start") $workingRoot
    $temporaryStarted = $true

    $databaseName = [string]$context.Config.database.name
    if ($RequireCurrentMatch) {
        $sourceBefore = Get-DatabaseFacts $context $workingRoot `
            ([int]$context.Config.ports.postgres) $databaseName
    }
    Invoke-BundledCommand (Get-PostgresExecutable $context "createdb" $workingRoot) @(
        "-h", "127.0.0.1", "-p", [string]$temporaryPort,
        "-U", [string]$context.Config.database.user, "-T", "template0", "-E", "UTF8",
        $databaseName) $workingRoot | Out-Null
    Invoke-BundledCommand (Get-PostgresExecutable $context "pg_restore" $workingRoot) @(
        "-h", "127.0.0.1", "-p", [string]$temporaryPort,
        "-U", [string]$context.Config.database.user, "-d", $databaseName,
        "--exit-on-error", "--no-owner", "--no-privileges", $backupArgument) $workingRoot | Out-Null

    $restored = Get-DatabaseFacts $context $workingRoot $temporaryPort $databaseName
    if ($RequireCurrentMatch) {
        $sourceAfter = Get-DatabaseFacts $context $workingRoot `
            ([int]$context.Config.ports.postgres) $databaseName
        Assert-FactsEqual $sourceBefore $sourceAfter "恢复验证期间源数据库发生变化，请在无业务写入时重试。"
        Assert-FactsEqual $sourceBefore $restored "隔离恢复后的事实与当前源实例不一致。"
    }

    $resultPath = Join-Path $context.Logs ("restore-verification-" +
        [DateTime]::Now.ToString("yyyyMMdd-HHmmss-fff") + ".json")
    [ordered]@{
        backup = $backup
        verified_at = [DateTimeOffset]::UtcNow.ToString("o")
        instance_id = [string]$identity.instance_id
        origin_instance_id = [string]$recoveryPoint.Metadata.origin_instance_id
        origin_source_commit = [string]$recoveryPoint.Metadata.origin_source_commit
        required_current_match = [bool]$RequireCurrentMatch
        restored_to_isolated_instance = $true
        database_facts = $restored
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host "隔离恢复及关键业务表逐表对账通过：$resultPath"
    exit 0
} catch {
    Write-Error ("隔离恢复验证失败：" + $_.Exception.Message)
    exit 1
} finally {
    [Environment]::SetEnvironmentVariable("PGPASSWORD", $oldPassword, "Process")
    if ($null -ne $workingRoot -and $null -ne $verificationClusterArgument) {
        $pgCtl = Get-PostgresExecutable $context "pg_ctl" $workingRoot
        $status = Invoke-BundledCommandResult $pgCtl @(
            "-D", $verificationClusterArgument, "status") $workingRoot
        if ($temporaryStarted -or $status.ExitCode -eq 0) {
            Invoke-BundledCommand $pgCtl @(
                "-D", $verificationClusterArgument, "-m", "fast", "-w", "stop") $workingRoot | Out-Null
        }
    }
    if ($null -ne $verificationCase -and (Test-Path -LiteralPath $verificationCase)) {
        $markerPath = Join-Path $verificationCase "case.json"
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
            throw "隔离恢复目录缺少身份标记，拒绝自动清理：$verificationCase"
        }
        $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $expectedCase = Join-Path $context.RestoreVerificationRoot ([string]$marker.case_id)
        if ($marker.product -ne "alert-management-system-restore-verification" -or
                -not (Normalize-DirectoryPath $verificationCase).Equals(
                    (Normalize-DirectoryPath $expectedCase), [StringComparison]::OrdinalIgnoreCase) -or
                -not (Normalize-DirectoryPath ([string]$marker.release_root)).Equals(
                    (Normalize-DirectoryPath $context.Root), [StringComparison]::OrdinalIgnoreCase)) {
            throw "隔离恢复目录身份不一致，拒绝自动清理：$verificationCase"
        }
        [void](Assert-OwnedMutableDirectory $context $context.RestoreVerificationRoot `
            (Join-ReleasePath $context.Root "data/restore-verification"))
        Remove-Item -LiteralPath $verificationCase -Recurse -Force
        if (@(Get-ChildItem -LiteralPath $context.RestoreVerificationRoot -Force).Count -eq 0) {
            [IO.Directory]::Delete($context.RestoreVerificationRoot, $false)
        }
    }
    Exit-InstanceMaintenanceLock $maintenanceLock
}
