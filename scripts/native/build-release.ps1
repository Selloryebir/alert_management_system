[CmdletBinding()]
param(
    [string]$OutputRoot = "",
    [string]$ReleaseVersion = "0.2.0-m9",
    [switch]$AllowDirty
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$nativeRuntime = Join-Path $repositoryRoot ".runtime\native"
$cacheRoot = Join-Path $nativeRuntime "cache"
$toolsRoot = Join-Path $nativeRuntime "tools"
$stagingRoot = Join-Path $nativeRuntime ("staging\" + [Guid]::NewGuid().ToString("N"))
$releaseRoot = Join-Path $stagingRoot "alert-management-system-windows-x64"
$lockPath = Join-Path $repositoryRoot "packaging\native\runtime-lock.json"
$templateRoot = Join-Path $repositoryRoot "packaging\native\release"
$algorithmSpec = Join-Path $repositoryRoot "packaging\native\algorithm-service.spec"
$pyinstallerLock = Join-Path $repositoryRoot "packaging\native\pyinstaller.lock"

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $nativeRuntime "artifacts"
}
elseif (-not [IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)

function ConvertTo-NativeArgumentLine {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        if ($_ -match '[\s"]' -or $_.Length -eq 0) {
            '"' + $_.Replace('"', '\"') + '"'
        }
        else {
            $_
        }
    }) -join " ")
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $process = Start-Process -FilePath $FilePath `
        -ArgumentList (ConvertTo-NativeArgumentLine -Arguments $Arguments) `
        -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$FailureMessage（退出码 $($process.ExitCode)）。"
    }
}

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    $captureBase = Join-Path ([IO.Path]::GetTempPath()) ("ams-native-" + [Guid]::NewGuid().ToString("N"))
    $standardOutput = "$captureBase.out"
    $standardError = "$captureBase.err"
    try {
        $process = Start-Process -FilePath $FilePath `
            -ArgumentList (ConvertTo-NativeArgumentLine -Arguments $Arguments) `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $standardOutput -RedirectStandardError $standardError
        $output = [string](Get-Content -LiteralPath $standardOutput -Raw -ErrorAction SilentlyContinue)
        $errorOutput = [string](Get-Content -LiteralPath $standardError -Raw -ErrorAction SilentlyContinue)
        if ($process.ExitCode -ne 0) {
            throw "$FailureMessage（退出码 $($process.ExitCode)）：$($errorOutput.Trim())"
        }
        return $output
    }
    finally {
        foreach ($captureFile in @($standardOutput, $standardError)) {
            if (Test-Path -LiteralPath $captureFile) {
                Remove-Item -LiteralPath $captureFile -Force
            }
        }
    }
}

function Assert-FileHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "下载文件 SHA-256 不匹配：$Path；预期 $Expected，实际 $actual。"
    }
}

function Get-LockedArchive {
    param([Parameter(Mandatory = $true)]$Artifact)

    $archivePath = Join-Path $cacheRoot ([string]$Artifact.archive_name)
    if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
        Assert-FileHash -Path $archivePath -Expected ([string]$Artifact.sha256)
        Write-Host "复用已校验缓存：$($Artifact.archive_name)"
        return $archivePath
    }

    $partialPath = "$archivePath.partial.$([Guid]::NewGuid().ToString('N'))"
    try {
        $lastError = $null
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Write-Host "下载 $($Artifact.distribution) $($Artifact.version)（第 $attempt 次）..."
                Invoke-WebRequest -UseBasicParsing -Uri ([string]$Artifact.url) -OutFile $partialPath
                Assert-FileHash -Path $partialPath -Expected ([string]$Artifact.sha256)
                Move-Item -LiteralPath $partialPath -Destination $archivePath
                return $archivePath
            }
            catch {
                $lastError = $_
                if (Test-Path -LiteralPath $partialPath) {
                    Remove-Item -LiteralPath $partialPath -Force
                }
                if ($attempt -lt 3) {
                    Start-Sleep -Seconds 2
                }
            }
        }
        throw $lastError
    }
    finally {
        if (Test-Path -LiteralPath $partialPath) {
            Remove-Item -LiteralPath $partialPath -Force
        }
    }
}

