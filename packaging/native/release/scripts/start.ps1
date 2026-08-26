[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$context = Get-RuntimeContext
$postgresStarted = $false
$algorithmStarted = $false
$backendStarted = $false
$workingRoot = $null
$backendJava = $null
$backendJar = $null
$backendExpectedExecutables = @()

function Remove-StalePidRecord {
    param([Parameter(Mandatory = $true)][string]$Name)
    $path = Join-Path $context.Pids ($Name + ".json")
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return
    }
    $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -ne (Get-CimProcess ([int]$record.pid))) {
        throw "检测到仍在运行的 $Name（PID $($record.pid)）。请先执行 scripts\stop.ps1。"
    }
    Remove-Item -LiteralPath $path -Force
}

function Save-RunningProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string[]]$ExpectedExecutable,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [string[]]$Logs = @()
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    $process = $null
    while ($null -eq $process -and [DateTimeOffset]::UtcNow -lt $deadline) {
        $process = Get-CimProcess $ProcessId
        if ($null -eq $process) {
            Start-Sleep -Milliseconds 200
        }
    }
    if ($null -eq $process) {
        throw "$Name 启动后立即退出，PID：$ProcessId。请查看 logs 目录。"
    }
    $actual = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
    $expectedMatch = @($ExpectedExecutable | Where-Object {
            $actual.Equals([IO.Path]::GetFullPath($_), [StringComparison]::OrdinalIgnoreCase)
        }).Count -gt 0
    if (-not $expectedMatch) {
        throw "$Name 的实际可执行路径不属于当前发布包：$actual"
    }
    Write-PidRecord $context $Name $ProcessId $actual ([string]$process.CommandLine) $Logs $WorkingDirectory
}

function Initialize-PostgresData {
    if (Test-Path -LiteralPath (Join-Path $context.PgData "PG_VERSION") -PathType Leaf) {
        return
    }
    if (Test-Path -LiteralPath $context.PgData) {
        if (@(Get-ChildItem -LiteralPath $context.PgData -Force).Count -ne 0) {
            throw "PostgreSQL 数据目录非空但缺少 PG_VERSION，拒绝覆盖：$($context.PgData)"
        }
        Remove-Item -LiteralPath $context.PgData -Force
    }
    $passwordName = ".pg-password-" + [Guid]::NewGuid().ToString("N")
    $passwordFile = Join-Path (Split-Path $context.PgData -Parent) $passwordName
    $passwordArgument = Join-Path $workingRoot (Join-Path "data" $passwordName)
    $postgresDataArgument = Join-Path $workingRoot $context.PgDataArgument
    try {
        [IO.File]::WriteAllText($passwordFile, (Get-SecretValue $context.DatabasePasswordFile),
            (New-Object Text.UTF8Encoding($false)))
        Invoke-BundledCommand (Get-PostgresExecutable $context "initdb" $workingRoot) @(
            "-D", $postgresDataArgument,
            "-U", [string]$context.Config.database.user,
            "--encoding=UTF8", "--locale=C", "--auth-local=trust", "--auth-host=scram-sha-256",
            "--pwfile=$passwordArgument") $workingRoot | Out-Null
    } finally {
        if (Test-Path -LiteralPath $passwordFile) {
            Remove-Item -LiteralPath $passwordFile -Force
        }
    }
}

function Ensure-DemoDatabase {
    $oldPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
    [Environment]::SetEnvironmentVariable("PGPASSWORD", (Get-SecretValue $context.DatabasePasswordFile), "Process")
    try {
        $common = @("-h", "127.0.0.1", "-p", [string]$context.Config.ports.postgres,
            "-U", [string]$context.Config.database.user)
        $queryArguments = $common + @("-d", "postgres", "-Atc",
            ("SELECT 1 FROM pg_database WHERE datname = '" + [string]$context.Config.database.name + "'"))
        $queryOutput = Invoke-BundledCommand (Get-PostgresExecutable $context "psql" $workingRoot) `
            $queryArguments $workingRoot
        if ($queryOutput.Trim() -ne "1") {
            $createArguments = $common + @("-T", "template0", "-E", "UTF8",
                [string]$context.Config.database.name)
            Invoke-BundledCommand (Get-PostgresExecutable $context "createdb" $workingRoot) `
                $createArguments $workingRoot | Out-Null
        }
    } finally {
        [Environment]::SetEnvironmentVariable("PGPASSWORD", $oldPassword, "Process")
    }
}

