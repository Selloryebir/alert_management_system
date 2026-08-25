[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$temporary = $null
try {
    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    Initialize-ReleaseDirectories $context
    $postgresRoot = Initialize-PostgresWorkingRoot $context
    $postgresDataArgument = Join-Path $postgresRoot $context.PgDataArgument
    Assert-OwnedProcess $context "postgresql" `
        (Get-PostgresExpectedExecutables $context $postgresRoot "postgres") `
        $postgresDataArgument | Out-Null

    Invoke-BundledCommand (Get-PostgresExecutable $context "pg_isready" $postgresRoot) @(
        "-h", "127.0.0.1", "-p", [string]$context.Config.ports.postgres,
        "-U", [string]$context.Config.database.user, "-d", [string]$context.Config.database.name,
        "-t", "10") $postgresRoot | Out-Null

    $fileName = "alert-management-" + [DateTime]::Now.ToString("yyyyMMdd-HHmmss-fff") + ".dump"
    $final = Join-Path $context.Backups $fileName
    if (Test-Path -LiteralPath $final) {
        throw "备份目标已存在，拒绝覆盖：$final"
    }
    $temporaryName = "." + $fileName + ".tmp-" + [Guid]::NewGuid().ToString("N")
    $temporary = Join-Path $context.Backups $temporaryName
    $temporaryArgument = Join-Path $postgresRoot (Join-Path "backups" $temporaryName)

    $oldPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
    [Environment]::SetEnvironmentVariable("PGPASSWORD", [string]$context.Config.database.password, "Process")
    try {
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

    [IO.File]::Move($temporary, $final)
    $temporary = $null
    Write-Host "备份完成：$final"
    exit 0
} catch {
    if ($null -ne $temporary -and (Test-Path -LiteralPath $temporary)) {
        Remove-Item -LiteralPath $temporary -Force
    }
    Write-Error ("备份失败：" + $_.Exception.Message)
    exit 1
}
