[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$RemoveBackups
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Assert-NoReparsePoints {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return
    }
    foreach ($item in @(Get-ChildItem -LiteralPath $Path -Force -Recurse)) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "受控目录内存在 Junction 或符号链接，拒绝递归清理：$($item.FullName)"
        }
    }
}

$maintenanceLock = $null
try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    $identity = Assert-InstanceIdentity $context

    if (-not $Force) {
        Write-Host "即将停止并清理当前发布实例的数据、日志和 PID 记录。"
        Write-Host "发布目录：$($context.Root)"
        Write-Host "实例 ID：$($identity.instance_id)"
        if ($RemoveBackups) {
            Write-Warning "本次还会删除当前实例 backups 目录中的全部备份。"
        } else {
            Write-Host "backups 目录将保留。"
        }
        $confirmation = Read-Host "请输入完整实例 ID 继续"
        if ($confirmation -ne [string]$identity.instance_id) {
            throw "实例 ID 不匹配，已取消清理。"
        }
    }

    $powerShell = Join-Path $PSHOME "powershell.exe"
    Invoke-BundledCommand $powerShell @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $PSScriptRoot "backup-schedule.ps1"), "-Action", "Remove") $context.Root | Out-Null
    $maintenanceLock = Enter-InstanceMaintenanceLock $context
    Invoke-BundledCommand $powerShell @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $PSScriptRoot "stop.ps1")) $context.Root | Out-Null
    $pidRecords = @(Get-ChildItem -LiteralPath $context.Pids -Filter "*.json" -File -ErrorAction SilentlyContinue)
    if ($pidRecords.Count -ne 0) {
        throw "停止后仍存在 PID 身份记录，拒绝清理：$($pidRecords.Name -join ', ')"
    }

    $targets = @(
        [PSCustomObject]@{ Path = Join-ReleasePath $context.Root "data"; Expected = Join-ReleasePath $context.Root "data" },
        [PSCustomObject]@{ Path = $context.Logs; Expected = Join-ReleasePath $context.Root "logs" },
        [PSCustomObject]@{ Path = $context.Pids; Expected = Join-ReleasePath $context.Root "pids" }
    )
    if ($RemoveBackups) {
        $targets += [PSCustomObject]@{
            Path = $context.Backups
            Expected = Join-ReleasePath $context.Root "backups"
        }
    }
    foreach ($target in $targets) {
        Assert-NoReleasePathReparseBoundary $context $target.Path
        $safePath = Assert-OwnedMutableDirectory $context $target.Path $target.Expected
        Assert-NoReparsePoints $safePath
    }
    foreach ($target in $targets) {
        if (Test-Path -LiteralPath $target.Path) {
            Remove-Item -LiteralPath $target.Path -Recurse -Force
        }
    }

    Write-Host "当前发布实例已清理。程序文件未删除，可手工删除当前解压目录。"
    if (-not $RemoveBackups) {
        Write-Host "备份已保留：$($context.Backups)"
    }
    exit 0
} catch {
    Write-Error ("实例清理失败：" + $_.Exception.Message)
    exit 1
} finally {
    Exit-InstanceMaintenanceLock $maintenanceLock
}
