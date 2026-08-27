[CmdletBinding()]
param(
    [string]$OutputRoot = "",
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$ReleaseVersion = "1.0.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$buildScript = Join-Path $repositoryRoot "scripts\native\build-release.ps1"
$verifyScript = Join-Path $repositoryRoot "scripts\native\verify-release.ps1"
$standardUserScript = Join-Path $repositoryRoot "scripts\native\verify-release-as-standard-user.ps1"
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot ".runtime\release"
} elseif (-not [IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$expectedOutputRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot ".runtime\release"))
if (-not $OutputRoot.Equals($expectedOutputRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "M14 正式验收输出根必须为仓库专用目录：$expectedOutputRoot"
}
$artifactRoot = Join-Path $OutputRoot "artifacts"
$verificationRoot = Join-Path $OutputRoot "verification"
$negativeRoot = Join-Path $OutputRoot ("negative-" + [Guid]::NewGuid().ToString("N"))
$summaryPath = Join-Path $OutputRoot "business-release-summary.json"
$ownershipMarkerPath = Join-Path $OutputRoot ".ams-business-release-output.json"
$sourceCommit = $null
$archive = $null
$archiveHash = $null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-OrdinaryPathIfExists {
    param([string]$Path, [string]$Description)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return }
    Assert-True (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) `
        "$Description 不得是符号链接、Junction 或其他重解析点：$Path"
}

function Assert-OrdinaryExistingSegments {
    param([string]$Path, [string]$Description)
    $fullPath = [IO.Path]::GetFullPath($Path)
    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    $current = $pathRoot.TrimEnd('\')
    foreach ($segment in $fullPath.Substring($pathRoot.Length).Split(
            @([char]'\', [char]'/'), [StringSplitOptions]::RemoveEmptyEntries)) {
        $current = Join-Path $current $segment
        Assert-OrdinaryPathIfExists $current $Description
    }
}

function Write-BusinessSummary {
    param([System.Collections.IDictionary]$Summary)
    Assert-OrdinaryExistingSegments $summaryPath "业务发布摘要路径"
    Assert-OrdinaryPathIfExists $summaryPath "业务发布摘要"
    $Summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
}

function ConvertTo-NativeArgument {
    param([string]$Value)
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-CapturedProcess {
    param([string]$Executable, [string[]]$Arguments = @())
    $captureRoot = Join-Path ([IO.Path]::GetTempPath()) ("ams-release-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $captureRoot | Out-Null
    $stdout = Join-Path $captureRoot "stdout.log"
    $stderr = Join-Path $captureRoot "stderr.log"
    try {
        $argumentLine = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join ' ')
        $process = Start-Process -FilePath $Executable -ArgumentList $argumentLine `
            -WorkingDirectory $repositoryRoot -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $output = @()
        if (Test-Path -LiteralPath $stdout) { $output += @(Get-Content -LiteralPath $stdout -Encoding UTF8) }
        if (Test-Path -LiteralPath $stderr) { $output += @(Get-Content -LiteralPath $stderr -Encoding UTF8) }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
    } finally {
        Remove-Item -LiteralPath $captureRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-CapturedPowerShell {
    param([string]$Script, [string[]]$Arguments = @())
    return Invoke-CapturedProcess $powerShellExe `
        (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Script) + $Arguments)
}

try {
    Assert-OrdinaryExistingSegments $repositoryRoot "仓库路径"
    Assert-OrdinaryExistingSegments $OutputRoot "正式验收输出路径"
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
    Assert-OrdinaryExistingSegments $OutputRoot "正式验收输出路径"
    foreach ($required in @($powerShellExe, $buildScript, $verifyScript, $standardUserScript)) {
        Assert-True (Test-Path -LiteralPath $required -PathType Leaf) "发布候选验收缺少文件：$required"
    }

    $status = Invoke-CapturedProcess "git.exe" @(
        "-c", "safe.directory=$repositoryRoot", "-C", $repositoryRoot,
        "status", "--porcelain", "--untracked-files=all")
    Assert-True ($status.ExitCode -eq 0) "无法读取 Git 工作区状态。"
    Assert-True ($status.Output.Count -eq 0) "正式业务验收拒绝脏工作区。"
    $commitResult = Invoke-CapturedProcess "git.exe" @(
        "-c", "safe.directory=$repositoryRoot", "-C", $repositoryRoot, "rev-parse", "HEAD")
    $sourceCommit = (($commitResult.Output -join "`n").Trim())
    Assert-True ($commitResult.ExitCode -eq 0 -and $sourceCommit -match '^[0-9a-f]{40}$') "无法读取完整源提交。"

    Assert-OrdinaryPathIfExists $ownershipMarkerPath "正式验收输出目录身份标记"
    Assert-OrdinaryPathIfExists $summaryPath "业务发布摘要"
    if (Test-Path -LiteralPath $ownershipMarkerPath -PathType Leaf) {
        $ownership = Get-Content -LiteralPath $ownershipMarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert-True ($ownership.product -eq "alert-management-system-business-release" -and
            ([IO.Path]::GetFullPath([string]$ownership.output_root)).Equals(
                $OutputRoot, [StringComparison]::OrdinalIgnoreCase)) `
            "正式验收输出目录身份标记无效，拒绝清理：$OutputRoot"
    } else {
        Assert-True (-not (Test-Path -LiteralPath $artifactRoot) -and
            -not (Test-Path -LiteralPath $verificationRoot)) `
            "正式验收输出目录缺少身份标记且已有生成目录，拒绝清理：$OutputRoot"
        [ordered]@{
            schema_version = 1
            product = "alert-management-system-business-release"
            output_root = $OutputRoot
        } | ConvertTo-Json | Set-Content -LiteralPath $ownershipMarkerPath -Encoding UTF8
    }

    $artifactDirectory = [IO.Path]::GetFullPath((Join-Path $artifactRoot $sourceCommit))
    $verificationRoot = [IO.Path]::GetFullPath($verificationRoot)
    Assert-True ((Split-Path -Leaf $artifactDirectory) -eq $sourceCommit -and
        (Split-Path -Parent $artifactDirectory).Equals([IO.Path]::GetFullPath($artifactRoot),
            [StringComparison]::OrdinalIgnoreCase)) `
        "仅允许清理以当前完整提交命名的产物目录：$artifactDirectory"
    Assert-True ((Split-Path -Leaf $verificationRoot) -eq "verification" -and
        (Split-Path -Parent $verificationRoot).Equals($OutputRoot,
            [StringComparison]::OrdinalIgnoreCase)) `
        "仅允许清理固定的验收生成目录：$verificationRoot"
    foreach ($generatedDirectory in @($artifactDirectory, $verificationRoot)) {
        Assert-OrdinaryExistingSegments $generatedDirectory "验收生成目录"
        Assert-OrdinaryPathIfExists $generatedDirectory "验收生成目录"
        if (Test-Path -LiteralPath $generatedDirectory) {
            Remove-Item -LiteralPath $generatedDirectory -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Path $artifactRoot, $verificationRoot, $negativeRoot -Force | Out-Null
    foreach ($generatedDirectory in @($artifactRoot, $verificationRoot, $negativeRoot)) {
        Assert-OrdinaryExistingSegments $generatedDirectory "验收生成目录"
    }

    $junctionExternal = Join-Path ([IO.Path]::GetTempPath()) (
        "ams-release-boundary-" + [Guid]::NewGuid().ToString("N"))
    $junctionPath = Join-Path $negativeRoot "reparse-boundary"
    New-Item -ItemType Directory -Path $junctionExternal | Out-Null
    Set-Content -LiteralPath (Join-Path $junctionExternal "sentinel.txt") -Value "KEEP" -Encoding ASCII
    try {
        $junctionResult = Invoke-CapturedProcess $env:ComSpec @(
            "/d", "/c", "mklink", "/J", $junctionPath, $junctionExternal)
        Assert-True ($junctionResult.ExitCode -eq 0) `
            "无法建立发布目录重解析点负例：$($junctionResult.Output -join ' | ')"
        $reparseRejected = $false
        try {
            Assert-OrdinaryPathIfExists $junctionPath "发布目录重解析点负例"
        } catch {
            $reparseRejected = $true
        }
        Assert-True $reparseRejected "正式验收边界未拒绝发布目录内的 Junction。"
        Assert-True (Test-Path -LiteralPath (Join-Path $junctionExternal "sentinel.txt") -PathType Leaf) `
            "重解析点负例越界损坏了外部哨兵文件。"
    } finally {
        $junctionItem = Get-Item -LiteralPath $junctionPath -Force -ErrorAction SilentlyContinue
        if ($null -ne $junctionItem) {
            Assert-True (($junctionItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) `
                "只允许以非递归方式删除已验证的负例 Junction：$junctionPath"
            [IO.Directory]::Delete($junctionPath, $false)
        }
        Assert-OrdinaryExistingSegments $junctionExternal "重解析点负例外部目录"
        Assert-OrdinaryPathIfExists $junctionExternal "重解析点负例外部目录"
        if (Test-Path -LiteralPath $junctionExternal) {
            Remove-Item -LiteralPath $junctionExternal -Recurse -Force
        }
    }

    $build = Invoke-CapturedPowerShell $buildScript @(
        "-OutputRoot", $artifactRoot, "-ReleaseVersion", $ReleaseVersion)
    $build.Output | ForEach-Object { Write-Host $_ }
    Assert-True ($build.ExitCode -eq 0) "发布候选 ZIP 构建失败，退出码：$($build.ExitCode)。"
    $markers = @($build.Output | Where-Object { [string]$_ -like "NATIVE_RELEASE_ZIP=*" })
    Assert-True ($markers.Count -eq 1) "构建脚本未输出唯一 NATIVE_RELEASE_ZIP。"
    $archive = [IO.Path]::GetFullPath(([string]$markers[0]).Substring("NATIVE_RELEASE_ZIP=".Length).Trim())
    Assert-OrdinaryExistingSegments $archive "发布候选 ZIP"
    Assert-OrdinaryPathIfExists $archive "发布候选 ZIP"
    Assert-True (Test-Path -LiteralPath $archive -PathType Leaf) "发布候选 ZIP 不存在：$archive"
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()

    $corruptArchive = Join-Path $negativeRoot ([IO.Path]::GetFileName($archive))
    Copy-Item -LiteralPath $archive -Destination $corruptArchive
    Copy-Item -LiteralPath ($archive + ".sha256") -Destination ($corruptArchive + ".sha256")
    $stream = [IO.File]::Open($corruptArchive, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $first = $stream.ReadByte()
        Assert-True ($first -ge 0) "发布候选 ZIP 为空。"
        $stream.Position = 0
        $stream.WriteByte($first -bxor 0xFF)
    } finally {
        $stream.Dispose()
    }
    $negative = Invoke-CapturedPowerShell $verifyScript @(
        "-ArchivePath", $corruptArchive, "-OutputRoot", (Join-Path $negativeRoot "verification"),
        "-ReleaseVersion", $ReleaseVersion, "-BusinessRelease")
    Assert-True ($negative.ExitCode -ne 0) "损坏 ZIP 本应被 SHA-256 门槛拒绝，却返回成功。"
    Assert-True (($negative.Output -join "`n") -match 'SHA-256') "损坏 ZIP 失败原因不是 SHA-256 校验。"

    $verification = Invoke-CapturedPowerShell $standardUserScript @(
        "-ArchivePath", $archive, "-OutputRoot", $verificationRoot,
        "-ReleaseVersion", $ReleaseVersion, "-BusinessRelease")
    $verification.Output | ForEach-Object { Write-Host $_ }
    Assert-True ($verification.ExitCode -eq 0) "标准用户业务终验预检查失败，退出码：$($verification.ExitCode)。"

    $summaries = @(Get-ChildItem -LiteralPath $verificationRoot -Filter "verification-summary.json" -File -Recurse)
    Assert-True ($summaries.Count -eq 1) "业务终验必须生成唯一 verification-summary.json。"
    $technical = Get-Content -LiteralPath $summaries[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ($technical.source_commit -eq $sourceCommit -and [bool]$technical.business_release) `
        "技术验收摘要未绑定当前提交或业务发布模式。"
    Assert-True (-not [bool]$technical.windows_is_administrator) `
        "业务发布预检查必须由 Windows 标准用户执行。"
    Assert-True ($null -ne $technical.cross_instance_restore) "技术验收摘要缺少跨实例恢复证据。"

    Write-BusinessSummary ([ordered]@{
        schema_version = 1
        product = "alert-management-system"
        status = "PASS"
        release_version = $ReleaseVersion
        source_commit = $sourceCommit
        archive = $archive
        archive_sha256 = $archiveHash
        corrupt_archive_rejected = $true
        standard_user_precheck = "PASS"
        technical_summary = $summaries[0].FullName
        human_business_acceptance = "REQUIRED"
        completed_at = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Write-Host "M14 Windows 业务发布候选自动预检查通过；仍需非技术业务人员人工终验。"
    Write-Host "BUSINESS_RELEASE_ZIP=$archive"
    Write-Host "BUSINESS_RELEASE_SHA256=$archiveHash"
    Write-Host "BUSINESS_RELEASE_SUMMARY=$summaryPath"
} catch {
    $primaryError = $_
    try {
        Write-BusinessSummary ([ordered]@{
                schema_version = 1
                product = "alert-management-system"
                status = "FAILED"
                release_version = $ReleaseVersion
                source_commit = $sourceCommit
                archive = $archive
                archive_sha256 = $archiveHash
                error = $primaryError.Exception.Message
                completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            })
    } catch {
        Write-Warning "失败摘要未写入不可信的输出路径：$($_.Exception.Message)"
    }
    throw $primaryError
} finally {
    if (Test-Path -LiteralPath $negativeRoot) {
        try {
            Assert-OrdinaryExistingSegments $negativeRoot "验收负例目录"
            Assert-OrdinaryPathIfExists $negativeRoot "验收负例目录"
            Remove-Item -LiteralPath $negativeRoot -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Warning "验收负例目录未清理：$($_.Exception.Message)"
        }
    }
}