function Wait-PostgresReady {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    $lastExitCode = $null
    $lastOutput = "尚未收到 PostgreSQL 就绪响应"
    $oldPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
    [Environment]::SetEnvironmentVariable("PGPASSWORD", (Get-SecretValue $context.DatabasePasswordFile), "Process")
    try {
        while ([DateTimeOffset]::UtcNow -lt $deadline) {
            $result = Invoke-BundledCommandResult (Get-PostgresExecutable $context "pg_isready" $workingRoot) @(
                "-h", "127.0.0.1", "-p", [string]$context.Config.ports.postgres,
                "-U", [string]$context.Config.database.user, "-d", "postgres", "-t", "2") $workingRoot
            if ($result.ExitCode -eq 0) {
                return
            }
            if ($result.ExitCode -eq 3) {
                throw "pg_isready 参数无效：$($result.Output)"
            }
            $lastExitCode = $result.ExitCode
            $lastOutput = $result.Output
            Start-Sleep -Milliseconds 500
        }
    } finally {
        [Environment]::SetEnvironmentVariable("PGPASSWORD", $oldPassword, "Process")
    }
    throw "PostgreSQL 就绪等待超时（最后退出码 $lastExitCode）：$lastOutput"
}

try {
    Initialize-ReleaseDirectories $context
    foreach ($name in @("postgresql", "algorithm", "backend")) {
        Remove-StalePidRecord $name
    }

    $powerShell = Join-Path $PSHOME "powershell.exe"
    $preflightOutput = Invoke-BundledCommand $powerShell @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "preflight.ps1"))
    if (-not [string]::IsNullOrWhiteSpace($preflightOutput)) {
        Write-Host $preflightOutput
    }
    Initialize-InstanceSecrets $context

    $workingRoot = Initialize-PostgresWorkingRoot $context
    $backendJava = Join-Path $workingRoot "runtime\jre\bin\java.exe"
    $backendJar = Join-Path $workingRoot "app\core-api.jar"
    $backendExpectedExecutables = @($backendJava, $context.Java) | Select-Object -Unique
    Initialize-PostgresData
    $stamp = [DateTime]::Now.ToString("yyyyMMdd-HHmmss-fff")
    $postgresOut = Join-Path $context.Logs ("postgresql-" + $stamp + ".out.log")
    $postgresError = Join-Path $context.Logs ("postgresql-" + $stamp + ".err.log")
    $postgresDataArgument = Join-Path $workingRoot $context.PgDataArgument
    $postgresArguments = '-D "' + $postgresDataArgument + '" -p ' +
        [string]$context.Config.ports.postgres + ' -h 127.0.0.1'
    $postgresProcess = Start-BundledProcess (Get-PostgresExecutable $context "postgres" $workingRoot) `
        $postgresArguments $workingRoot $postgresOut $postgresError
    $postgresStarted = $true
    Save-RunningProcess "postgresql" $postgresProcess.Id `
        (Get-PostgresExpectedExecutables $context $workingRoot "postgres") $workingRoot @(
            $postgresOut, $postgresError)

    Wait-PostgresReady
    Ensure-DemoDatabase

    $algorithmOut = Join-Path $context.Logs ("algorithm-" + $stamp + ".out.log")
    $algorithmError = Join-Path $context.Logs ("algorithm-" + $stamp + ".err.log")
    $algorithmProcess = Start-BundledProcess $context.Algorithm "" $context.Root $algorithmOut $algorithmError @{
        ALGORITHM_HOST = "127.0.0.1"
        ALGORITHM_PORT = [string]$context.Config.ports.algorithm
    }
    $algorithmStarted = $true
    Save-RunningProcess "algorithm" $algorithmProcess.Id $context.Algorithm $context.Root @(
        $algorithmOut, $algorithmError)
    $algorithmHealth = Wait-JsonHealth (
        "http://127.0.0.1:" + [string]$context.Config.ports.algorithm + "/health") {
            param($body)
            return $body.status -eq "UP" -and $body.service -eq "algorithm-service" -and
                $body.version -eq "0.2.0" -and $body.contract_version -eq "v2"
        } 90 "算法服务"

    $backendOut = Join-Path $context.Logs ("backend-" + $stamp + ".out.log")
    $backendError = Join-Path $context.Logs ("backend-" + $stamp + ".err.log")
    $databaseUrl = "jdbc:postgresql://127.0.0.1:" + [string]$context.Config.ports.postgres + "/" +
        [string]$context.Config.database.name
    $jarArgument = '-jar "' + $backendJar + '"'
    $backendProcess = Start-BundledProcess $backendJava $jarArgument $workingRoot $backendOut $backendError @{
        SERVER_PORT = [string]$context.Config.ports.backend
        SERVER_ADDRESS = "127.0.0.1"
        DB_URL = $databaseUrl
        DB_USERNAME = [string]$context.Config.database.user
        DB_PASSWORD = Get-SecretValue $context.DatabasePasswordFile
        APP_IDENTITY = [string]$context.Config.identity
        APP_DEPLOYMENT_MODE = [string]$context.Config.deployment_mode
        APP_BOOTSTRAP_ADMIN_USERNAME = [string]$context.Config.bootstrap_admin.username
        APP_BOOTSTRAP_ADMIN_PASSWORD_FILE = $context.BootstrapAdminPasswordFile
        SESSION_COOKIE_SECURE = "false"
        DEBUG = "false"
        TRACE = "false"
        LOGGING_LEVEL_ROOT = "INFO"
        LOGGING_LEVEL_ORG_SPRINGFRAMEWORK_WEB = "INFO"
        SPRING_MVC_LOG_REQUEST_DETAILS = "false"
        ALGORITHM_HEALTH_URL = "http://127.0.0.1:$($context.Config.ports.algorithm)/health"
        ALGORITHM_ANALYSIS_URL = "http://127.0.0.1:$($context.Config.ports.algorithm)/api/v2/analyze"
    }
    $backendStarted = $true
    Save-RunningProcess "backend" $backendProcess.Id $backendExpectedExecutables $workingRoot @(
        $backendOut, $backendError)
    $backendHealth = Wait-JsonHealth (
        "http://127.0.0.1:" + [string]$context.Config.ports.backend + "/api/v1/health") {
            param($body)
            return $body.status -eq "UP" -and $body.service -eq "alert-management-backend" -and
                $body.components.system.status -eq "UP" -and $body.components.database.status -eq "UP" -and
                $body.components.algorithm.status -eq "UP"
        } 120 "主程序"

    Write-Host "启动成功：http://127.0.0.1:8080"
    Write-Host "初始管理员：$($context.Config.bootstrap_admin.username)"
    Write-Host "首次登录临时密码文件：$($context.BootstrapAdminPasswordFile)"
    Write-Host "PostgreSQL、算法服务和主程序均为 UP，进程身份已写入 pids 目录。"
    exit 0
} catch {
    Write-Error ("启动失败：" + $_.Exception.Message + " 日志已保留在 " + $context.Logs)
    if ($backendStarted) {
        try { Stop-OwnedProcess $context "backend" $backendExpectedExecutables $backendJar } catch { Write-Warning $_.Exception.Message }
    }
    if ($algorithmStarted) {
        try { Stop-OwnedProcess $context "algorithm" $context.Algorithm } catch { Write-Warning $_.Exception.Message }
    }
    if ($postgresStarted) {
        try {
            $postgresDataArgument = Join-Path $workingRoot $context.PgDataArgument
            Invoke-BundledCommand (Get-PostgresExecutable $context "pg_ctl" $workingRoot) @(
                "-D", $postgresDataArgument, "-m", "fast", "-w", "stop") $workingRoot | Out-Null
            Remove-PidRecord $context "postgresql"
            $postgresStarted = $false
        } catch { Write-Warning $_.Exception.Message }
    }
    if (-not $postgresStarted -and $null -ne $workingRoot) {
        try { Remove-PostgresWorkingRoot $context } catch { Write-Warning $_.Exception.Message }
    }
    exit 1
}
