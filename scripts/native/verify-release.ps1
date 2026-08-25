param(
    [string]$ArchivePath,
    [string]$OutputRoot,
    [switch]$AllowDirty
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$runtimeRoot = Join-Path $repositoryRoot ".runtime\native"
$artifactRoot = Join-Path $runtimeRoot "artifacts"
$nodeRoot = Join-Path $runtimeRoot "tools\node-22.22.1"
$playwrightBrowsers = Join-Path $runtimeRoot "tools\playwright"
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$originalPath = $env:PATH
$originalPathExt = $env:PATHEXT
$releaseRoots = New-Object System.Collections.ArrayList

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $runtimeRoot "verification"
} else {
    $OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function ConvertTo-NativeArgument {
    param([string]$Value)
    if ($null -eq $Value -or $Value.Length -eq 0) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
        } elseif ($character -eq '"') {
            [void]$builder.Append(('\' * ($backslashes * 2 + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
        } else {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * $backslashes))
                $backslashes = 0
            }
            [void]$builder.Append($character)
        }
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-NativeProcess {
    param(
        [string]$Executable,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $repositoryRoot,
        [switch]$WaitForProcessOnly
    )
    $captureRoot = Join-Path ([IO.Path]::GetTempPath()) ("alert-m6-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $captureRoot | Out-Null
    $stdoutPath = Join-Path $captureRoot "stdout.log"
    $stderrPath = Join-Path $captureRoot "stderr.log"
    try {
        $argumentLine = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join ' ')
        $parameters = @{
            FilePath = $Executable
            ArgumentList = $argumentLine
            WorkingDirectory = $WorkingDirectory
            PassThru = $true
            NoNewWindow = $true
            RedirectStandardOutput = $stdoutPath
            RedirectStandardError = $stderrPath
        }
        if (-not $WaitForProcessOnly) {
            $parameters.Wait = $true
        }
        $process = Start-Process @parameters
        if ($WaitForProcessOnly) {
            $process.WaitForExit()
        }
        $output = @()
        if (Test-Path -LiteralPath $stdoutPath) {
            $output += @(Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue)
        }
        if (Test-Path -LiteralPath $stderrPath) {
            $output += @(Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue)
        }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
    } finally {
        Remove-Item -LiteralPath $captureRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-CheckedCommand {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$FailureMessage
    )
    $result = Invoke-NativeProcess $Executable $Arguments
    foreach ($line in $result.Output) {
        Write-Host $line
    }
    if ($result.ExitCode -ne 0) {
        throw "$FailureMessage（退出码 $($result.ExitCode)）。"
    }
}

function Invoke-ReleaseScript {
    param(
        [string]$ReleaseRoot,
        [string]$Name,
        [string[]]$Arguments = @(),
        [switch]$ExpectFailure
    )
    $scriptPath = Join-Path $ReleaseRoot "scripts\$Name"
    Assert-True (Test-Path -LiteralPath $scriptPath -PathType Leaf) "发布脚本不存在：$scriptPath"
    $nativeArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scriptPath) + $Arguments
    $processOnly = $Name -eq "start.ps1"
    $result = Invoke-NativeProcess $powerShellExe $nativeArguments $ReleaseRoot -WaitForProcessOnly:$processOnly
    foreach ($line in $result.Output) {
        Write-Host $line
    }
    $exitCode = $result.ExitCode
    if ($processOnly -and $null -eq $exitCode) {
        $exitCode = if (($result.Output -join "`n") -match 'http://127\.0\.0\.1:8080') { 0 } else { 1 }
    }
    if ($ExpectFailure) {
        Assert-True ($exitCode -ne 0) "$Name 本应失败，却返回成功。"
    } else {
        Assert-True ($exitCode -eq 0) "$Name 执行失败，退出码：$exitCode。"
    }
    return ,$result.Output
}