function Find-ArtifactRoot {
    param(
        [Parameter(Mandatory = $true)][string]$ExtractionRoot,
        [Parameter(Mandatory = $true)][string]$Marker
    )

    $windowsMarker = $Marker.Replace("/", "\")
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath (Join-Path $ExtractionRoot $windowsMarker) -PathType Leaf) {
        $candidates.Add($ExtractionRoot)
    }
    Get-ChildItem -LiteralPath $ExtractionRoot -Directory -Recurse | ForEach-Object {
        if (Test-Path -LiteralPath (Join-Path $_.FullName $windowsMarker) -PathType Leaf) {
            $candidates.Add($_.FullName)
        }
    }
    if ($candidates.Count -eq 0) {
        throw "归档缺少根标记：$Marker。"
    }
    $rankedCandidates = @($candidates | ForEach-Object {
        $relative = $_.Substring($ExtractionRoot.Length).TrimStart("\")
        $depth = if ($relative.Length -eq 0) { 0 } else { @($relative.Split("\")).Count }
        [pscustomobject]@{ Path = $_; Depth = $depth }
    } | Sort-Object Depth, Path)
    $minimumDepth = $rankedCandidates[0].Depth
    $shallowest = @($rankedCandidates | Where-Object { $_.Depth -eq $minimumDepth })
    if ($shallowest.Count -ne 1) {
        throw "归档根目录不唯一：标记 $Marker，最浅候选数 $($shallowest.Count)。"
    }
    return $shallowest[0].Path
}

function Expand-LockedArchive {
    param(
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)]$Artifact,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    New-Item -ItemType Directory -Path $Destination | Out-Null
    if ([string]$Artifact.archive_type -eq "zip") {
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $Destination
    }
    elseif ([string]$Artifact.archive_type -eq "tar.gz") {
        $tar = (Get-Command tar.exe -ErrorAction Stop).Source
        Invoke-Checked -FilePath $tar -Arguments @("-xzf", $ArchivePath, "-C", $Destination) `
            -FailureMessage "无法解压 $($Artifact.archive_name)"
    }
    else {
        throw "不支持的归档类型：$($Artifact.archive_type)。"
    }
    return Find-ArtifactRoot -ExtractionRoot $Destination -Marker ([string]$Artifact.root_marker)
}

function Get-BuildTool {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Artifact
    )

    $toolDirectory = Join-Path $toolsRoot ("$Name-$($Artifact.version)")
    $stampPath = Join-Path $toolDirectory ".source.sha256"
    $markerPath = Join-Path $toolDirectory ([string]$Artifact.root_marker).Replace("/", "\")
    if ((Test-Path -LiteralPath $markerPath -PathType Leaf) -and
            (Test-Path -LiteralPath $stampPath -PathType Leaf) -and
            ((Get-Content -LiteralPath $stampPath -Raw).Trim() -eq [string]$Artifact.sha256)) {
        return $toolDirectory
    }
    if (Test-Path -LiteralPath $toolDirectory) {
        throw "构建工具目录存在但与锁不一致，请人工检查后移除：$toolDirectory"
    }

    $archive = Get-LockedArchive -Artifact $Artifact
    $extractDirectory = Join-Path $toolsRoot (".extract-" + [Guid]::NewGuid().ToString("N"))
    try {
        $sourceRoot = Expand-LockedArchive -ArchivePath $archive -Artifact $Artifact `
            -Destination $extractDirectory
        Move-Item -LiteralPath $sourceRoot -Destination $toolDirectory
        [IO.File]::WriteAllText($stampPath, ([string]$Artifact.sha256 + "`n"), (New-Object Text.UTF8Encoding($false)))
    }
    finally {
        if (Test-Path -LiteralPath $extractDirectory) {
            Remove-Item -LiteralPath $extractDirectory -Recurse -Force
        }
    }
    return $toolDirectory
}

if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "原生发布构建只支持 Windows x64。"
}
foreach ($required in @($lockPath, $algorithmSpec, $pyinstallerLock)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "缺少构建输入：$required"
    }
}
if (-not (Test-Path -LiteralPath $templateRoot -PathType Container)) {
    throw "缺少发布模板目录：$templateRoot"
}
$requiredTemplates = @(
    "README.txt", "THIRD-PARTY-NOTICES.txt", "config\runtime.json",
    "scripts\common.ps1", "scripts\preflight.ps1", "scripts\start.ps1",
    "scripts\stop.ps1", "scripts\backup.ps1", "scripts\reset-demo.ps1",
    "scripts\self-check.ps1"
)
foreach ($relativeTemplate in $requiredTemplates) {
    if (-not (Test-Path -LiteralPath (Join-Path $templateRoot $relativeTemplate) -PathType Leaf)) {
        throw "发布模板不完整：$relativeTemplate"
    }
}
$windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
Invoke-Checked -FilePath $windowsPowerShell -Arguments @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
    (Join-Path $templateRoot "scripts\self-check.ps1")
) -FailureMessage "发布运行模板静态自检失败"

