[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("Configure", "Remove", "Status")][string]$Action,
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')][string]$DailyAt = "02:00",
    [ValidateRange(1, 365)][int]$RetentionCount = 14
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Get-ExpectedTaskBinding {
    param($Context, $Identity, [int]$KeepCount)
    $powerShell = Join-Path $PSHOME "powershell.exe"
    $backupScript = Join-Path $PSScriptRoot "backup.ps1"
    $argumentPrefix = '-NoProfile -ExecutionPolicy Bypass -File "' +
        [IO.Path]::GetFullPath($backupScript) + '" -RetentionCount '
    return [PSCustomObject]@{
        Name = Get-InstanceBackupTaskName $Identity
        User = [Environment]::UserName
        Execute = [IO.Path]::GetFullPath($powerShell)
        Arguments = $argumentPrefix + $KeepCount + ' -Scheduled'
        ArgumentPattern = '^' + [regex]::Escape($argumentPrefix) + '\d{1,3} -Scheduled$'
        Description = "报警管理系统每日备份；instance_id=$($Identity.instance_id)；release_root=$($Context.Root)"
    }
}

function Assert-OwnedScheduledTask {
    param($Task, $Expected, [switch]$IgnoreArguments)
    if ($Task.TaskName -ne $Expected.Name -or $Task.Description -ne $Expected.Description -or
            $Task.Principal.UserId -ne $Expected.User -or @($Task.Actions).Count -ne 1 -or
            -not ([IO.Path]::GetFullPath([string]$Task.Actions[0].Execute)).Equals(
                $Expected.Execute, [StringComparison]::OrdinalIgnoreCase) -or
            (-not $IgnoreArguments -and [string]$Task.Actions[0].Arguments -ne $Expected.Arguments) -or
            ($IgnoreArguments -and [string]$Task.Actions[0].Arguments -notmatch $Expected.ArgumentPattern)) {
        throw "同名计划任务不属于当前发布实例，拒绝覆盖或删除：$($Expected.Name)"
    }
}

try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    $identity = Assert-InstanceIdentity $context
    $expected = Get-ExpectedTaskBinding $context $identity $RetentionCount
    $existing = Get-ScheduledTask -TaskName $expected.Name -ErrorAction SilentlyContinue

    if ($Action -eq "Configure") {
        if ($null -ne $existing) {
            Assert-OwnedScheduledTask $existing $expected -IgnoreArguments
        }
        $taskAction = New-ScheduledTaskAction -Execute $expected.Execute -Argument $expected.Arguments
        $trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
        $principal = New-ScheduledTaskPrincipal -UserId $expected.User -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2)
        $definition = New-ScheduledTask -Action $taskAction -Trigger $trigger -Principal $principal `
            -Settings $settings -Description $expected.Description
        Register-ScheduledTask -TaskName $expected.Name -InputObject $definition -Force | Out-Null
        $registered = Get-ScheduledTask -TaskName $expected.Name -ErrorAction Stop
        Assert-OwnedScheduledTask $registered $expected
        Write-Host "当前用户每日备份任务已配置：$($expected.Name)，时间 $DailyAt，保留 $RetentionCount 个恢复点。"
    } elseif ($Action -eq "Remove") {
        if ($null -eq $existing) {
            Write-Host "当前实例没有每日备份计划任务。"
        } else {
            Assert-OwnedScheduledTask $existing $expected -IgnoreArguments
            if ([string]$existing.State -eq "Running") {
                Stop-ScheduledTask -TaskName $expected.Name
                $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
                do {
                    Start-Sleep -Milliseconds 250
                    $existing = Get-ScheduledTask -TaskName $expected.Name -ErrorAction Stop
                } while ([string]$existing.State -eq "Running" -and
                    [DateTimeOffset]::UtcNow -lt $deadline)
                if ([string]$existing.State -eq "Running") {
                    throw "等待本实例每日备份计划任务停止超时，拒绝清理。"
                }
            }
            Unregister-ScheduledTask -TaskName $expected.Name -Confirm:$false
            Write-Host "当前实例每日备份计划任务已移除：$($expected.Name)"
        }
    } else {
        if ($null -eq $existing) {
            Write-Host "当前实例没有每日备份计划任务。"
        } else {
            Assert-OwnedScheduledTask $existing $expected -IgnoreArguments
            $info = Get-ScheduledTaskInfo -TaskName $expected.Name
            Write-Host "计划任务：$($expected.Name)"
            Write-Host "状态：$($existing.State)"
            Write-Host "下次运行：$($info.NextRunTime)"
            Write-Host "上次结果：$($info.LastTaskResult)"
            Write-Host "执行参数：$($existing.Actions[0].Arguments)"
        }
    }
    exit 0
} catch {
    Write-Error ("每日备份计划任务操作失败：" + $_.Exception.Message)
    exit 1
}
