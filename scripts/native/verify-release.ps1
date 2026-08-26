param(
    [string]$ArchivePath,
    [string]$OutputRoot,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$ReleaseVersion = "1.0.0-rc.1",
    [switch]$AllowDirty,
    [switch]$BusinessRelease
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$runtimeRoot = Join-Path $repositoryRoot ".runtime\native"
$artifactRoot = Join-Path $runtimeRoot "artifacts"
$nodeRoot = Join-Path $runtimeRoot "tools\node-22.22.1"
$playwrightBrowsers = Join-Path $runtimeRoot "tools\playwright"
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$originalPath = $env:PATH
$originalPathExt = $env:PATHEXT
$releaseRoots = New-Object System.Collections.ArrayList
$observedSecrets = New-Object System.Collections.Generic.List[string]
$credentialRoot = $null

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
            $output += @(Get-Content -LiteralPath $stdoutPath -Encoding UTF8 -ErrorAction SilentlyContinue)
        }
        if (Test-Path -LiteralPath $stderrPath) {
            $output += @(Get-Content -LiteralPath $stderrPath -Encoding UTF8 -ErrorAction SilentlyContinue)
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
    if ($processOnly) {
        $reportedReady = ($result.Output -join "`n") -match '127\.0\.0\.1:8080'
        if ($reportedReady) {
            $exitCode = 0
        } elseif ($null -eq $exitCode) {
            $exitCode = 1
        }
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
    $result = Invoke-NativeProcess "git.exe" @(
        "-c", "safe.directory=$repositoryRoot", "-C", $repositoryRoot, "rev-parse", "HEAD")
    $commit = (($result.Output -join "`n").Trim())
    Assert-True ($result.ExitCode -eq 0 -and $commit -match '^[0-9a-f]{40}$') "无法读取当前 Git 提交。"
    return $commit
}

function Resolve-Archive {
    param([string]$RequestedArchive)

    $sourceCommit = Get-CurrentSourceCommit
    $statusResult = Invoke-NativeProcess "git.exe" @(
        "-c", "safe.directory=$repositoryRoot", "-C", $repositoryRoot,
        "status", "--porcelain", "--untracked-files=all")
    Assert-True ($statusResult.ExitCode -eq 0) "无法检查 Git 工作区状态。"
    if ($statusResult.Output.Count -gt 0 -and -not $AllowDirty) {
        throw "正式原生发布拒绝脏工作区；请提交改动后重试。"
    }

    if (-not [string]::IsNullOrWhiteSpace($RequestedArchive)) {
        $resolved = [IO.Path]::GetFullPath($RequestedArchive)
        Assert-True (Test-Path -LiteralPath $resolved -PathType Leaf) "指定 ZIP 不存在：$resolved"
        return $resolved
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
        "-OutputRoot", $artifactRoot, "-ReleaseVersion", $ReleaseVersion
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
    param([string]$ReleaseRoot, [string]$ExpectedCommit, [string]$ExpectedReleaseVersion)
    $manifestPath = Join-Path $ReleaseRoot "release-manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ($manifest.source_commit -eq $ExpectedCommit) "发布 manifest 源提交与当前提交不一致。"
    Assert-True ($manifest.release_version -eq $ExpectedReleaseVersion) "发布 manifest 版本与验收目标不一致。"
    Assert-True ($manifest.PSObject.Properties.Name -contains "source_dirty") "发布 manifest 缺少 source_dirty。"
    Assert-True (-not [bool]$manifest.source_dirty -or $AllowDirty) "正式验收拒绝 source_dirty=true 的发布包。"
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
    foreach ($requiredManual in @(
            "manuals/business-user-manual.docx",
            "manuals/business-user-manual.pdf",
            "manuals/windows-deployment-operations.docx",
            "manuals/windows-deployment-operations.pdf")) {
        Assert-True ($seen.ContainsKey($requiredManual)) "发布包缺少正式使用手册：$requiredManual"
        $packagedManual = Join-Path $ReleaseRoot ($requiredManual.Replace('/', '\'))
        $repositoryManual = Join-Path $repositoryRoot ("deliverables\" + [IO.Path]::GetFileName($requiredManual))
        Assert-True (Test-Path -LiteralPath $repositoryManual -PathType Leaf) `
            "仓库缺少与发布包对照的正式使用手册：$repositoryManual"
        Assert-True ((Get-FileHash -LiteralPath $packagedManual -Algorithm SHA256).Hash -eq
            (Get-FileHash -LiteralPath $repositoryManual -Algorithm SHA256).Hash) `
            "发布包手册与当前已提交正式交付物不一致：$requiredManual"
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

function Initialize-AdminCredential {
    param([string]$ReleaseRoot)
    $passwordFile = Join-Path $ReleaseRoot "data\secrets\bootstrap-admin-password.txt"
    Assert-True (Test-Path -LiteralPath $passwordFile -PathType Leaf) "缺少初始管理员密码文件。"
    $currentPassword = [IO.File]::ReadAllText($passwordFile, [Text.Encoding]::UTF8).Trim()
    Assert-True (-not [string]::IsNullOrWhiteSpace($currentPassword)) "初始管理员密码为空。"
    $observedSecrets.Add($currentPassword)

    $csrf = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/csrf" -Method Get `
        -TimeoutSec 15 -UseBasicParsing -SessionVariable adminSession
    $headers = @{}
    $headers[[string]$csrf.header_name] = [string]$csrf.token
    $loginPayload = @{ username = "admin"; password = $currentPassword } | ConvertTo-Json -Compress
    $current = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/login" -Method Post `
        -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($loginPayload)) `
        -Headers $headers -WebSession $adminSession -TimeoutSec 15 -UseBasicParsing
    Assert-True ($current.username -eq "admin" -and $current.global_role -eq "SYSTEM_ADMIN") `
        "发布包初始管理员身份不正确。"

    if ([bool]$current.must_change_password) {
        $bytes = New-Object byte[] 24
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $generator.GetBytes($bytes)
        } finally {
            $generator.Dispose()
        }
        $newPassword = "Native-M11-" + [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        $observedSecrets.Add($newPassword)
        $changePayload = @{
            current_password = $currentPassword
            new_password = $newPassword
        } | ConvertTo-Json -Compress
        $changed = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/password" -Method Post `
            -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($changePayload)) `
            -Headers $headers -WebSession $adminSession -TimeoutSec 15 -UseBasicParsing
        Assert-True (-not [bool]$changed.must_change_password) "发布包首次改密后仍要求改密。"

        $verifyCsrf = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/csrf" -Method Get `
            -TimeoutSec 15 -UseBasicParsing -SessionVariable verifySession
        $verifyHeaders = @{}
        $verifyHeaders[[string]$verifyCsrf.header_name] = [string]$verifyCsrf.token
        $verifyPayload = @{ username = "admin"; password = $newPassword } | ConvertTo-Json -Compress
        $verified = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/login" -Method Post `
            -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($verifyPayload)) `
            -Headers $verifyHeaders -WebSession $verifySession -TimeoutSec 15 -UseBasicParsing
        Assert-True ($verified.username -eq "admin" -and -not [bool]$verified.must_change_password) `
            "发布包首次改密后无法重新登录。"
        [IO.File]::WriteAllText($passwordFile, $newPassword, (New-Object Text.UTF8Encoding($false)))
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $security = New-Object Security.AccessControl.FileSecurity
        $security.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow)
        [void]$security.AddAccessRule($rule)
        [IO.File]::SetAccessControl($passwordFile, $security)
    }
    return $passwordFile
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
    $releasePath = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
    $nonAsciiRelease = $releasePath -match '[^\x00-\x7F]'
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
        if ($definition.Name -eq "backend") {
            Assert-True (([string]$record.command_line).Contains("-Xms128m")) `
                "主程序命令行缺少最小堆限制。"
            Assert-True (([string]$record.command_line).Contains("-Xmx768m")) `
                "主程序命令行缺少最大堆限制。"
        }

        if ($definition.Name -eq "postgresql") {
            $workingRoot = [IO.Path]::GetFullPath([string]$record.working_directory).TrimEnd('\')
            if (-not $nonAsciiRelease) {
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
        } elseif ($definition.Name -eq "backend" -and $nonAsciiRelease) {
            Assert-True (-not [string]::IsNullOrWhiteSpace($postgresAlias)) "非 ASCII 发布目录下缺少 PostgreSQL 受控 Junction。"
            $backendWorkingRoot = [IO.Path]::GetFullPath([string]$record.working_directory).TrimEnd('\')
            Assert-True ($backendWorkingRoot.Equals($postgresAlias, [StringComparison]::OrdinalIgnoreCase)) "非 ASCII 发布目录下主程序与 PostgreSQL 未使用同一受控 Junction。"
            Assert-JunctionTargetsRelease $backendWorkingRoot $ReleaseRoot
            Assert-PathOwnedByReleaseOrAlias ([string]$record.executable_path) $ReleaseRoot $postgresAlias "backend 记录的可执行路径"
            $aliasJar = Join-Path $postgresAlias "app\core-api.jar"
            Assert-True (([string]$record.command_line).IndexOf($aliasJar, [StringComparison]::OrdinalIgnoreCase) -ge 0) "非 ASCII 发布目录下主程序命令行未使用 Junction 内的 JAR。"
        } else {
            Assert-PathUnderRoot ([string]$record.executable_path) $ReleaseRoot "$($definition.Name) 记录的可执行路径"
            Assert-PathUnderRoot ([string]$record.working_directory) $ReleaseRoot "$($definition.Name) 记录的工作目录"
        }

        $actual = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        Assert-True ($null -ne $actual) "$($definition.Name) 记录的进程不存在：$processId"
        if ($definition.Name -eq "algorithm") {
            Assert-PathUnderRoot ([string]$actual.ExecutablePath) $ReleaseRoot "algorithm 实际可执行路径"
        } else {
            Assert-PathOwnedByReleaseOrAlias ([string]$actual.ExecutablePath) $ReleaseRoot $postgresAlias "$($definition.Name) 实际可执行路径"
        }
        Assert-True ([IO.Path]::GetFullPath([string]$actual.ExecutablePath) -eq [IO.Path]::GetFullPath([string]$record.executable_path)) "$($definition.Name) 记录与实际可执行路径不一致。"
        Assert-True (([string]$actual.CommandLine).Trim() -eq ([string]$record.command_line).Trim()) "$($definition.Name) 记录与实际命令行不一致。"

        $owners = @(Get-ListeningOwners ([int]$definition.Port))
        Assert-True ($owners.Count -eq 1) "端口 $($definition.Port) 必须有唯一监听进程。"
        Assert-True ([int]$owners[0] -eq $processId) "端口 $($definition.Port) owner 与 $($definition.Name) PID 记录不一致。"
        $captured += $processId
    }
    return [pscustomobject]@{ Pids = $captured; PostgresAlias = $postgresAlias }
}

function New-ResetValidationSession {
    param([string]$PasswordFile)
    Assert-True (Test-Path -LiteralPath $PasswordFile -PathType Leaf) "复位核验缺少管理员密码文件。"
    $password = [IO.File]::ReadAllText($PasswordFile, [Text.Encoding]::UTF8).Trim()
    Assert-True (-not [string]::IsNullOrWhiteSpace($password)) "复位核验管理员密码为空。"
    $csrf = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/csrf" -Method Get `
        -TimeoutSec 15 -UseBasicParsing -SessionVariable resetAuditSession
    $headers = @{}
    $headers[[string]$csrf.header_name] = [string]$csrf.token
    $payload = @{ username = "admin"; password = $password } | ConvertTo-Json -Compress
    $current = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/auth/login" -Method Post `
        -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($payload)) `
        -Headers $headers -WebSession $resetAuditSession -TimeoutSec 15 -UseBasicParsing
    Assert-True ($current.global_role -eq "SYSTEM_ADMIN" -and -not [bool]$current.must_change_password) `
        "复位核验未取得可用的系统管理员会话。"
    return $resetAuditSession
}

function Assert-ResetEmpty {
    param([Microsoft.PowerShell.Commands.WebRequestSession]$WebSession)
    $audit = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/audit-events?page=0&size=1" `
        -Method Get -WebSession $WebSession -TimeoutSec 15 -UseBasicParsing
    Assert-True ([int]$audit.total -eq 1 -and @($audit.items).Count -eq 1) `
        "复位后应只保留一条复位审计。"
    Assert-True ($audit.items[0].event_type -eq "DEMO_RESET" -and $audit.items[0].result -eq "SUCCESS") `
        "复位后保留的唯一审计不是成功的 DEMO_RESET。"
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
        [string]$ResultRoot,
        [string]$NewPasswordFile = "",
        [string]$ProjectCode = "",
        [int]$ExpectedRecoveryPoints = 0
    )
    $savedPath = $env:PATH
    try {
        $env:PATH = "$nodeRoot;$savedPath"
        $env:E2E_ADMIN_USERNAME = "admin"
        $env:E2E_ADMIN_PASSWORD_FILE = Join-Path $ReleaseRoot "data\secrets\bootstrap-admin-password.txt"
        $env:E2E_BASE_URL = "http://127.0.0.1:8080"
        $env:E2E_MODE = $Mode
        $env:E2E_DATASET = $Dataset
        $env:E2E_EXPECTED_TOTAL = [string]$ExpectedTotal
        $env:E2E_CYCLES = "1"
        $env:M5_OUTPUT_DIR = $ResultRoot
        $env:PLAYWRIGHT_BROWSERS_PATH = $playwrightBrowsers
        if (-not [string]::IsNullOrWhiteSpace($NewPasswordFile)) {
            $env:E2E_ADMIN_NEW_PASSWORD_FILE = $NewPasswordFile
        }
        if (-not [string]::IsNullOrWhiteSpace($ProjectCode)) {
            $env:E2E_PROJECT_CODE = $ProjectCode
        }
        if ($ExpectedRecoveryPoints -gt 0) {
            $env:E2E_EXPECTED_RECOVERY_POINTS = [string]$ExpectedRecoveryPoints
        }
        Invoke-CheckedCommand $Npm @("--prefix", $E2eRoot, "run", $Script) "Playwright $Script/$Mode 验收失败"
    } finally {
        $env:PATH = $savedPath
        Remove-Item Env:E2E_ADMIN_USERNAME, Env:E2E_ADMIN_PASSWORD_FILE, `
            Env:E2E_ADMIN_NEW_PASSWORD_FILE, Env:E2E_PROJECT_CODE, `
            Env:E2E_EXPECTED_RECOVERY_POINTS -ErrorAction SilentlyContinue
    }
}

function New-ReleasePasswordFile {
    param([string]$Path)
    $bytes = New-Object byte[] 24
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    $password = "Release-RC-" + [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $observedSecrets.Add($password)
    [IO.File]::WriteAllText($Path, $password, (New-Object Text.UTF8Encoding($false)))
    return $password
}

function Set-AdminPasswordFile {
    param([string]$Path, [string]$Password)
    [IO.File]::WriteAllText($Path, $Password, (New-Object Text.UTF8Encoding($false)))
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow)
    [void]$security.AddAccessRule($rule)
    [IO.File]::SetAccessControl($Path, $security)
}

function Invoke-CrossInstanceRestoreCheck {
    param(
        [string]$ReleaseRoot,
        [string]$ExternalBackup,
        [string]$ExternalMetadata,
        [string]$ResultRoot
    )
    foreach ($source in @($ExternalBackup, $ExternalMetadata)) {
        Assert-True (Test-Path -LiteralPath $source -PathType Leaf) "跨实例恢复缺少外部备份文件：$source"
    }
    $destinationBackup = Join-Path $ReleaseRoot ("backups\" + [IO.Path]::GetFileName($ExternalBackup))
    $destinationMetadata = $destinationBackup + ".meta.json"
    Copy-Item -LiteralPath $ExternalBackup -Destination $destinationBackup
    Copy-Item -LiteralPath $ExternalMetadata -Destination $destinationMetadata
    Assert-True ((Get-FileHash -LiteralPath $destinationBackup -Algorithm SHA256).Hash -eq
        (Get-FileHash -LiteralPath $ExternalBackup -Algorithm SHA256).Hash) "外部备份复制到新实例后哈希变化。"
    Invoke-ReleaseScript $ReleaseRoot "backup-status.ps1" | Out-Null

    $before = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "logs") `
        -Filter "restore-verification-*.json" -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName })
    Invoke-ReleaseScript $ReleaseRoot "restore-verify.ps1" @("-BackupPath", $destinationBackup) | Out-Null
    $created = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "logs") `
        -Filter "restore-verification-*.json" -File |
        Where-Object { $before -notcontains $_.FullName })
    Assert-True ($created.Count -eq 1) "跨实例隔离恢复未生成唯一结果证据。"
    $payload = Get-Content -LiteralPath $created[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $identity = Get-Content -LiteralPath (Join-Path $ReleaseRoot "data\instance.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([bool]$payload.restored_to_isolated_instance) "跨实例恢复没有使用隔离实例。"
    Assert-True ([string]$payload.origin_instance_id -ne [string]$identity.instance_id) `
        "外部备份与新实例 ID 相同，未证明跨实例迁移。"
    Assert-True (@($payload.database_facts.PSObject.Properties).Count -ge 17) `
        "跨实例恢复结果缺少数据库对账事实。"
    $savedEvidence = Join-Path $ResultRoot "cross-instance-restore-verification.json"
    Copy-Item -LiteralPath $created[0].FullName -Destination $savedEvidence
    return [ordered]@{
        origin_instance_id = [string]$payload.origin_instance_id
        destination_instance_id = [string]$identity.instance_id
        fact_count = @($payload.database_facts.PSObject.Properties).Count
        evidence_sha256 = (Get-FileHash -LiteralPath $savedEvidence -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Invoke-BackupCheck {
    param([string]$ReleaseRoot, [string]$ReleaseAlias, [string]$ResultRoot)
    $before = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "backups") -Filter "*.dump" -File -ErrorAction SilentlyContinue)
    $beforeNames = @($before | ForEach-Object { $_.FullName })
    Invoke-ReleaseScript $ReleaseRoot "backup.ps1" | Out-Null
    $after = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "backups") -Filter "*.dump" -File)
    Assert-True ($after.Count -eq ($before.Count + 1)) "backup.ps1 未生成唯一的新备份。"
    $newBackup = @($after | Where-Object { $beforeNames -notcontains $_.FullName })
    Assert-True ($newBackup.Count -eq 1 -and $newBackup[0].Length -gt 0) "新备份不存在或为空。"
    $workingRoot = $ReleaseRoot
    if (-not [string]::IsNullOrWhiteSpace($ReleaseAlias)) {
        Assert-JunctionTargetsRelease $ReleaseAlias $ReleaseRoot
        $workingRoot = $ReleaseAlias
    }
    $pgRestore = Join-Path $workingRoot "runtime\postgresql\bin\pg_restore.exe"
    $backupArgument = Join-Path $workingRoot ("backups\" + $newBackup[0].Name)
    Assert-True (Test-Path -LiteralPath $backupArgument -PathType Leaf) "受控路径下看不到新备份。"
    Assert-True ((Get-Item -LiteralPath $backupArgument).Length -eq $newBackup[0].Length) "受控路径下的新备份大小不一致。"
    Invoke-CheckedCommand $pgRestore @("--list", $backupArgument) "包内 pg_restore 无法读取备份"
    $metadataPath = $newBackup[0].FullName + ".meta.json"
    Assert-True (Test-Path -LiteralPath $metadataPath -PathType Leaf) "backup.ps1 未生成恢复点元数据。"
    $metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $instance = Get-Content -LiteralPath (Join-Path $ReleaseRoot "data\instance.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath (Join-Path $ReleaseRoot "release-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $preexistingForeignNames = @()
    foreach ($existingBackup in $before) {
        $existingMetadataPath = $existingBackup.FullName + ".meta.json"
        Assert-True (Test-Path -LiteralPath $existingMetadataPath -PathType Leaf) `
            "既有恢复点缺少元数据：$existingMetadataPath"
        $existingMetadata = Get-Content -LiteralPath $existingMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert-True ([string]$existingMetadata.origin_instance_id -ne [string]$instance.instance_id) `
            "全新验收目录不应预先存在当前实例恢复点：$($existingBackup.Name)"
        $preexistingForeignNames += $existingBackup.Name
    }
    Assert-True ($metadata.origin_instance_id -eq $instance.instance_id -and
        $metadata.origin_source_commit -eq $manifest.source_commit -and
        [Int64]$metadata.size_bytes -eq [Int64]$newBackup[0].Length -and
        $metadata.sha256 -eq (Get-FileHash -LiteralPath $newBackup[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()) `
        "恢复点元数据未正确绑定实例、源提交、大小和 SHA-256。"
    Invoke-ReleaseScript $ReleaseRoot "backup-status.ps1" | Out-Null

    $negativeDump = Join-Path $ReleaseRoot "backups\negative-hash.dump"
    $negativeMetadata = $negativeDump + ".meta.json"
    try {
        Copy-Item -LiteralPath $newBackup[0].FullName -Destination $negativeDump
        $negativePayload = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $negativePayload.backup_file = [IO.Path]::GetFileName($negativeDump)
        $negativePayload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $negativeMetadata -Encoding UTF8
        $stream = [IO.File]::Open($negativeDump, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None)
        try {
            $first = $stream.ReadByte()
            $stream.Position = 0
            $stream.WriteByte(($first -bxor 0xFF))
        } finally {
            $stream.Dispose()
        }
        Invoke-ReleaseScript $ReleaseRoot "backup-status.ps1" -ExpectFailure | Out-Null
    } finally {
        Remove-Item -LiteralPath $negativeDump, $negativeMetadata -Force -ErrorAction SilentlyContinue
    }
    Invoke-ReleaseScript $ReleaseRoot "backup-status.ps1" | Out-Null
    $restoreBefore = @(
        Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "logs") `
            -Filter "restore-verification-*.json" -File -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName }
    )
    Invoke-ReleaseScript $ReleaseRoot "restore-verify.ps1" @(
        "-BackupPath", $newBackup[0].FullName, "-RequireCurrentMatch") | Out-Null
    $restoreEvidence = @(
        Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "logs") `
            -Filter "restore-verification-*.json" -File |
            Where-Object { $restoreBefore -notcontains $_.FullName }
    )
    Assert-True ($restoreEvidence.Count -eq 1) "隔离恢复未生成唯一结果证据。"
    $restorePayload = Get-Content -LiteralPath $restoreEvidence[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([bool]$restorePayload.restored_to_isolated_instance) "隔离恢复结果没有确认独立实例。"
    Assert-True (@($restorePayload.database_facts.PSObject.Properties).Count -ge 17) `
        "隔离恢复结果缺少迁移、业务表或序列对账事实。"
    $savedEvidence = Join-Path $ResultRoot "restore-verification.json"
    Copy-Item -LiteralPath $restoreEvidence[0].FullName -Destination $savedEvidence
    Invoke-ReleaseScript $ReleaseRoot "backup.ps1" @("-RetentionCount", "2") | Out-Null
    Invoke-ReleaseScript $ReleaseRoot "backup.ps1" @("-RetentionCount", "2") | Out-Null
    $retainedDumps = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "backups") -Filter "*.dump" -File)
    $retainedMetadata = @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "backups") `
        -Filter "*.dump.meta.json" -File)
    $retainedPoints = @($retainedDumps | ForEach-Object {
        $pointMetadataPath = $_.FullName + ".meta.json"
        Assert-True (Test-Path -LiteralPath $pointMetadataPath -PathType Leaf) `
            "保留后的恢复点缺少元数据：$pointMetadataPath"
        [PSCustomObject]@{
            Dump = $_
            Metadata = (Get-Content -LiteralPath $pointMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json)
        }
    })
    $currentRetained = @($retainedPoints | Where-Object {
            [string]$_.Metadata.origin_instance_id -eq [string]$instance.instance_id })
    $foreignRetained = @($retainedPoints | Where-Object {
            [string]$_.Metadata.origin_instance_id -ne [string]$instance.instance_id })
    Assert-True ($currentRetained.Count -eq 2 -and $retainedMetadata.Count -eq $retainedDumps.Count) `
        "RetentionCount=2 未精确保留两个当前实例恢复点。"
    Assert-True ($foreignRetained.Count -eq $preexistingForeignNames.Count) `
        "保留策略改变了外部迁移恢复点数量。"
    foreach ($foreignName in $preexistingForeignNames) {
        Assert-True (@($foreignRetained | Where-Object { $_.Dump.Name -eq $foreignName }).Count -eq 1) `
            "保留策略删除或替换了外部迁移恢复点：$foreignName"
    }
    Invoke-ReleaseScript $ReleaseRoot "backup-status.ps1" | Out-Null
    $latestCurrentPoint = @($currentRetained | Sort-Object { $_.Dump.LastWriteTimeUtc } -Descending)[0]
    $latestBackup = $latestCurrentPoint.Dump
    return [PSCustomObject]@{
        BackupPath = $latestBackup.FullName
        EvidencePath = $savedEvidence
        EvidenceSha256 = (Get-FileHash -LiteralPath $savedEvidence -Algorithm SHA256).Hash.ToLowerInvariant()
        FactCount = @($restorePayload.database_facts.PSObject.Properties).Count
    }
}

function Invoke-ResetCheck {
    param([string]$ReleaseRoot, [string]$PasswordFile)
    $validationSession = New-ResetValidationSession $PasswordFile
    Invoke-ReleaseScript $ReleaseRoot "reset-demo.ps1" @(
        "-Force", "-Username", "admin", "-PasswordFile", $PasswordFile) | Out-Null
    Assert-ResetEmpty $validationSession
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
    param([string]$ReleaseRoot, [string[]]$SecretValues)
    $logsRoot = Join-Path $ReleaseRoot "logs"
    $badLines = @()
    if (Test-Path -LiteralPath $logsRoot -PathType Container) {
        foreach ($log in @(Get-ChildItem -LiteralPath $logsRoot -File -Recurse)) {
            $content = Get-Content -LiteralPath $log.FullName -Raw -ErrorAction SilentlyContinue
            foreach ($secretValue in @($SecretValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
                if ($null -ne $content -and $content.IndexOf($secretValue, [StringComparison]::Ordinal) -ge 0) {
                    $badLines += "$($log.Name)：发现实际密码值"
                    break
                }
            }
            $matches = @(Select-String -LiteralPath $log.FullName `
                -Pattern '(?i)(^\d{4}[-/].*\s(?:ERROR|FATAL|PANIC)(?:\s|:)|^(?:ERROR|FATAL|PANIC)(?:\s|:)|^Traceback \(|^Unhandled exception|^Exception in thread)' `
                -ErrorAction SilentlyContinue)
            foreach ($match in $matches) {
                $expectedStartupProbe = $log.Name -like "postgresql-*.err.log" -and `
                    $match.Line -match '(?i)FATAL:\s+the database system is starting up\s*$'
                if ($expectedStartupProbe) {
                    continue
                }
                $badLines += "$($log.Name):$($match.LineNumber):$($match.Line)"
            }
            $unredactedPassword = @(Select-String -LiteralPath $log.FullName `
                -Pattern '(?i)(?:password|currentPassword|newPassword)=((?!\[REDACTED\])[^,\]\s]+)' `
                -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($unredactedPassword.Count -gt 0) {
                $badLines += "$($log.Name)：发现未脱敏的密码请求字段"
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
    $scheduleScript = Join-Path $ReleaseRoot "scripts\backup-schedule.ps1"
    if (Test-Path -LiteralPath $scheduleScript -PathType Leaf) {
        try {
            [void](Invoke-NativeProcess $powerShellExe @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scheduleScript,
                "-Action", "Remove") $ReleaseRoot)
        } catch {
            Write-Warning "清理时移除每日备份任务失败：$($_.Exception.Message)"
        }
    }
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
    $cleanupAlias = $null
    $postgresRecordPath = Join-Path $ReleaseRoot "pids\postgresql.json"
    if (Test-Path -LiteralPath $postgresRecordPath -PathType Leaf) {
        try {
            $postgresRecord = Get-Content -LiteralPath $postgresRecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
            Assert-True ([IO.Path]::GetFullPath([string]$postgresRecord.release_root).Equals(
                    [IO.Path]::GetFullPath($ReleaseRoot), [StringComparison]::OrdinalIgnoreCase)) "PostgreSQL 清理记录的发布根不一致。"
            $recordWorkingRoot = [IO.Path]::GetFullPath([string]$postgresRecord.working_directory).TrimEnd('\')
            $releasePath = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
            if (-not $recordWorkingRoot.Equals($releasePath, [StringComparison]::OrdinalIgnoreCase)) {
                Assert-JunctionTargetsRelease $recordWorkingRoot $ReleaseRoot
                Assert-PathOwnedByReleaseOrAlias ([string]$postgresRecord.executable_path) `
                    $ReleaseRoot $recordWorkingRoot "PostgreSQL 清理记录的可执行路径"
                $recordProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$postgresRecord.pid)" `
                    -ErrorAction SilentlyContinue
                if ($null -ne $recordProcess) {
                    Assert-PathOwnedByReleaseOrAlias ([string]$recordProcess.ExecutablePath) `
                        $ReleaseRoot $recordWorkingRoot "PostgreSQL 清理进程"
                    Assert-True ([IO.Path]::GetFullPath([string]$recordProcess.ExecutablePath).Equals(
                            [IO.Path]::GetFullPath([string]$postgresRecord.executable_path),
                            [StringComparison]::OrdinalIgnoreCase)) "PostgreSQL 清理记录与实际可执行路径不一致。"
                    Assert-True (([string]$recordProcess.CommandLine).Trim() -eq
                            ([string]$postgresRecord.command_line).Trim()) "PostgreSQL 清理记录与实际命令行不一致。"
                }
                $cleanupAlias = $recordWorkingRoot
            }
        } catch {
            Write-Warning "未能验证 PostgreSQL 清理 Junction：$($_.Exception.Message)"
        }
    }
    foreach ($recordPath in @(Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot "pids") -Filter "*.json" -File -ErrorAction SilentlyContinue)) {
        try {
            $record = Get-Content -LiteralPath $recordPath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            Assert-True ([IO.Path]::GetFullPath([string]$record.release_root).Equals(
                    [IO.Path]::GetFullPath($ReleaseRoot), [StringComparison]::OrdinalIgnoreCase)) "清理 PID 记录的发布根不一致。"
            $actual = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue
            if ($null -ne $actual) {
                if ($recordPath.BaseName -eq "algorithm") {
                    Assert-PathUnderRoot ([string]$actual.ExecutablePath) $ReleaseRoot "清理算法进程"
                } else {
                    Assert-PathOwnedByReleaseOrAlias ([string]$actual.ExecutablePath) $ReleaseRoot $cleanupAlias "清理候选进程"
                }
                Assert-True ([IO.Path]::GetFullPath([string]$actual.ExecutablePath).Equals(
                        [IO.Path]::GetFullPath([string]$record.executable_path),
                        [StringComparison]::OrdinalIgnoreCase)) "清理 PID 记录与实际可执行路径不一致。"
                Assert-True (([string]$actual.CommandLine).Trim() -eq ([string]$record.command_line).Trim()) "清理 PID 记录与实际命令行不一致。"
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
                    if ($port -eq 8001) {
                        Assert-PathUnderRoot ([string]$actual.ExecutablePath) $ReleaseRoot "清理端口 $port 的进程"
                    } else {
                        Assert-PathOwnedByReleaseOrAlias ([string]$actual.ExecutablePath) $ReleaseRoot $cleanupAlias "清理端口 $port 的进程"
                    }
                    Stop-Process -Id ([int]$owner) -Force -ErrorAction SilentlyContinue
                }
            } catch {
                Write-Warning "清理端口 $port 的发布进程失败：$($_.Exception.Message)"
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($cleanupAlias) -and (Test-Path -LiteralPath $cleanupAlias)) {
        try {
            Assert-JunctionTargetsRelease $cleanupAlias $ReleaseRoot
            [IO.Directory]::Delete($cleanupAlias, $false)
            Assert-True (-not (Test-Path -LiteralPath $cleanupAlias)) "清理后受控 Junction 仍存在：$cleanupAlias"
        } catch {
            Write-Warning "清理受控 Junction 失败：$($_.Exception.Message)"
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
    $credentialRoot = Join-Path $runRoot ".credentials"
    $externalBackupRoot = Join-Path $runRoot "external-backup"
    if ($BusinessRelease) {
        New-Item -ItemType Directory -Path $credentialRoot, $externalBackupRoot | Out-Null
    }
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
    $maintenanceSummaries = @()
    $externalBackup = $null
    $externalMetadata = $null
    $crossInstanceSummary = $null

    for ($index = 0; $index -lt $destinations.Count; $index += 1) {
        $round = $index + 1
        Write-Host "开始第 $round 轮全新目录验收：$($destinations[$index])"
        $releaseRoot = Expand-FreshRelease $archive $destinations[$index]
        [void]$releaseRoots.Add($releaseRoot)
        Assert-ReleaseManifest $releaseRoot $sourceCommit $ReleaseVersion

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
        $instanceIdentity = Get-Content -LiteralPath (Join-Path $releaseRoot "data\instance.json") `
            -Raw -Encoding UTF8 | ConvertFrom-Json
        $backupTaskName = "AlertManagementSystem-Backup-" + [string]$instanceIdentity.instance_id
        Invoke-ReleaseScript $releaseRoot "backup-schedule.ps1" @(
            "-Action", "Configure", "-DailyAt", "23:59", "-RetentionCount", "2") | Out-Null
        Invoke-ReleaseScript $releaseRoot "backup-schedule.ps1" @("-Action", "Status") | Out-Null
        $roundResultRoot = Join-Path $runRoot "round-$round-results"
        New-Item -ItemType Directory -Path $roundResultRoot | Out-Null
        $smokeDataset = Join-Path $releaseRoot "samples\smoke\synthetic_smoke_utf8.csv"
        $demoDataset = Join-Path $releaseRoot "samples\demo\synthetic_demo_20000.csv"
        Assert-True (Test-Path -LiteralPath $smokeDataset -PathType Leaf) "发布包缺少 300 行样例。"
        Assert-True (Test-Path -LiteralPath $demoDataset -PathType Leaf) "发布包缺少 20k 样例。"

        if ($BusinessRelease) {
            $adminPasswordFile = Join-Path $releaseRoot "data\secrets\bootstrap-admin-password.txt"
            Assert-True (Test-Path -LiteralPath $adminPasswordFile -PathType Leaf) "缺少初始管理员密码文件。"
            $bootstrapPassword = [IO.File]::ReadAllText($adminPasswordFile, [Text.Encoding]::UTF8).Trim()
            Assert-True (-not [string]::IsNullOrWhiteSpace($bootstrapPassword)) "初始管理员密码为空。"
            $observedSecrets.Add($bootstrapPassword)
            $newPasswordFile = Join-Path $credentialRoot "round-$round-new-password.txt"
            $newPassword = New-ReleasePasswordFile $newPasswordFile
            $projectCode = "RELEASE-R$round-$($sourceCommit.Substring(0, 8).ToUpperInvariant())"
            Invoke-E2e $e2eRoot $npm $releaseRoot "release-bootstrap" $smokeDataset 300 `
                "test:release-bootstrap" $roundResultRoot $newPasswordFile $projectCode
            Set-AdminPasswordFile $adminPasswordFile $newPassword
            Remove-Item -LiteralPath $newPasswordFile -Force
        } else {
            $adminPasswordFile = Initialize-AdminCredential $releaseRoot
        }
        $processInventory = Assert-ProcessInventory $releaseRoot
        $startedPids = @($processInventory.Pids)
        $postgresAlias = [string]$processInventory.PostgresAlias

        Invoke-ResetCheck $releaseRoot $adminPasswordFile
        if ($BusinessRelease -and $round -eq 2) {
            $crossInstanceSummary = Invoke-CrossInstanceRestoreCheck $releaseRoot `
                $externalBackup $externalMetadata $roundResultRoot
        }

        Invoke-E2e $e2eRoot $npm $releaseRoot "smoke" $smokeDataset 300 "test:smoke" $roundResultRoot
        $backupCheck = Invoke-BackupCheck $releaseRoot $postgresAlias $roundResultRoot
        $backupPath = [string]$backupCheck.BackupPath
        if ($BusinessRelease) {
            Invoke-E2e $e2eRoot $npm $releaseRoot "release-backup" $smokeDataset 300 `
                "test:release-backup-status" $roundResultRoot "" "" 2
            if ($round -eq 1) {
                $externalBackup = Join-Path $externalBackupRoot ([IO.Path]::GetFileName($backupPath))
                $externalMetadata = $externalBackup + ".meta.json"
                Copy-Item -LiteralPath $backupPath -Destination $externalBackup
                Copy-Item -LiteralPath ($backupPath + ".meta.json") -Destination $externalMetadata
                Assert-True ((Get-FileHash -LiteralPath $externalBackup -Algorithm SHA256).Hash -eq
                    (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash) `
                    "导出到发布目录外的备份哈希变化。"
            }
        }
        Invoke-ResetCheck $releaseRoot $adminPasswordFile
        Assert-True (Test-Path -LiteralPath $backupPath -PathType Leaf) "演示复位越界删除了备份。"
        Invoke-ReleaseScript $releaseRoot "restore-verify.ps1" @(
            "-BackupPath", $backupPath) | Out-Null

        Invoke-E2e $e2eRoot $npm $releaseRoot "demo" $demoDataset 20000 "test:smoke" $roundResultRoot
        Invoke-ResetCheck $releaseRoot $adminPasswordFile
        Invoke-E2e $e2eRoot $npm $releaseRoot "smoke" $smokeDataset 300 "test:m5" $roundResultRoot
        Invoke-E2e $e2eRoot $npm $releaseRoot "demo" $demoDataset 20000 "test:m5" $roundResultRoot

        $roundSummary = Get-NormalizedRoundSummary $roundResultRoot
        $roundSummary["restore_verification"] = [ordered]@{
            fact_count = [int]$backupCheck.FactCount
        }
        $maintenanceSummaries += ,[ordered]@{
            round = $round
            restore_evidence = [IO.Path]::GetFullPath([string]$backupCheck.EvidencePath).Substring(
                [IO.Path]::GetFullPath($runRoot).TrimEnd('\').Length + 1)
            restore_evidence_sha256 = [string]$backupCheck.EvidenceSha256
            restore_fact_count = [int]$backupCheck.FactCount
            cleanup = "PASS"
            backups_preserved = $true
        }
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
        Assert-ServiceLogsClean $releaseRoot $observedSecrets.ToArray()
        Invoke-ReleaseScript $releaseRoot "cleanup-instance.ps1" @("-Force") | Out-Null
        Assert-True ($null -eq (Get-ScheduledTask -TaskName $backupTaskName -ErrorAction SilentlyContinue)) `
            "cleanup-instance.ps1 未移除当前实例计划任务。"
        foreach ($mutableDirectory in @("data", "logs", "pids")) {
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $releaseRoot $mutableDirectory))) `
                "cleanup-instance.ps1 未清理当前实例的 $mutableDirectory 目录。"
        }
        Assert-True (@(Get-ChildItem -LiteralPath $releaseRoot `
                    -Filter ".instance-maintenance-*.lock" -File -ErrorAction SilentlyContinue).Count -eq 0) `
            "cleanup-instance.ps1 未清理当前实例维护锁文件。"
        Assert-True (Test-Path -LiteralPath $backupPath -PathType Leaf) `
            "cleanup-instance.ps1 默认不应删除备份。"
        if ($BusinessRelease -and $round -eq 1) {
            $firstDestination = [IO.Path]::GetFullPath($destinations[0]).TrimEnd('\')
            $runPrefix = [IO.Path]::GetFullPath($runRoot).TrimEnd('\') + '\'
            Assert-True ($firstDestination.StartsWith($runPrefix, [StringComparison]::OrdinalIgnoreCase)) `
                "拒绝清理验收运行根以外的第一实例目录。"
            Assert-True (Test-Path -LiteralPath $externalBackup -PathType Leaf) `
                "清理第一实例前缺少外部备份。"
            Remove-Item -LiteralPath $firstDestination -Recurse -Force
            Assert-True (-not (Test-Path -LiteralPath $firstDestination)) "第一实例目录清理失败。"
            Assert-True (Test-Path -LiteralPath $externalBackup -PathType Leaf) `
                "清理第一实例越界删除了外部备份。"
        }
        $roundSummaries += ,$roundSummary
        Write-Host "第 $round 轮验收通过。"
    }

    $firstSummary = $roundSummaries[0] | ConvertTo-Json -Depth 20 -Compress
    $secondSummary = $roundSummaries[1] | ConvertTo-Json -Depth 20 -Compress
    Assert-True ($firstSummary -eq $secondSummary) "两轮规范化业务摘要不一致。"
    if ($BusinessRelease) {
        Assert-True ($null -ne $crossInstanceSummary) "发布候选缺少跨实例恢复结果。"
    }
    $verificationIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $verificationPrincipal = New-Object Security.Principal.WindowsPrincipal($verificationIdentity)
    [ordered]@{
        source_commit = $sourceCommit
        archive = $archive
        archive_sha256 = $archiveHash
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        windows_identity = $verificationIdentity.Name
        windows_is_administrator = $verificationPrincipal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
        rounds = 2
        business_release = [bool]$BusinessRelease
        normalized_summary = $roundSummaries[0]
        instance_maintenance = $maintenanceSummaries
        cross_instance_restore = $crossInstanceSummary
    } | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath (Join-Path $runRoot "verification-summary.json") -Encoding UTF8
    if ($BusinessRelease) {
        Write-Host "M14 Windows 原生发布业务终验预检查通过。"
    } else {
        Write-Host "M6 Windows 原生发布双目录验收通过。"
    }
    Write-Host "NATIVE_VERIFICATION_ROOT=$runRoot"
} finally {
    $env:PATH = $originalPath
    $env:PATHEXT = $originalPathExt
    for ($index = $releaseRoots.Count - 1; $index -ge 0; $index -= 1) {
        Stop-ReleaseSafely ([string]$releaseRoots[$index])
    }
    if ($null -ne $credentialRoot -and (Test-Path -LiteralPath $credentialRoot)) {
        Remove-Item -LiteralPath $credentialRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