$git = (Get-Command git.exe -ErrorAction SilentlyContinue)
if ($null -eq $git) {
    $git = Get-Command git -ErrorAction Stop
}
$gitPath = $git.Source
$sourceCommit = (Invoke-Captured -FilePath $gitPath -Arguments @("-C", $repositoryRoot, "rev-parse", "HEAD") `
    -FailureMessage "无法确定源提交").Trim()
if ($sourceCommit -notmatch "^[0-9a-f]{40}$") {
    throw "无法确定源提交。"
}
$worktreeStatus = Invoke-Captured -FilePath $gitPath `
    -Arguments @("-C", $repositoryRoot, "status", "--porcelain", "--untracked-files=all") `
    -FailureMessage "无法检查 Git 工作区"
$sourceDirty = -not [string]::IsNullOrWhiteSpace($worktreeStatus)
if (-not $AllowDirty -and $sourceDirty) {
    throw "正式发布构建要求干净工作区；开发试构建可显式使用 -AllowDirty。"
}

$shortCommit = $sourceCommit.Substring(0, 12)
$artifactDirectory = Join-Path $OutputRoot $sourceCommit
$zipName = "alert-management-system-windows-x64-$shortCommit.zip"
$zipPath = Join-Path $artifactDirectory $zipName
$checksumPath = "$zipPath.sha256"
if (Test-Path -LiteralPath $artifactDirectory) {
    throw "拒绝覆盖已存在的发布目标：$artifactDirectory"
}

New-Item -ItemType Directory -Force -Path $cacheRoot, $toolsRoot, $stagingRoot, $releaseRoot | Out-Null
$runtimeLock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
if ($runtimeLock.schema_version -ne 1 -or $runtimeLock.target -ne "windows-x64") {
    throw "runtime-lock.json 版本或目标平台不受支持。"
}

$javaTool = Get-BuildTool -Name "microsoft-jdk" -Artifact $runtimeLock.artifacts.java
$pythonTool = Get-BuildTool -Name "python" -Artifact $runtimeLock.artifacts.python
$nodeTool = Get-BuildTool -Name "node" -Artifact $runtimeLock.artifacts.node
$postgresArchive = Get-LockedArchive -Artifact $runtimeLock.artifacts.postgresql

$oldJavaHome = $env:JAVA_HOME
$oldPath = $env:Path
$oldPathExt = $env:PATHEXT
$buildSucceeded = $false
try {
    $env:JAVA_HOME = $javaTool
    $env:Path = "$nodeTool;$javaTool\bin;$oldPath"
    $env:PATHEXT = ".COM;.EXE;.BAT;.CMD"
    $npm = Join-Path $nodeTool "npm.cmd"
    $frontendSource = Join-Path $repositoryRoot "src\frontend"
    $frontendBuild = Join-Path $stagingRoot "frontend-build"
    New-Item -ItemType Directory -Path $frontendBuild | Out-Null
    foreach ($frontendFile in @("package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html")) {
        Copy-Item -LiteralPath (Join-Path $frontendSource $frontendFile) -Destination $frontendBuild
    }
    Copy-Item -LiteralPath (Join-Path $frontendSource "src") -Destination $frontendBuild -Recurse
    Invoke-Checked -FilePath $npm -Arguments @("--prefix", $frontendBuild, "ci") `
        -FailureMessage "前端依赖安装失败"
    Invoke-Checked -FilePath $npm -Arguments @("--prefix", $frontendBuild, "run", "build") `
        -FailureMessage "Vue 生产构建失败"
    $frontendDistTarget = Join-Path $frontendSource "dist"
    if (Test-Path -LiteralPath $frontendDistTarget) {
        Remove-Item -LiteralPath $frontendDistTarget -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $frontendBuild "dist") -Destination $frontendDistTarget -Recurse

    $mavenWrapper = Join-Path $repositoryRoot "mvnw.cmd"
    Invoke-Checked -FilePath $mavenWrapper `
        -Arguments @("-f", (Join-Path $repositoryRoot "src\backend\pom.xml"), "package", "-DskipTests") `
        -FailureMessage "Java 发布 JAR 构建失败"

    $buildVenv = Join-Path $stagingRoot "python-build-venv"
    $python = Join-Path $pythonTool "python.exe"
    Invoke-Checked -FilePath $python -Arguments @("-m", "venv", $buildVenv) `
        -FailureMessage "Windows Python 构建环境创建失败"
    $buildPython = Join-Path $buildVenv "Scripts\python.exe"
    Invoke-Checked -FilePath $buildPython -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "--requirement", (Join-Path $repositoryRoot "src\algorithm\requirements.lock"),
        "--requirement", $pyinstallerLock
    ) -FailureMessage "算法及 PyInstaller 锁定依赖安装失败"
    Invoke-Checked -FilePath $buildPython -Arguments @("-m", "pip", "check") `
        -FailureMessage "Python 构建依赖不完整"

    $algorithmDist = Join-Path $stagingRoot "algorithm-dist"
    $algorithmWork = Join-Path $stagingRoot "algorithm-work"
    Invoke-Checked -FilePath $buildPython -Arguments @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", $algorithmDist, "--workpath", $algorithmWork, $algorithmSpec
    ) -FailureMessage "算法 Windows 可执行文件构建失败"

    Copy-Item -LiteralPath (Join-Path $templateRoot "README.txt") -Destination $releaseRoot
    Copy-Item -LiteralPath (Join-Path $templateRoot "THIRD-PARTY-NOTICES.txt") -Destination $releaseRoot
    Copy-Item -LiteralPath (Join-Path $templateRoot "config") -Destination $releaseRoot -Recurse
    $releaseScripts = Join-Path $releaseRoot "scripts"
    New-Item -ItemType Directory -Path $releaseScripts -Force | Out-Null
    foreach ($scriptName in @("common.ps1", "preflight.ps1", "start.ps1", "stop.ps1", "backup.ps1", "reset-demo.ps1")) {
        Copy-Item -LiteralPath (Join-Path $templateRoot "scripts\$scriptName") -Destination $releaseScripts
    }

    $appDirectory = New-Item -ItemType Directory -Path (Join-Path $releaseRoot "app\algorithm") -Force
    $jarSource = Join-Path $repositoryRoot "src\backend\target\alert-management-backend-0.1.0.jar"
    Copy-Item -LiteralPath $jarSource -Destination (Join-Path $releaseRoot "app\core-api.jar")
    $algorithmSource = Join-Path $algorithmDist "algorithm-service"
    if (-not (Test-Path -LiteralPath (Join-Path $algorithmSource "algorithm-service.exe") -PathType Leaf)) {
        throw "PyInstaller 未生成预期算法 EXE。"
    }
    Copy-Item -Path (Join-Path $algorithmSource "*") -Destination $appDirectory.FullName -Recurse

    $jreDirectory = Join-Path $releaseRoot "runtime\jre"
    New-Item -ItemType Directory -Path (Split-Path -Parent $jreDirectory) -Force | Out-Null
    $jlink = Join-Path $javaTool "bin\jlink.exe"
    Invoke-Checked -FilePath $jlink -Arguments @(
        "--add-modules", "java.se,jdk.crypto.ec,jdk.unsupported,jdk.zipfs",
        "--bind-services", "--strip-debug", "--no-header-files", "--no-man-pages",
        "--compress=zip-6", "--output", $jreDirectory
    ) -FailureMessage "jlink Java 运行时构建失败"

    $postgresExtract = Join-Path $stagingRoot "postgresql-extract"
    $postgresSource = Expand-LockedArchive -ArchivePath $postgresArchive `
        -Artifact $runtimeLock.artifacts.postgresql -Destination $postgresExtract
    $postgresTarget = Join-Path $releaseRoot "runtime\postgresql"
    New-Item -ItemType Directory -Path $postgresTarget -Force | Out-Null
    foreach ($directoryName in @("bin", "lib", "share")) {
        $sourceDirectory = Join-Path $postgresSource $directoryName
        if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) {
            throw "PostgreSQL 归档缺少运行目录：$directoryName"
        }
        Copy-Item -LiteralPath $sourceDirectory -Destination $postgresTarget -Recurse
    }
    foreach ($licenseName in @("server_license.txt", "commandlinetools_3rd_party_licenses.txt")) {
        $licensePath = Join-Path $postgresSource $licenseName
        if (Test-Path -LiteralPath $licensePath -PathType Leaf) {
            Copy-Item -LiteralPath $licensePath -Destination $postgresTarget
        }
    }

    $sampleTarget = Join-Path $releaseRoot "samples"
    foreach ($sampleGroup in @("smoke", "invalid")) {
        $groupTarget = Join-Path $sampleTarget $sampleGroup
        New-Item -ItemType Directory -Path $groupTarget -Force | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $repositoryRoot "samples\$sampleGroup") -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $groupTarget
        }
    }
    $expectedTarget = Join-Path $sampleTarget "expected"
    New-Item -ItemType Directory -Path $expectedTarget -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $repositoryRoot "samples\expected") -File -Filter "*.json" | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $expectedTarget
    }
    $demoTarget = Join-Path $sampleTarget "demo"
    New-Item -ItemType Directory -Path $demoTarget -Force | Out-Null
    Invoke-Checked -FilePath $buildPython -Arguments @(
        (Join-Path $repositoryRoot "samples\generate_samples.py"),
        "--dataset", "demo", "--output", (Join-Path $demoTarget "synthetic_demo_20000.csv")
    ) -FailureMessage "固定种子 20000 行发布样例生成失败"

    $forbiddenNames = @("node.exe", "npm.cmd", "npx.cmd", "python.exe", "docker.exe", "wsl.exe", "bash.exe")
    foreach ($forbiddenName in $forbiddenNames) {
        if (Get-ChildItem -LiteralPath $releaseRoot -File -Recurse -Filter $forbiddenName | Select-Object -First 1) {
            throw "发布物包含禁止的开发运行时：$forbiddenName"
        }
    }
    foreach ($pythonSourcePattern in @("*.py", "*.pyc")) {
        if (Get-ChildItem -LiteralPath $releaseRoot -File -Recurse -Filter $pythonSourcePattern | Select-Object -First 1) {
            throw "发布物包含禁止的 Python 源码或缓存：$pythonSourcePattern"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $releaseRoot "scripts\dev")) {
        throw "发布物不得包含开发脚本。"
    }

    $manifestFiles = @(
        Get-ChildItem -LiteralPath $releaseRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
            $relativePath = $_.FullName.Substring($releaseRoot.Length + 1).Replace("\", "/")
            [ordered]@{
                path = $relativePath
                size = [int64]$_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    )
    $manifest = [ordered]@{
        schema_version = 1
        product = "alert-management-system"
        release_version = $ReleaseVersion
        target = "windows-x64"
        source_commit = $sourceCommit
        source_dirty = $sourceDirty
        built_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        components = [ordered]@{
            backend = [ordered]@{ version = "0.1.0" }
            frontend = [ordered]@{ version = "0.1.0" }
            algorithm = [ordered]@{
                version = "0.2.0"
                contract_version = "v2"
                python_version = [string]$runtimeLock.artifacts.python.version
                packager = "PyInstaller 6.22.2"
            }
            java = [ordered]@{
                distribution = [string]$runtimeLock.artifacts.java.distribution
                version = [string]$runtimeLock.artifacts.java.version
                source_sha256 = [string]$runtimeLock.artifacts.java.sha256
            }
            postgresql = [ordered]@{
                version = [string]$runtimeLock.artifacts.postgresql.version
                source_sha256 = [string]$runtimeLock.artifacts.postgresql.sha256
            }
        }
        files = $manifestFiles
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText(
        (Join-Path $releaseRoot "release-manifest.json"),
        ($manifestJson + "`n"),
        (New-Object Text.UTF8Encoding($false))
    )

    $zipTemporary = Join-Path $stagingRoot $zipName
    Compress-Archive -LiteralPath $releaseRoot -DestinationPath $zipTemporary -CompressionLevel Optimal
    New-Item -ItemType Directory -Path $artifactDirectory | Out-Null
    Move-Item -LiteralPath $zipTemporary -Destination $zipPath
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText(
        $checksumPath,
        ("$zipHash  $zipName`n"),
        (New-Object Text.UTF8Encoding($false))
    )
    $buildSucceeded = $true
}
finally {
    $env:JAVA_HOME = $oldJavaHome
    $env:Path = $oldPath
    $env:PATHEXT = $oldPathExt
    if ($buildSucceeded -and (Test-Path -LiteralPath $stagingRoot)) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}

Write-Host "Windows x64 原生发布包已生成：$zipPath"
Write-Output "NATIVE_RELEASE_ZIP=$zipPath"
