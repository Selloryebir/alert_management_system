param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,
    [Parameter(Mandatory = $true)]
    [string]$BootstrapAdminPasswordFile,
    [Parameter(Mandatory = $true)]
    [string]$PidFile,
    [Parameter(Mandatory = $true)]
    [int]$PostgresPort,
    [switch]$Build,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$scriptPath = $MyInvocation.MyCommand.Path
$env:Path = "$env:SystemRoot\System32\WindowsPowerShell\v1.0;$env:SystemRoot\System32;$env:Path"
$env:PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

if (-not $Detached) {
    if ($Build) {
        $maven = Start-Process -FilePath $env:ComSpec `
            -ArgumentList @("/d", "/s", "/c", "mvnw.cmd -f src\backend\pom.xml package -DskipTests") `
            -WorkingDirectory $RepositoryRoot -NoNewWindow -Wait -PassThru
        if ($maven.ExitCode -ne 0) {
            throw "Java 后端构建失败，退出码：$($maven.ExitCode)"
        }
    }
    $powershell = Join-Path $PSHOME "powershell.exe"
    $commandLine = '"' + $powershell + '" -NoProfile -ExecutionPolicy Bypass -File "' +
        $scriptPath + '" -RepositoryRoot "' + $RepositoryRoot +
        '" -BootstrapAdminPasswordFile "' + $BootstrapAdminPasswordFile +
        '" -PidFile "' + $PidFile + '" -PostgresPort ' + [string]$PostgresPort + ' -Detached'
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = $commandLine }
    if ($created.ReturnValue -ne 0) {
        throw "Windows 后端启动进程创建失败，返回码：$($created.ReturnValue)"
    }
    exit 0
}

$logDirectory = Join-Path $RepositoryRoot ".runtime\logs"
$java = (Get-Command java.exe -ErrorAction Stop).Source
$jar = Join-Path $RepositoryRoot "src\backend\target\alert-management-backend-0.1.0.jar"

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
[IO.File]::WriteAllText($PidFile, [string]$backend.Id, [Text.Encoding]::ASCII)
