[CmdletBinding()]
param(
    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    $identity = Assert-InstanceIdentity $context
    Initialize-ReleaseDirectories $context
    [void](Assert-OwnedMutableDirectory $context $context.Backups `
        (Join-ReleasePath $context.Root "backups"))

    $items = @()
    $knownDumps = New-Object 'System.Collections.Generic.HashSet[string]' `
        ([StringComparer]::OrdinalIgnoreCase)
    foreach ($metadataFile in @(Get-ChildItem -LiteralPath $context.Backups `
            -Filter "*.dump.meta.json" -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
        $dumpName = $metadataFile.Name.Substring(0, $metadataFile.Name.Length - ".meta.json".Length)
        [void]$knownDumps.Add($dumpName)
        try {
            $point = Get-RecoveryPoint $context $metadataFile.FullName -VerifyHash
            $items += [PSCustomObject]@{
                backup_file = [IO.Path]::GetFileName($point.BackupPath)
                origin_instance_id = [string]$point.Metadata.origin_instance_id
                origin_source_commit = [string]$point.Metadata.origin_source_commit
                created_at = $point.CreatedAt.ToString("o")
                size_bytes = [Int64]$point.SizeBytes
                hash_status = "OK"
                status = "OK"
                message = "恢复点完整"
            }
        } catch {
            $dumpPath = Join-Path $context.Backups $dumpName
            $size = if (Test-Path -LiteralPath $dumpPath -PathType Leaf) {
                [Int64](Get-Item -LiteralPath $dumpPath).Length
            } else { 0L }
            $items += [PSCustomObject]@{
                backup_file = $dumpName
                origin_instance_id = $null
                origin_source_commit = $null
                created_at = $null
                size_bytes = $size
                hash_status = "FAILED"
                status = "INVALID"
                message = $_.Exception.Message
            }
        }
    }
    foreach ($dumpFile in @(Get-ChildItem -LiteralPath $context.Backups `
            -Filter "*.dump" -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
        if (-not $knownDumps.Contains($dumpFile.Name)) {
            $isReparse = (($dumpFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
            $items += [PSCustomObject]@{
                backup_file = $dumpFile.Name
                origin_instance_id = $null
                origin_source_commit = $null
                created_at = $null
                size_bytes = if ($isReparse) { 0L } else { [Int64]$dumpFile.Length }
                hash_status = "UNKNOWN"
                status = if ($isReparse) { "INVALID" } else { "MISSING_METADATA" }
                message = if ($isReparse) { "恢复点文件不能是 Junction 或符号链接" } else { "缺少恢复点元数据" }
            }
        }
    }

    $successful = @($items | Where-Object { $_.status -eq "OK" } |
        Sort-Object created_at -Descending)
    $payload = [ordered]@{
        schema_version = 1
        instance_id = [string]$identity.instance_id
        checked_at = [DateTimeOffset]::UtcNow.ToString("o")
        recovery_point_count = $successful.Count
        total_dump_bytes = [Int64](($items | Measure-Object -Property size_bytes -Sum).Sum)
        latest_success_at = if ($successful.Count -gt 0) { $successful[0].created_at } else { $null }
        all_hashes_valid = @($items | Where-Object { $_.status -ne "OK" }).Count -eq 0
        recovery_points = $items
    }
    if ($Json) {
        Write-Output ($payload | ConvertTo-Json -Depth 6)
    } else {
        Write-Host "实例 ID：$($payload.instance_id)"
        Write-Host "有效恢复点：$($payload.recovery_point_count)"
        Write-Host "备份容量（字节）：$($payload.total_dump_bytes)"
        Write-Host "最近成功：$($payload.latest_success_at)"
        if ($items.Count -gt 0) {
            Write-Host ($items | Select-Object backup_file, created_at, size_bytes, hash_status, status |
                Format-Table -AutoSize | Out-String)
        } else {
            Write-Host "尚无恢复点。"
        }
    }
    if (-not $payload.all_hashes_valid) {
        Write-Error "存在缺失元数据、大小异常或 SHA-256 不匹配的恢复点。"
        exit 1
    }
    exit 0
} catch {
    Write-Error ("备份状态检查失败：" + $_.Exception.Message)
    exit 1
}