function Get-ListeningOwners {
    param([int]$Port)
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Assert-PortsFree {
    foreach ($port in @(55432, 8001, 8080)) {
        $owners = @(Get-ListeningOwners $port)
        Assert-True ($owners.Count -eq 0) "固定端口 $port 已被占用，进程：$($owners -join ', ')。请停止占用后重试。"
    }
}

function Assert-PathUnderRoot {
    param([string]$Path, [string]$Root, [string]$Description)
    Assert-True (-not [string]::IsNullOrWhiteSpace($Path)) "$Description 为空。"
    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $isRoot = $fullPath.TrimEnd('\').Equals($fullRoot, [StringComparison]::OrdinalIgnoreCase)
    $isChild = $fullPath.StartsWith(($fullRoot + '\'), [StringComparison]::OrdinalIgnoreCase)
    Assert-True ($isRoot -or $isChild) "$Description 不在当前解压目录：$fullPath"
}

function Get-CurrentSourceCommit {
    $result = Invoke-NativeProcess "git.exe" @("-C", $repositoryRoot, "rev-parse", "HEAD")
    $commit = (($result.Output -join "`n").Trim())
    Assert-True ($result.ExitCode -eq 0 -and $commit -match '^[0-9a-f]{40}$') "无法读取当前 Git 提交。"
    return $commit
}

function Resolve-Archive {
    param([string]$RequestedArchive)
    if (-not [string]::IsNullOrWhiteSpace($RequestedArchive)) {
        $resolved = [IO.Path]::GetFullPath($RequestedArchive)
        Assert-True (Test-Path -LiteralPath $resolved -PathType Leaf) "指定 ZIP 不存在：$resolved"
        return $resolved
    }

    $sourceCommit = Get-CurrentSourceCommit
    $statusResult = Invoke-NativeProcess "git.exe" @("-C", $repositoryRoot, "status", "--porcelain", "--untracked-files=all")
    Assert-True ($statusResult.ExitCode -eq 0) "无法检查 Git 工作区状态。"
    if ($statusResult.Output.Count -gt 0 -and -not $AllowDirty) {
        throw "正式原生发布拒绝脏工作区；请提交改动后重试。"
    }

    $commitArtifactRoot = Join-Path $artifactRoot $sourceCommit
    $existing = @()
    if (Test-Path -LiteralPath $commitArtifactRoot -PathType Container) {
        $existing = @(Get-ChildItem -LiteralPath $commitArtifactRoot -Filter "*.zip" -File)
    }
    Assert-True ($existing.Count -le 1) "当前提交存在多个候选 ZIP，请显式传入 -ArchivePath。"
    if ($existing.Count -eq 1) {
        Write-Host "复用当前提交的已有发布包：$($existing[0].FullName)"
        return $existing[0].FullName
    }

    $buildScript = Join-Path $PSScriptRoot "build-release.ps1"
    Assert-True (Test-Path -LiteralPath $buildScript -PathType Leaf) "构建脚本不存在：$buildScript"
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $buildScript,
        "-OutputRoot", $artifactRoot
    )
    if ($AllowDirty) {
        $arguments += "-AllowDirty"
    }
    $buildResult = Invoke-NativeProcess $powerShellExe $arguments $repositoryRoot
    foreach ($line in $buildResult.Output) {
        Write-Host $line
    }
    Assert-True ($buildResult.ExitCode -eq 0) "Windows 原生发布包构建失败，退出码：$($buildResult.ExitCode)。"
    $marker = @($buildResult.Output | ForEach-Object { [string]$_ } | Where-Object { $_ -like "NATIVE_RELEASE_ZIP=*" } | Select-Object -Last 1)
    Assert-True ($marker.Count -eq 1) "构建脚本未输出唯一的 NATIVE_RELEASE_ZIP。"
    $resolvedArchive = $marker[0].Substring("NATIVE_RELEASE_ZIP=".Length).Trim()
    Assert-True (Test-Path -LiteralPath $resolvedArchive -PathType Leaf) "构建输出的 ZIP 不存在：$resolvedArchive"
    return [IO.Path]::GetFullPath($resolvedArchive)
}

function Assert-ArchiveHash {
    param([string]$Archive)
    $hashFile = "$Archive.sha256"
    Assert-True (Test-Path -LiteralPath $hashFile -PathType Leaf) "缺少 ZIP SHA-256 文件：$hashFile"
    $expectedLine = (Get-Content -LiteralPath $hashFile -Raw).Trim()
    Assert-True ($expectedLine -match '^([0-9A-Fa-f]{64})(\s+\*?.+)?$') "ZIP SHA-256 文件格式无效。"
    $expected = $Matches[1].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-True ($actual -eq $expected) "ZIP SHA-256 不匹配：期望 $expected，实际 $actual。"
    return $actual
}

function Expand-FreshRelease {
    param([string]$Archive, [string]$Destination)
    Assert-True (-not (Test-Path -LiteralPath $Destination)) "验收解压目录必须全新：$Destination"
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
    $manifests = @(Get-ChildItem -LiteralPath $Destination -Filter "release-manifest.json" -File -Recurse)
    Assert-True ($manifests.Count -eq 1) "ZIP 中必须且只能有一个 release-manifest.json。"
    return $manifests[0].Directory.FullName
}

function Assert-ReleaseManifest {
    param([string]$ReleaseRoot, [string]$ExpectedCommit)
    $manifestPath = Join-Path $ReleaseRoot "release-manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ($manifest.source_commit -eq $ExpectedCommit) "发布 manifest 源提交与当前提交不一致。"
    $entries = @($manifest.files)
    Assert-True ($entries.Count -gt 0) "发布 manifest 没有文件清单。"
    $seen = @{}
    foreach ($entry in $entries) {
        $relative = [string]$entry.path
        Assert-True ($relative -match '^[^\\]+(?:/[^\\]+)*$') "manifest 路径必须使用安全的相对 / 路径：$relative"
        Assert-True (-not $relative.Contains("../")) "manifest 路径不得越界：$relative"
        Assert-True (-not $seen.ContainsKey($relative)) "manifest 文件路径重复：$relative"
        $seen[$relative] = $true
        $fullPath = Join-Path $ReleaseRoot ($relative.Replace('/', '\'))
        Assert-PathUnderRoot $fullPath $ReleaseRoot "manifest 文件"
        Assert-True (Test-Path -LiteralPath $fullPath -PathType Leaf) "manifest 文件缺失：$relative"
        Assert-True ((Get-Item -LiteralPath $fullPath).Length -eq [long]$entry.size) "manifest 文件大小不符：$relative"
        $actualHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
        Assert-True ($actualHash -eq ([string]$entry.sha256).ToLowerInvariant()) "manifest 文件哈希不符：$relative"
    }
    $actualFiles = @(
        Get-ChildItem -LiteralPath $ReleaseRoot -File -Recurse |
            Where-Object { $_.FullName -ne $manifestPath }
    )
    Assert-True ($actualFiles.Count -eq $entries.Count) "发布目录文件数与 manifest 不一致。"
}

function Assert-HealthUp {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/health" -Method Get -TimeoutSec 15
    Assert-True ($health.status -eq "UP") "聚合健康顶层状态不是 UP。"
    foreach ($component in @("system", "database", "algorithm")) {
        Assert-True ($health.components.$component.status -eq "UP") "聚合健康组件 $component 不是 UP。"
    }
}

function Assert-JunctionTargetsRelease {
    param([string]$AliasRoot, [string]$ReleaseRoot)
    Assert-True ($AliasRoot -notmatch '[^\x00-\x7F]') "PostgreSQL 路径别名必须是纯 ASCII：$AliasRoot"
    $aliasItem = Get-Item -LiteralPath $AliasRoot -Force -ErrorAction Stop
    Assert-True ($aliasItem.LinkType -eq "Junction") "PostgreSQL 路径别名不是 Junction：$AliasRoot"
    $targets = @($aliasItem.Target)
    Assert-True ($targets.Count -eq 1) "PostgreSQL Junction 必须只有一个目标：$AliasRoot"
    $actualTarget = [IO.Path]::GetFullPath([string]$targets[0]).TrimEnd('\')
    $expectedTarget = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
    Assert-True ($actualTarget.Equals($expectedTarget, [StringComparison]::OrdinalIgnoreCase)) "PostgreSQL Junction 未指向当前发布根：$AliasRoot"
}

function Assert-PathOwnedByReleaseOrAlias {
    param([string]$Path, [string]$ReleaseRoot, [string]$AliasRoot, [string]$Description)
    $fullPath = [IO.Path]::GetFullPath($Path)
    $releasePrefix = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
    if ($fullPath.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        return
    }
    Assert-True (-not [string]::IsNullOrWhiteSpace($AliasRoot)) "$Description 不属于当前发布根。"
    Assert-JunctionTargetsRelease $AliasRoot $ReleaseRoot
    $aliasPrefix = [IO.Path]::GetFullPath($AliasRoot).TrimEnd('\') + '\'
    Assert-True ($fullPath.StartsWith($aliasPrefix, [StringComparison]::OrdinalIgnoreCase)) "$Description 不属于当前发布根或其受控别名：$fullPath"
}

function Assert-ProcessInventory {
    param([string]$ReleaseRoot)
    $definitions = @(
        @{ Name = "postgresql"; Port = 55432 },
        @{ Name = "algorithm"; Port = 8001 },
        @{ Name = "backend"; Port = 8080 }
    )
    $captured = @()
    $postgresAlias = $null
    foreach ($definition in $definitions) {
        $recordPath = Join-Path $ReleaseRoot "pids\$($definition.Name).json"
        Assert-True (Test-Path -LiteralPath $recordPath -PathType Leaf) "缺少 PID 记录：$recordPath"
        $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $processId = [int]$record.pid
        Assert-True ($processId -gt 0) "$($definition.Name) PID 无效。"
        Assert-True (-not [string]::IsNullOrWhiteSpace([string]$record.command_line)) "$($definition.Name) 未记录命令行。"
        Assert-True (-not [string]::IsNullOrWhiteSpace([string]$record.working_directory)) "$($definition.Name) 未记录工作目录。"
        Assert-True ([IO.Path]::GetFullPath([string]$record.release_root) -eq [IO.Path]::GetFullPath($ReleaseRoot)) "$($definition.Name) 记录的发布根不一致。"
        Assert-True (-not [string]::IsNullOrWhiteSpace([string]$record.started_at)) "$($definition.Name) 未记录启动时间。"
        Assert-True ($null -ne $record.logs) "$($definition.Name) 未记录日志路径。"

        if ($definition.Name -eq "postgresql") {
            $workingRoot = [IO.Path]::GetFullPath([string]$record.working_directory).TrimEnd('\')
            $releasePath = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
            if ($releasePath -notmatch '[^\x00-\x7F]') {
                Assert-True ($workingRoot.Equals($releasePath, [StringComparison]::OrdinalIgnoreCase)) "ASCII 发布目录下 PostgreSQL 工作目录必须是当前发布根。"
            } else {
                Assert-True (-not $workingRoot.Equals($releasePath, [StringComparison]::OrdinalIgnoreCase)) "非 ASCII 发布目录下 PostgreSQL 工作目录未使用受控 Junction。"
                Assert-JunctionTargetsRelease $workingRoot $ReleaseRoot
                $postgresAlias = $workingRoot
            }
            $postgresDataArgument = Join-Path $workingRoot "data\postgresql"
            Assert-True ($postgresDataArgument -notmatch '[^\x00-\x7F]') "PostgreSQL -D 路径不是纯 ASCII：$postgresDataArgument"
            Assert-True (([string]$record.command_line).IndexOf($postgresDataArgument, [StringComparison]::OrdinalIgnoreCase) -ge 0) "PostgreSQL 命令行未使用受控 ASCII -D 路径。"
            Assert-True (Test-Path -LiteralPath (Join-Path $ReleaseRoot "data\postgresql\PG_VERSION") -PathType Leaf) "当前发布根看不到 PostgreSQL 实际数据。"
            Assert-PathOwnedByReleaseOrAlias ([string]$record.executable_path) $ReleaseRoot $postgresAlias "postgresql 记录的可执行路径"
        } else {
            Assert-PathUnderRoot ([string]$record.executable_path) $ReleaseRoot "$($definition.Name) 记录的可执行路径"
            Assert-PathUnderRoot ([string]$record.working_directory) $ReleaseRoot "$($definition.Name) 记录的工作目录"
        }

        $actual = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        Assert-True ($null -ne $actual) "$($definition.Name) 记录的进程不存在：$processId"
        Assert-PathOwnedByReleaseOrAlias ([string]$actual.ExecutablePath) $ReleaseRoot $postgresAlias "$($definition.Name) 实际可执行路径"
        Assert-True ([IO.Path]::GetFullPath([string]$actual.ExecutablePath) -eq [IO.Path]::GetFullPath([string]$record.executable_path)) "$($definition.Name) 记录与实际可执行路径不一致。"
        Assert-True (([string]$actual.CommandLine).Trim() -eq ([string]$record.command_line).Trim()) "$($definition.Name) 记录与实际命令行不一致。"

        $owners = @(Get-ListeningOwners ([int]$definition.Port))
        Assert-True ($owners.Count -eq 1) "端口 $($definition.Port) 必须有唯一监听进程。"
        Assert-True ([int]$owners[0] -eq $processId) "端口 $($definition.Port) owner 与 $($definition.Name) PID 记录不一致。"
        $captured += $processId
    }
    return [pscustomobject]@{ Pids = $captured; PostgresAlias = $postgresAlias }
}

function Assert-ResetEmpty {
    $audit = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/audit-events?page=0&size=1" -Method Get -TimeoutSec 15
    Assert-True ([int]$audit.total -eq 0) "复位后审计业务状态不为空。"
}

function Invoke-E2e {
    param(
        [string]$E2eRoot,
        [string]$Npm,
        [string]$ReleaseRoot,
        [string]$Mode,
        [string]$Dataset,
        [int]$ExpectedTotal,
        [string]$Script,
        [string]$ResultRoot
    )
    $savedPath = $env:PATH
    try {
        $env:PATH = "$nodeRoot;$savedPath"
        $env:E2E_BASE_URL = "http://127.0.0.1:8080"
        $env:E2E_MODE = $Mode
        $env:E2E_DATASET = $Dataset
        $env:E2E_EXPECTED_TOTAL = [string]$ExpectedTotal
        $env:E2E_CYCLES = "1"
        $env:M5_OUTPUT_DIR = $ResultRoot
        $env:PLAYWRIGHT_BROWSERS_PATH = $playwrightBrowsers
        Invoke-CheckedCommand $Npm @("--prefix", $E2eRoot, "run", $Script) "Playwright $Script/$Mode 验收失败"
    } finally {
        $env:PATH = $savedPath
    }
}

function Invoke-BackupCheck {
    param([string]$ReleaseRoot)
    $before = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "backups") -Filter "*.dump" -File -ErrorAction SilentlyContinue)
    $beforeNames = @($before | ForEach-Object { $_.FullName })
    Invoke-ReleaseScript $ReleaseRoot "backup.ps1" | Out-Null
    $after = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "backups") -Filter "*.dump" -File)
    Assert-True ($after.Count -eq ($before.Count + 1)) "backup.ps1 未生成唯一的新备份。"
    $newBackup = @($after | Where-Object { $beforeNames -notcontains $_.FullName })
    Assert-True ($newBackup.Count -eq 1 -and $newBackup[0].Length -gt 0) "新备份不存在或为空。"
    $pgRestore = Join-Path $ReleaseRoot "runtime\postgresql\bin\pg_restore.exe"
    Invoke-CheckedCommand $pgRestore @("--list", $newBackup[0].FullName) "包内 pg_restore 无法读取备份"
    return $newBackup[0].FullName
}

function Invoke-ResetCheck {
    param([string]$ReleaseRoot)
    Invoke-ReleaseScript $ReleaseRoot "reset-demo.ps1" @("-Force") | Out-Null
    Assert-ResetEmpty
}

function Get-NormalizedRoundSummary {
    param([string]$ResultRoot)
    $smokePath = Join-Path $ResultRoot "smoke-normalized-summary.json"
    $demoPath = Join-Path $ResultRoot "demo-20000-metrics.json"
    Assert-True (Test-Path -LiteralPath $smokePath -PathType Leaf) "缺少 M5 Smoke 规范化摘要。"
    Assert-True (Test-Path -LiteralPath $demoPath -PathType Leaf) "缺少 M5 20k 指标。"
    $smoke = Get-Content -LiteralPath $smokePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $demo = Get-Content -LiteralPath $demoPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([int]$smoke.total -eq 300) "M5 Smoke 摘要总数不是 300。"
    Assert-True ([int]$demo.total -eq 20000) "M5 Demo 摘要总数不是 20000。"
    $summary = [ordered]@{
        smoke = $smoke
        demo = [ordered]@{
            total = [int]$demo.total
            report_formats = @($demo.reports | ForEach-Object { $_.format })
            reset_deleted_counts = $demo.reset_deleted_counts
        }
    }
    return $summary
}

function Assert-ServiceLogsClean {
    param([string]$ReleaseRoot)
    $logsRoot = Join-Path $ReleaseRoot "logs"
    $badLines = @()
    if (Test-Path -LiteralPath $logsRoot -PathType Container) {
        foreach ($log in @(Get-ChildItem -LiteralPath $logsRoot -File -Recurse)) {
            $matches = @(Select-String -LiteralPath $log.FullName `
                -Pattern '(?i)(^\d{4}[-/].*\s(?:ERROR|FATAL|PANIC)(?:\s|:)|^(?:ERROR|FATAL|PANIC)(?:\s|:)|^Traceback \(|^Unhandled exception|^Exception in thread)' `
                -ErrorAction SilentlyContinue)
            foreach ($match in $matches) {
                $badLines += "$($log.Name):$($match.LineNumber):$($match.Line)"
            }
        }
    }
    if ($badLines.Count -gt 0) {
        $lastIndex = [Math]::Min($badLines.Count - 1, 19)
        throw "服务日志包含错误：`n$($badLines[0..$lastIndex] -join "`n")"
    }
}

function Stop-ReleaseSafely {
    param([string]$ReleaseRoot)
    $stopScript = Join-Path $ReleaseRoot "scripts\stop.ps1"
    if (Test-Path -LiteralPath $stopScript -PathType Leaf) {
        try {
            $stopResult = Invoke-NativeProcess $powerShellExe @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $stopScript) $ReleaseRoot
            foreach ($line in $stopResult.Output) {
                Write-Host $line
            }
        } catch {
            Write-Warning "清理时 stop.ps1 失败：$($_.Exception.Message)"
        }
    }
    foreach ($recordPath in @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "pids") -Filter "*.json" -File -ErrorAction SilentlyContinue)) {
        try {
            $record = Get-Content -LiteralPath $recordPath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $actual = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue
            if ($null -ne $actual) {
                Assert-PathUnderRoot ([string]$actual.ExecutablePath) $ReleaseRoot "清理候选进程"
                Stop-Process -Id ([int]$record.pid) -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Warning "清理 PID 记录失败：$($recordPath.FullName)：$($_.Exception.Message)"
        }
    }
    foreach ($port in @(55432, 8001, 8080)) {
        foreach ($owner in @(Get-ListeningOwners $port)) {
            try {
                $actual = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
                if ($null -ne $actual) {
                    Assert-PathUnderRoot ([string]$actual.ExecutablePath) $ReleaseRoot "清理端口 $port 的进程"
                    Stop-Process -Id ([int]$owner) -Force -ErrorAction SilentlyContinue
                }
            } catch {
                Write-Warning "清理端口 $port 的发布进程失败：$($_.Exception.Message)"
            }
        }
    }
}

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    Assert-True ([Environment]::Is64BitOperatingSystem) "M6 仅支持 Windows x64。"
    Assert-True ($env:PROCESSOR_ARCHITECTURE -eq "AMD64") "M6 必须由 Windows x64 PowerShell 执行。"
    Assert-True (Test-Path -LiteralPath $powerShellExe -PathType Leaf) "找不到 Windows PowerShell 5.1。"

    $sourceCommit = Get-CurrentSourceCommit
    $archive = Resolve-Archive $ArchivePath
    $archiveHash = Assert-ArchiveHash $archive
    Write-Host "待验收 ZIP：$archive"
    Write-Host "ZIP SHA-256：$archiveHash"

    $runRoot = Join-Path $OutputRoot ((Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $runRoot | Out-Null
    $e2eRoot = Join-Path $runRoot "tools\e2e"
    New-Item -ItemType Directory -Path $e2eRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $repositoryRoot "tests\e2e\*.ts") -Destination $e2eRoot
    Copy-Item -Path (Join-Path $repositoryRoot "tests\e2e\package.json") -Destination $e2eRoot
    Copy-Item -Path (Join-Path $repositoryRoot "tests\e2e\package-lock.json") -Destination $e2eRoot

    $npm = Join-Path $nodeRoot "npm.cmd"
    Assert-True (Test-Path -LiteralPath $npm -PathType Leaf) "锁定 Node 验收工具不存在：$npm"
    $env:PLAYWRIGHT_BROWSERS_PATH = $playwrightBrowsers
    $env:PATH = "$nodeRoot;$originalPath"
    $env:PATHEXT = ".COM;.EXE;.BAT;.CMD;.CPL"
    Invoke-CheckedCommand $npm @("--prefix", $e2eRoot, "ci") "Playwright 依赖安装失败"
    Invoke-CheckedCommand $npm @("--prefix", $e2eRoot, "run", "install:chromium") "Chromium 安装失败"

    Assert-PortsFree
    $destinations = @(
        (Join-Path $runRoot "ascii-release"),
        (Join-Path $runRoot "中文 空格 发布目录")
    )
    $roundSummaries = @()

    for ($index = 0; $index -lt $destinations.Count; $index += 1) {
        $round = $index + 1
        Write-Host "开始第 $round 轮全新目录验收：$($destinations[$index])"
        $releaseRoot = Expand-FreshRelease $archive $destinations[$index]
        [void]$releaseRoots.Add($releaseRoot)
        Assert-ReleaseManifest $releaseRoot $sourceCommit

        $restrictedPath = @(
            (Join-Path $releaseRoot "runtime\jre\bin"),
            (Join-Path $releaseRoot "runtime\postgresql\bin"),
            (Join-Path $releaseRoot "app\algorithm"),
            (Join-Path $env:SystemRoot "System32"),
            $env:SystemRoot
        ) -join ';'
        $env:PATH = $restrictedPath

        if ($round -eq 1) {
            $listener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 55432)
            try {
                $listener.Start()
                Invoke-ReleaseScript $releaseRoot "preflight.ps1" -ExpectFailure | Out-Null
                $failedPreflightPids = @(Get-ChildItem -LiteralPath (Join-Path $releaseRoot "pids") -Filter "*.json" -File -ErrorAction SilentlyContinue)
                Assert-True ($failedPreflightPids.Count -eq 0) "失败预检不应创建 PID 记录。"
                $algorithmOwners = @(Get-ListeningOwners 8001)
                $backendOwners = @(Get-ListeningOwners 8080)
                Assert-True ($algorithmOwners.Count -eq 0 -and $backendOwners.Count -eq 0) "失败预检不应启动应用服务。"
            } finally {
                $listener.Stop()
            }
        }

        Invoke-ReleaseScript $releaseRoot "preflight.ps1" | Out-Null
        Invoke-ReleaseScript $releaseRoot "start.ps1" | Out-Null
        Assert-HealthUp
        $processInventory = Assert-ProcessInventory $releaseRoot
        $startedPids = @($processInventory.Pids)
        $postgresAlias = [string]$processInventory.PostgresAlias

        Invoke-ResetCheck $releaseRoot
        $roundResultRoot = Join-Path $runRoot "round-$round-results"
        $smokeDataset = Join-Path $releaseRoot "samples\smoke\synthetic_smoke_utf8.csv"
        $demoDataset = Join-Path $releaseRoot "samples\demo\synthetic_demo_20000.csv"
        Assert-True (Test-Path -LiteralPath $smokeDataset -PathType Leaf) "发布包缺少 300 行样例。"
        Assert-True (Test-Path -LiteralPath $demoDataset -PathType Leaf) "发布包缺少 20k 样例。"

        Invoke-E2e $e2eRoot $npm $releaseRoot "smoke" $smokeDataset 300 "test:smoke" $roundResultRoot
        $backupPath = Invoke-BackupCheck $releaseRoot
        Invoke-ResetCheck $releaseRoot
        Assert-True (Test-Path -LiteralPath $backupPath -PathType Leaf) "演示复位越界删除了备份。"

        Invoke-E2e $e2eRoot $npm $releaseRoot "demo" $demoDataset 20000 "test:smoke" $roundResultRoot
        Invoke-ResetCheck $releaseRoot
        Invoke-E2e $e2eRoot $npm $releaseRoot "smoke" $smokeDataset 300 "test:m5" $roundResultRoot
        Invoke-E2e $e2eRoot $npm $releaseRoot "demo" $demoDataset 20000 "test:m5" $roundResultRoot

        $roundSummary = Get-NormalizedRoundSummary $roundResultRoot
        $roundSummaryPath = Join-Path $runRoot "round-$round-summary.json"
        $roundSummary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $roundSummaryPath -Encoding UTF8

        Invoke-ReleaseScript $releaseRoot "stop.ps1" | Out-Null
        foreach ($port in @(55432, 8001, 8080)) {
            $remainingOwners = @(Get-ListeningOwners $port)
            Assert-True ($remainingOwners.Count -eq 0) "stop.ps1 后端口 $port 仍在监听。"
        }
        foreach ($processId in $startedPids) {
            Assert-True ($null -eq (Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue)) "stop.ps1 后进程 $processId 仍存在。"
        }
        if (-not [string]::IsNullOrWhiteSpace($postgresAlias)) {
            Assert-True (-not (Test-Path -LiteralPath $postgresAlias)) "stop.ps1 后 PostgreSQL 路径别名仍存在：$postgresAlias"
        }
        Assert-ServiceLogsClean $releaseRoot
        $roundSummaries += ,$roundSummary
        Write-Host "第 $round 轮验收通过。"
    }

    $firstSummary = $roundSummaries[0] | ConvertTo-Json -Depth 20 -Compress
    $secondSummary = $roundSummaries[1] | ConvertTo-Json -Depth 20 -Compress
    Assert-True ($firstSummary -eq $secondSummary) "两轮规范化业务摘要不一致。"
    [ordered]@{
        source_commit = $sourceCommit
        archive = $archive
        archive_sha256 = $archiveHash
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        rounds = 2
        normalized_summary = $roundSummaries[0]
    } | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath (Join-Path $runRoot "verification-summary.json") -Encoding UTF8
    Write-Host "M6 Windows 原生发布双目录验收通过。"
    Write-Host "NATIVE_VERIFICATION_ROOT=$runRoot"
} finally {
    $env:PATH = $originalPath
    $env:PATHEXT = $originalPathExt
    for ($index = $releaseRoots.Count - 1; $index -ge 0; $index -= 1) {
        Stop-ReleaseSafely ([string]$releaseRoots[$index])
    }
}
