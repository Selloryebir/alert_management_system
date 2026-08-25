[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Assert-RequiredFile {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Hint)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "发布文件缺失：$Path。修复建议：$Hint"
    }
}

function Assert-WindowsX64Pe {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $reader = New-Object IO.BinaryReader($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            throw "不是有效的 Windows PE 文件：$Path"
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadInt32()
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw "不是 Windows x64 可执行文件：$Path"
        }
    } finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Assert-ManifestFileHash {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    $entries = @($Manifest.files | Where-Object { $_.path -eq $RelativePath })
    if ($entries.Count -ne 1) {
        throw "release-manifest.json 未唯一记录文件：$RelativePath"
    }
    $path = Join-ReleasePath $Context.Root $RelativePath
    $actualSize = (Get-Item -LiteralPath $path).Length
    if ([Int64]$entries[0].size -ne $actualSize) {
        throw "发布文件大小与清单不一致：$RelativePath"
    }
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$entries[0].sha256).ToLowerInvariant()) {
        throw "发布文件 SHA-256 与清单不一致：$RelativePath"
    }
}

try {
    if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
        throw "仅支持 Windows 11 x64。修复建议：在 64 位 Windows 11 电脑上运行本发布包。"
    }

    $context = Get-RuntimeContext
    Assert-FixedRuntimeConfig $context
    Initialize-ReleaseDirectories $context

    $required = [ordered]@{
        $context.Manifest = "重新获取完整发布 ZIP 并完整解压。"
        $context.Java = "重新获取包含 jlink JRE 的完整发布 ZIP。"
        $context.BackendJar = "重新获取包含主程序 JAR 的完整发布 ZIP。"
        $context.Algorithm = "重新获取包含算法 onedir 的完整发布 ZIP。"
        (Get-PostgresExecutable $context "postgres") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "initdb") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "pg_ctl") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "pg_isready") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "psql") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "createdb") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "pg_dump") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
        (Get-PostgresExecutable $context "pg_restore") = "重新获取包含 PostgreSQL 运行时的完整发布 ZIP。"
    }
    foreach ($item in $required.GetEnumerator()) {
        Assert-RequiredFile $item.Key $item.Value
    }
    $samples = Join-ReleasePath $context.Root "samples"
    if (-not (Test-Path -LiteralPath $samples -PathType Container) -or
            @(Get-ChildItem -LiteralPath $samples -File -Recurse).Count -eq 0) {
        throw "samples 目录缺少演示文件。修复建议：重新完整解压发布 ZIP。"
    }

    foreach ($directory in @($context.Logs, $context.Pids, $context.Backups, (Split-Path $context.PgData -Parent))) {
        $probe = Join-Path $directory (".write-test-" + [Guid]::NewGuid().ToString("N"))
        [IO.File]::WriteAllText($probe, "ok")
        Remove-Item -LiteralPath $probe -Force
    }
    $freeBytes = (Get-Item -LiteralPath $context.Root).PSDrive.Free
    if ($freeBytes -lt 2GB) {
        throw "发布盘剩余空间不足 2 GB。修复建议：清理磁盘后重试。"
    }

    foreach ($port in @([int]$context.Config.ports.postgres, [int]$context.Config.ports.algorithm,
            [int]$context.Config.ports.backend)) {
        if (-not (Test-PortAvailable $port)) {
            throw "固定端口 $port 已被占用。修复建议：停止占用该端口的程序后重试；本包不会自动换端口。"
        }
    }

    $manifest = Get-Content -LiteralPath $context.Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$manifest.schema_version -ne 1 -or $manifest.target -ne "windows-x64" -or
            [string]::IsNullOrWhiteSpace([string]$manifest.source_commit)) {
        throw "release-manifest.json 的版本、目标平台或源提交无效。"
    }
    if ($manifest.components.java.version -ne "21.0.12.1" -or
            $manifest.components.postgresql.version -ne "17.11" -or
            $manifest.components.algorithm.version -ne "0.1.0" -or
            $manifest.components.algorithm.contract_version -ne "v1" -or
            $manifest.components.algorithm.packager -ne "PyInstaller 6.22.2") {
        throw "release-manifest.json 的运行时版本与 M6 锁定版本不一致。"
    }

    foreach ($relative in @("app/core-api.jar", "app/algorithm/algorithm-service.exe",
            "runtime/jre/bin/java.exe", "runtime/postgresql/bin/postgres.exe")) {
        Assert-ManifestFileHash $context $manifest $relative
    }
    Assert-WindowsX64Pe $context.Algorithm
    Assert-WindowsX64Pe $context.Java
    Assert-WindowsX64Pe (Get-PostgresExecutable $context "postgres")

    $workingRoot = Initialize-PostgresWorkingRoot $context
    try {
        $workingJava = Join-Path $workingRoot "runtime\jre\bin\java.exe"
        $javaVersion = Invoke-BundledCommand $workingJava @("-version") $workingRoot
        if ($javaVersion -notmatch '21\.0\.12') {
            throw "包内 Java 版本不是锁定的 21.0.12.1：$javaVersion"
        }
        $postgresVersion = Invoke-BundledCommand `
            (Get-PostgresExecutable $context "postgres" $workingRoot) @("--version") $workingRoot
    } finally {
        Remove-PostgresWorkingRoot $context
    }
    if ($postgresVersion -notmatch '17\.11') {
        throw "包内 PostgreSQL 版本不是锁定的 17.11：$postgresVersion"
    }

    Write-Host "预检通过：Windows x64、发布清单、包内运行时、目录、磁盘和固定端口均符合要求。"
    Write-Host "Java：$javaVersion"
    Write-Host "PostgreSQL：$postgresVersion"
    Write-Host "算法：清单版本 0.1.0；启动后将通过 /health 核对真实版本和契约。"
    exit 0
} catch {
    Write-Error ("预检失败：" + $_.Exception.Message)
    exit 1
}
