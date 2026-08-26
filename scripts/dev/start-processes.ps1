param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,
    [Parameter(Mandatory = $true)]
    [string]$WslRepositoryRoot,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapAdminPasswordFile,
    [Parameter(Mandatory = $true)]
    [int]$PostgresPort
)

$ErrorActionPreference = "Stop"
$runtimeDirectory = Join-Path $RepositoryRoot ".runtime"
$logDirectory = Join-Path $runtimeDirectory "logs"
$python = "$WslRepositoryRoot/.runtime/venv/bin/python"
$wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
$java = (Get-Command java.exe -ErrorAction Stop).Source
$jar = Join-Path $RepositoryRoot "src\backend\target\alert-management-backend-0.1.0.jar"

$algorithm = $null
$backend = $null
try {
    $algorithm = Start-Process -FilePath $wsl `
        -ArgumentList @(
            "--cd", "$WslRepositoryRoot/src/algorithm",
            "env", "ALGORITHM_HOST=127.0.0.1", "ALGORITHM_PORT=8001",
            $python, "-m", "algorithm_service"
        ) `
        -RedirectStandardOutput (Join-Path $logDirectory "algorithm.log") `
        -RedirectStandardError (Join-Path $logDirectory "algorithm-error.log") `
        -WindowStyle Hidden -PassThru

    $env:SERVER_PORT = "8080"
    $env:SERVER_ADDRESS = "127.0.0.1"
    $env:DB_URL = "jdbc:postgresql://127.0.0.1:$PostgresPort/alert_management"
    $env:DB_USERNAME = "alert_management"
    $env:DB_PASSWORD = "alert_management"
    $env:APP_DEPLOYMENT_MODE = "LOCAL_NATIVE"
    $env:APP_BOOTSTRAP_ADMIN_USERNAME = "admin"
    $env:APP_BOOTSTRAP_ADMIN_PASSWORD_FILE = $BootstrapAdminPasswordFile
    $env:SESSION_COOKIE_SECURE = "false"
    $env:ALGORITHM_HEALTH_URL = "http://127.0.0.1:8001/health"
    $env:DEBUG = "false"
    $backend = Start-Process -FilePath $java -ArgumentList @("-jar", $jar) `
        -WorkingDirectory $RepositoryRoot `
        -RedirectStandardOutput (Join-Path $logDirectory "backend.log") `
        -RedirectStandardError (Join-Path $logDirectory "backend-error.log") `
        -WindowStyle Hidden -PassThru

    Write-Output "$($algorithm.Id) $($backend.Id)"
}
catch {
    if ($null -ne $backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    }
    if ($null -ne $algorithm -and -not $algorithm.HasExited) {
        Stop-Process -Id $algorithm.Id -ErrorAction SilentlyContinue
    }
    throw
}
