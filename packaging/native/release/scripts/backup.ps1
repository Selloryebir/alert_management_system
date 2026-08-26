[CmdletBinding()]
param(
    [ValidateRange(1, 365)][int]$RetentionCount = 14,
    [switch]$Scheduled
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$context = $null
$temporary = $null
$temporaryMetadata = $null
$final = $null
$finalMetadata = $null
$publishedDump = $false
$publishedMetadata = $false
$maintenanceLock = $null

function Write-ScheduledBackupLog {
    param([string]$Status, [string]$Message)
    if (-not $Scheduled) {
        return
    }
    $logRoot = if ($null -ne $context) { $context.Logs } else {
        [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\logs"))
    }
    if ($null -ne $context) {
        Assert-NoReleasePathReparseBoundary $context $logRoot
    }
    if (-not (Test-Path -LiteralPath $logRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $logRoot | Out-Null
    }
    $line = [DateTimeOffset]::UtcNow.ToString("o") + " status=" + $Status + " " +
        $Message.Replace("`r", " ").Replace("`n", " ")
    Add-Content -LiteralPath (Join-Path $logRoot "scheduled-backup.log") -Value $line -Encoding UTF8
}

try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    [void](Assert-InstanceIdentity $context)
    Initialize-ReleaseDirectories $context
    Assert-NoReleasePathReparseBoundary $context $context.Backups
    $maintenanceLock = Enter-InstanceMaintenanceLock $context
    $postgresRoot = Initialize-PostgresWorkingRoot $context
    $postgresDataArgument = Join-Path $postgresRoot $context.PgDataArgument
    Assert-OwnedProcess $context "postgresql" `
        (Get-PostgresExpectedExecutables $context $postgresRoot "postgres") `
        $postgresDataArgument | Out-Null

    $fileName = "alert-management-" + [DateTime]::Now.ToString("yyyyMMdd-HHmmss-fff") + ".dump"
    $final = Join-Path $context.Backups $fileName
    $finalMetadata = $final + ".meta.json"
    if ((Test-Path -LiteralPath $final) -or (Test-Path -LiteralPath $finalMetadata)) {
        throw "备份目标已存在，拒绝覆盖：$final"
    }
    $temporaryName = "." + $fileName + ".tmp-" + [Guid]::NewGuid().ToString("N")
    $temporary = Join-Path $context.Backups $temporaryName
    $temporaryMetadata = $temporary + ".meta.json"
    $temporaryArgument = Join-Path $postgresRoot (Join-Path "backups" $temporaryName)

    $oldPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
    [Environment]::SetEnvironmentVariable("PGPASSWORD", (Get-SecretValue $context.DatabasePasswordFile), "Process")
    try {
        Invoke-BundledCommand (Get-PostgresExecutable $context "pg_isready" $postgresRoot) @(
            "-h", "127.0.0.1", "-p", [string]$context.Config.ports.postgres,
            "-U", [string]$context.Config.database.user, "-d", [string]$context.Config.database.name,
            "-t", "10") $postgresRoot | Out-Null
        Invoke-BundledCommand (Get-PostgresExecutable $context "pg_dump" $postgresRoot) @(
            "-h", "127.0.0.1", "-p", [string]$context.Config.ports.postgres,
            "-U", [string]$context.Config.database.user, "-d", [string]$context.Config.database.name,
            "--format=custom", "--file=$temporaryArgument") $postgresRoot | Out-Null
        $listing = Invoke-BundledCommand (Get-PostgresExecutable $context "pg_restore" $postgresRoot) @(
            "--list", $temporaryArgument) $postgresRoot
        if ([string]::IsNullOrWhiteSpace($listing) -or (Get-Item -LiteralPath $temporary).Length -le 0) {
            throw "pg_restore 未能验证临时备份。"
        }
    } finally {
        [Environment]::SetEnvironmentVariable("PGPASSWORD", $oldPassword, "Process")
    }

    $dumpItem = Get-Item -LiteralPath $temporary
    $identity = Assert-InstanceIdentity $context
    $manifest = Get-ReleaseManifest $context
    $metadata = [ordered]@{
        schema_version = 1
        product = "alert-management-system-recovery-point"
        origin_instance_id = [string]$identity.instance_id
        origin_source_commit = [string]$manifest.source_commit
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
        database = [string]$context.Config.database.name
        backup_file = $fileName
        size_bytes = [Int64]$dumpItem.Length
        sha256 = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        pg_restore_list_verified = $true
    }
    [IO.File]::WriteAllText($temporaryMetadata, (($metadata | ConvertTo-Json -Depth 4) + "`n"),
        (New-Object Text.UTF8Encoding($false)))
    [IO.File]::Move($temporary, $final)
    $publishedDump = $true
    $temporary = $null
    [IO.File]::Move($temporaryMetadata, $finalMetadata)
    $publishedMetadata = $true
    $temporaryMetadata = $null
    Invoke-RecoveryPointRetention $context $RetentionCount
    Write-ScheduledBackupLog "SUCCESS" ("backup=" + $fileName + " retention=" + $RetentionCount)
    Write-Host "备份完成：$final"
    Write-Host "恢复点元数据：$finalMetadata"
    exit 0
} catch {
    if ($null -ne $temporary -and (Test-Path -LiteralPath $temporary)) {
        Remove-Item -LiteralPath $temporary -Force
    }
    if ($null -ne $temporaryMetadata -and (Test-Path -LiteralPath $temporaryMetadata)) {
        Remove-Item -LiteralPath $temporaryMetadata -Force
    }
    if ($publishedDump -and -not $publishedMetadata -and $null -ne $final -and
            (Test-Path -LiteralPath $final)) {
        Remove-Item -LiteralPath $final -Force
    }
    Write-ScheduledBackupLog "FAILED" $_.Exception.Message
    Write-Error ("备份失败：" + $_.Exception.Message)
    exit 1
} finally {
    Exit-InstanceMaintenanceLock $maintenanceLock
}
