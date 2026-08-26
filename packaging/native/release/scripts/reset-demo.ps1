[CmdletBinding()]
param(
    [switch]$Force,
    [string]$Username = "admin",
    [string]$PasswordFile
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    $workingRoot = Get-PostgresAliasPath $context
    if (-not (Normalize-DirectoryPath $workingRoot).Equals((Normalize-DirectoryPath $context.Root),
            [StringComparison]::OrdinalIgnoreCase)) {
        Assert-PostgresAliasTarget $context $workingRoot
    }
    $backendJava = Join-Path $workingRoot "runtime\jre\bin\java.exe"
    $backendJar = Join-Path $workingRoot "app\core-api.jar"
    $backendExpectedExecutables = @($backendJava, $context.Java) | Select-Object -Unique
    Assert-OwnedProcess $context "backend" $backendExpectedExecutables $backendJar | Out-Null

    if (-not $Force) {
        $answer = Read-Host "此操作将清空当前发布包的演示业务数据，但保留数据库、备份和样例。请输入 RESET_DEMO 继续"
        if ($answer -ne "RESET_DEMO") {
            throw "确认文本不匹配，未执行演示复位。"
        }
    }

    if ($Username -notmatch '^[a-z0-9._-]{3,50}$') {
        throw "管理员账号格式非法。"
    }
    if ([string]::IsNullOrWhiteSpace($PasswordFile)) {
        $credential = Get-Credential -UserName $Username -Message "请输入报警管理系统管理员凭据"
        if ($null -eq $credential) {
            throw "未提供管理员凭据。"
        }
        $Username = $credential.UserName.Trim().ToLowerInvariant()
        $password = $credential.GetNetworkCredential().Password
    } else {
        $resolvedPasswordFile = [IO.Path]::GetFullPath($PasswordFile)
        if (-not (Test-Path -LiteralPath $resolvedPasswordFile -PathType Leaf)) {
            throw "管理员密码文件不存在或不是普通文件。"
        }
        if ((Get-Item -LiteralPath $resolvedPasswordFile).Length -gt 1024) {
            throw "管理员密码文件异常过大。"
        }
        $password = [IO.File]::ReadAllText($resolvedPasswordFile, [Text.Encoding]::UTF8).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($password)) {
        throw "管理员密码不能为空。"
    }

    $baseUri = "http://127.0.0.1:" + [string]$context.Config.ports.backend
    $csrf = Invoke-RestMethod -Uri ($baseUri + "/api/v1/auth/csrf") -Method Get -TimeoutSec 15 `
        -UseBasicParsing -SessionVariable webSession
    if ([string]::IsNullOrWhiteSpace([string]$csrf.header_name) -or
            [string]::IsNullOrWhiteSpace([string]$csrf.token)) {
        throw "CSRF 初始化响应不完整。"
    }
    $headers = @{}
    $headers[[string]$csrf.header_name] = [string]$csrf.token
    $loginPayload = @{ username = $Username; password = $password } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Uri ($baseUri + "/api/v1/auth/login") -Method Post `
        -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($loginPayload)) `
        -Headers $headers -WebSession $webSession -TimeoutSec 15 -UseBasicParsing
    if ($login.global_role -ne "SYSTEM_ADMIN") {
        throw "演示复位只允许系统管理员执行。"
    }
    if ([bool]$login.must_change_password) {
        throw "当前仍是首次临时密码，请先在网页完成改密后再复位。"
    }

    $payload = @{ confirmation = "RESET_DEMO" } | ConvertTo-Json -Compress
    $resetParameters = @{
        Uri = $baseUri + "/api/v1/demo/reset"
        Method = "Post"
        ContentType = "application/json"
        Body = [Text.Encoding]::UTF8.GetBytes($payload)
        TimeoutSec = 60
        UseBasicParsing = $true
        Headers = $headers
        WebSession = $webSession
    }
    $result = Invoke-RestMethod @resetParameters
    if ($result.business_state -ne "EMPTY" -or $null -eq $result.deleted_counts) {
        throw "复位 API 未返回 EMPTY 业务状态。"
    }
    $imports = Invoke-RestMethod -Uri ($baseUri + "/api/v1/imports?project_id=00000000-0000-0000-0000-000000000001&limit=1") -Method Get -TimeoutSec 15 `
        -UseBasicParsing -WebSession $webSession
    if (@($imports).Count -ne 0) {
        throw "复位后仍存在导入批次，业务状态并非空。"
    }

    Write-Host "演示数据复位完成：EMPTY"
    exit 0
} catch {
    Write-Error ("演示复位失败：" + $_.Exception.Message)
    exit 1
}
