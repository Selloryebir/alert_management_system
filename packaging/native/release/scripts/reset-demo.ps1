[CmdletBinding()]
param([switch]$Force)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    Assert-OwnedProcess $context "backend" $context.Java $context.BackendJar | Out-Null

    if (-not $Force) {
        $answer = Read-Host "此操作将清空当前发布包的演示业务数据，但保留数据库、备份和样例。请输入 RESET_DEMO 继续"
        if ($answer -ne "RESET_DEMO") {
            throw "确认文本不匹配，未执行演示复位。"
        }
    }

    $operator = [Environment]::UserName
    if ([string]::IsNullOrWhiteSpace($operator)) {
        $operator = "native-demo-operator"
    }
    $payload = @{ operator = $operator; confirmation = "RESET_DEMO" } | ConvertTo-Json -Compress
    $baseUri = "http://127.0.0.1:" + [string]$context.Config.ports.backend
    $resetParameters = @{
        Uri = $baseUri + "/api/v1/demo/reset"
        Method = "Post"
        ContentType = "application/json"
        Body = [Text.Encoding]::UTF8.GetBytes($payload)
        TimeoutSec = 60
        UseBasicParsing = $true
    }
    $result = Invoke-RestMethod @resetParameters
    if ($result.business_state -ne "EMPTY" -or $null -eq $result.deleted_counts) {
        throw "复位 API 未返回 EMPTY 业务状态。"
    }
    $imports = Invoke-RestMethod -Uri ($baseUri + "/api/v1/imports?limit=1") -Method Get -TimeoutSec 15 `
        -UseBasicParsing
    if (@($imports).Count -ne 0) {
        throw "复位后仍存在导入批次，业务状态并非空。"
    }

    Write-Host "演示数据复位完成：EMPTY"
    exit 0
} catch {
    Write-Error ("演示复位失败：" + $_.Exception.Message)
    exit 1
}
