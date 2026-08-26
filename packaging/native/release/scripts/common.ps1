Set-StrictMode -Version 2.0
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = "Stop"

function Get-ReleaseRoot {
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}

function Join-ReleasePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    $native = $RelativePath.Replace('/', [IO.Path]::DirectorySeparatorChar)
    return [IO.Path]::GetFullPath((Join-Path $Root $native))
}

function Resolve-ReleaseChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    if ([IO.Path]::IsPathRooted($RelativePath)) {
        throw "运行配置中的路径必须是发布目录内的相对路径：$RelativePath"
    }
    $resolved = Join-ReleasePath $Root $RelativePath
    $rootPrefix = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "运行配置中的路径越出发布目录：$RelativePath"
    }
    return $resolved
}

function Get-RuntimeContext {
    $root = Get-ReleaseRoot
    $configPath = Join-ReleasePath $root "config/runtime.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "运行配置缺失：$configPath"
    }
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    return [PSCustomObject]@{
        Root = $root
        Config = $config
        Manifest = Join-ReleasePath $root "release-manifest.json"
        Java = Join-ReleasePath $root "runtime/jre/bin/java.exe"
        BackendJar = Join-ReleasePath $root "app/core-api.jar"
        Algorithm = Join-ReleasePath $root "app/algorithm/algorithm-service.exe"
        PgBin = Join-ReleasePath $root "runtime/postgresql/bin"
        PgData = Join-ReleasePath $root "data/postgresql"
        PgDataArgument = "data\postgresql"
        InstanceIdentity = Join-ReleasePath $root "data/instance.json"
        RestoreVerificationRoot = Join-ReleasePath $root "data/restore-verification"
        Secrets = Join-ReleasePath $root "data/secrets"
        DatabasePasswordFile = Resolve-ReleaseChildPath $root ([string]$config.database.password_file)
        BootstrapAdminPasswordFile = Resolve-ReleaseChildPath $root ([string]$config.bootstrap_admin.password_file)
        Logs = Join-ReleasePath $root "logs"
        Pids = Join-ReleasePath $root "pids"
        Backups = Join-ReleasePath $root "backups"
    }
}

function Initialize-ReleaseDirectories {
    param([Parameter(Mandatory = $true)]$Context)
    foreach ($path in @($Context.Logs, $Context.Pids, $Context.Backups, $Context.Secrets,
            (Split-Path $Context.PgData -Parent))) {
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Path $path | Out-Null
        }
    }
}

function Assert-FixedRuntimeConfig {
    param([Parameter(Mandatory = $true)]$Context)
    $config = $Context.Config
    if ([int]$config.schema_version -ne 2 -or $config.identity -ne "报警管理系统" -or
            $config.deployment_mode -ne "LOCAL_NATIVE") {
        throw "config/runtime.json 的 identity 与发布契约不一致。"
    }
    if ([int]$config.ports.postgres -ne 55432 -or [int]$config.ports.algorithm -ne 8001 -or
            [int]$config.ports.backend -ne 8080) {
        throw "发布端口必须固定为 PostgreSQL 55432、算法 8001、主程序 8080。"
    }
    foreach ($value in @($config.database.name, $config.database.user)) {
        if ([string]::IsNullOrWhiteSpace([string]$value) -or $value -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw "数据库名称和用户必须是安全的 PostgreSQL 标识符。"
        }
    }
    if ($config.PSObject.Properties.Name -contains "password") {
        throw "config/runtime.json 不得包含数据库明文密码。"
    }
    if ([string]$config.database.password_file -ne "data/secrets/database-password.txt" -or
            [string]$config.bootstrap_admin.password_file -ne "data/secrets/bootstrap-admin-password.txt" -or
            [string]$config.bootstrap_admin.username -notmatch '^[a-z0-9._-]{3,50}$') {
        throw "密钥文件或初始管理员配置与发布契约不一致。"
    }
}

function Get-ReleaseManifest {
    param([Parameter(Mandatory = $true)]$Context)
    if (-not (Test-Path -LiteralPath $Context.Manifest -PathType Leaf)) {
        throw "发布清单不存在：$($Context.Manifest)"
    }
    $manifest = Get-Content -LiteralPath $Context.Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$manifest.schema_version -ne 1 -or $manifest.product -ne "alert-management-system" -or
            $manifest.target -ne "windows-x64" -or
            [string]$manifest.source_commit -notmatch '^[0-9A-Fa-f]{40}$') {
        throw "release-manifest.json 不能唯一标识当前 Windows 发布包。"
    }
    return $manifest
}

function Assert-InstanceIdentity {
    param([Parameter(Mandatory = $true)]$Context)
    if (-not (Test-Path -LiteralPath $Context.InstanceIdentity -PathType Leaf)) {
        throw "当前发布实例缺少身份文件：$($Context.InstanceIdentity)"
    }
    $identity = Get-Content -LiteralPath $Context.InstanceIdentity -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest = Get-ReleaseManifest $Context
    if ([int]$identity.schema_version -ne 1 -or $identity.product -ne "alert-management-system" -or
            [string]$identity.instance_id -notmatch '^[0-9a-f]{32}$' -or
            -not (Normalize-DirectoryPath ([string]$identity.release_root)).Equals(
                (Normalize-DirectoryPath $Context.Root), [StringComparison]::OrdinalIgnoreCase) -or
            -not ([string]$identity.source_commit).Equals(
                [string]$manifest.source_commit, [StringComparison]::OrdinalIgnoreCase)) {
        throw "实例身份与当前发布目录或发布清单不一致，拒绝操作。"
    }
    return $identity
}

function Initialize-InstanceIdentity {
    param([Parameter(Mandatory = $true)]$Context)
    if (Test-Path -LiteralPath $Context.InstanceIdentity) {
        [void](Assert-InstanceIdentity $Context)
        return
    }
    if (Test-Path -LiteralPath (Join-Path $Context.PgData "PG_VERSION") -PathType Leaf) {
        throw "已有 PostgreSQL 数据但缺少实例身份，拒绝自动认领。"
    }
    $manifest = Get-ReleaseManifest $Context
    $identity = [ordered]@{
        schema_version = 1
        product = "alert-management-system"
        instance_id = [Guid]::NewGuid().ToString("N")
        release_root = Normalize-DirectoryPath $Context.Root
        source_commit = [string]$manifest.source_commit
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $parent = Split-Path $Context.InstanceIdentity -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }
    $temporary = $Context.InstanceIdentity + ".tmp-" + [Guid]::NewGuid().ToString("N")
    try {
        [IO.File]::WriteAllText($temporary, (($identity | ConvertTo-Json -Depth 4) + "`n"),
            (New-Object Text.UTF8Encoding($false)))
        [IO.File]::Move($temporary, $Context.InstanceIdentity)
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Assert-OwnedMutableDirectory {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedPath
    )
    $actual = Normalize-DirectoryPath $Path
    $expected = Normalize-DirectoryPath $ExpectedPath
    $root = Normalize-DirectoryPath $Context.Root
    if (-not $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -or
            $actual.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
            -not $actual.StartsWith(($root + [IO.Path]::DirectorySeparatorChar),
                [StringComparison]::OrdinalIgnoreCase)) {
        throw "清理路径不是当前发布实例的精确受控目录：$actual"
    }
    if (Test-Path -LiteralPath $actual) {
        $item = Get-Item -LiteralPath $actual -Force
        if (-not $item.PSIsContainer -or
                (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "清理路径不是普通目录，拒绝递归删除：$actual"
        }
    }
    return $actual
}

function Assert-NoReleasePathReparseBoundary {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $root = Normalize-DirectoryPath $Context.Root
    $target = Normalize-DirectoryPath $Path
    if (-not ($target.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
            $target.StartsWith(($root + [IO.Path]::DirectorySeparatorChar),
                [StringComparison]::OrdinalIgnoreCase))) {
        throw "路径越出当前发布实例：$target"
    }
    $current = $root
    foreach ($segment in @($target.Substring($root.Length).TrimStart('\', '/').Split(
            @([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar),
            [StringSplitOptions]::RemoveEmptyEntries))) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "发布实例路径边界包含 Junction 或符号链接：$current"
            }
        }
        $current = Join-Path $current $segment
    }
    if (Test-Path -LiteralPath $current) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "发布实例路径边界包含 Junction 或符号链接：$current"
        }
    }
}

function Enter-InstanceMaintenanceLock {
    param([Parameter(Mandatory = $true)]$Context)
    $identity = Assert-InstanceIdentity $Context
    $lockPath = Join-ReleasePath $Context.Root (".instance-maintenance-" +
        [string]$identity.instance_id + ".lock")
    Assert-NoReleasePathReparseBoundary $Context $lockPath
    try {
        $stream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw "当前实例正在执行备份、恢复或清理，无法取得维护互斥锁。"
    }
    return [PSCustomObject]@{ Path = $lockPath; Stream = $stream }
}

function Exit-InstanceMaintenanceLock {
    param($Lock)
    if ($null -ne $Lock -and $null -ne $Lock.Stream) {
        $Lock.Stream.Dispose()
    }
}

function Assert-OwnedBackupFile {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Path
    )
    Assert-NoReleasePathReparseBoundary $Context $Context.Backups
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not (Normalize-DirectoryPath (Split-Path $resolved -Parent)).Equals(
            (Normalize-DirectoryPath $Context.Backups), [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "文件不属于当前发布实例的 backups 目录：$resolved"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "恢复点文件不能是 Junction 或符号链接：$resolved"
    }
    return $item
}

function Get-RecoveryPoint {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$MetadataPath,
        [switch]$VerifyHash,
        [switch]$RequireCurrentOrigin
    )
    $metadataItem = Assert-OwnedBackupFile $Context $MetadataPath
    if (-not $metadataItem.Name.EndsWith(".dump.meta.json", [StringComparison]::OrdinalIgnoreCase)) {
        throw "恢复点元数据名称无效：$($metadataItem.Name)"
    }
    $metadata = Get-Content -LiteralPath $metadataItem.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $backupFile = [string]$metadata.backup_file
    if ([IO.Path]::GetFileName($backupFile) -ne $backupFile -or
            $metadataItem.Name -ne ($backupFile + ".meta.json") -or
            [int]$metadata.schema_version -ne 1 -or
            $metadata.product -ne "alert-management-system-recovery-point" -or
            [string]$metadata.origin_instance_id -notmatch '^[0-9a-f]{32}$' -or
            [string]$metadata.origin_source_commit -notmatch '^[0-9a-f]{40}$' -or
            [string]$metadata.database -ne [string]$Context.Config.database.name -or
            $metadata.pg_restore_list_verified -ne $true -or
            [string]$metadata.sha256 -notmatch '^[0-9a-f]{64}$' -or
            [Int64]$metadata.size_bytes -le 0) {
        throw "恢复点元数据格式或来源字段无效：$($metadataItem.FullName)"
    }
    $createdAt = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$metadata.created_at, [ref]$createdAt)) {
        throw "恢复点创建时间无效：$($metadataItem.FullName)"
    }
    if ($RequireCurrentOrigin) {
        $identity = Assert-InstanceIdentity $Context
        $manifest = Get-ReleaseManifest $Context
        if ([string]$metadata.origin_instance_id -ne [string]$identity.instance_id -or
                [string]$metadata.origin_source_commit -ne [string]$manifest.source_commit) {
            throw "恢复点来源不是当前实例，保留策略不会自动删除：$($metadataItem.FullName)"
        }
    }
    $dumpItem = Assert-OwnedBackupFile $Context (Join-Path $Context.Backups $backupFile)
    if ([Int64]$dumpItem.Length -ne [Int64]$metadata.size_bytes) {
        throw "恢复点大小与元数据不一致：$($dumpItem.FullName)"
    }
    $hashStatus = "NOT_CHECKED"
    if ($VerifyHash) {
        $actualHash = (Get-FileHash -LiteralPath $dumpItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne [string]$metadata.sha256) {
            throw "恢复点 SHA-256 与元数据不一致：$($dumpItem.FullName)"
        }
        $hashStatus = "OK"
    }
    return [PSCustomObject]@{
        BackupPath = $dumpItem.FullName
        MetadataPath = $metadataItem.FullName
        Metadata = $metadata
        CreatedAt = $createdAt
        SizeBytes = [Int64]$dumpItem.Length
        HashStatus = $hashStatus
    }
}

function Invoke-RecoveryPointRetention {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][ValidateRange(1, 365)][int]$RetentionCount
    )
    $validPoints = @()
    foreach ($metadataFile in @(Get-ChildItem -LiteralPath $Context.Backups `
            -Filter "*.dump.meta.json" -File -ErrorAction SilentlyContinue)) {
        try {
            $validPoints += Get-RecoveryPoint $Context $metadataFile.FullName `
                -VerifyHash -RequireCurrentOrigin
        } catch {
            Write-Warning "保留策略跳过异常恢复点：$($metadataFile.Name)：$($_.Exception.Message)"
        }
    }
    $ordered = @($validPoints | Sort-Object CreatedAt -Descending)
    if ($ordered.Count -le $RetentionCount) {
        return
    }
    foreach ($point in @($ordered | Select-Object -Skip $RetentionCount)) {
        [void](Assert-OwnedBackupFile $Context $point.BackupPath)
        [void](Assert-OwnedBackupFile $Context $point.MetadataPath)
        Remove-Item -LiteralPath $point.BackupPath -Force
        Remove-Item -LiteralPath $point.MetadataPath -Force
        Write-Host "已按保留策略移除旧恢复点：$([IO.Path]::GetFileName($point.BackupPath))"
    }
}

function Get-InstanceBackupTaskName {
    param([Parameter(Mandatory = $true)]$Identity)
    return "AlertManagementSystem-Backup-" + [string]$Identity.instance_id
}


function Protect-SecretFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow)
    [void]$security.AddAccessRule($rule)
    [IO.File]::SetAccessControl($Path, $security)
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Initialize-SecretFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "密钥路径不是普通文件：$Path"
        }
        if ([string]::IsNullOrWhiteSpace([IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8))) {
            throw "密钥文件为空，拒绝覆盖：$Path"
        }
        Protect-SecretFile $Path
        return
    }
    $temporary = $Path + ".tmp-" + [Guid]::NewGuid().ToString("N")
    try {
        [IO.File]::WriteAllText($temporary, (New-RandomSecret), (New-Object Text.UTF8Encoding($false)))
        Protect-SecretFile $temporary
        [IO.File]::Move($temporary, $Path)
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Initialize-InstanceSecrets {
    param([Parameter(Mandatory = $true)]$Context)
    Initialize-SecretFile $Context.DatabasePasswordFile
    Initialize-SecretFile $Context.BootstrapAdminPasswordFile
}

function Get-SecretValue {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "密钥文件不存在：$Path"
    }
    $value = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8).Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "密钥文件为空：$Path"
    }
    return $value
}

function Test-PortAvailable {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener -ArgumentList ([Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function ConvertTo-NativeArgumentLine {
    param([string[]]$Arguments = @())
    $quoted = foreach ($argument in $Arguments) {
        $value = [string]$argument
        '"' + $value.Replace('"', '\"') + '"'
    }
    return $quoted -join ' '
}

function Invoke-BundledCommandResult {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory
    )
    $stdout = [IO.Path]::GetTempFileName()
    $stderr = [IO.Path]::GetTempFileName()
    try {
        $parameters = @{
            FilePath = $FilePath
            Wait = $true
            PassThru = $true
            WindowStyle = "Hidden"
            RedirectStandardOutput = $stdout
            RedirectStandardError = $stderr
        }
        $argumentLine = ConvertTo-NativeArgumentLine $Arguments
        if (-not [string]::IsNullOrWhiteSpace($argumentLine)) {
            $parameters.ArgumentList = $argumentLine
        }
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            $parameters.WorkingDirectory = $WorkingDirectory
        }
        $process = Start-Process @parameters
        $standardOutput = [IO.File]::ReadAllText($stdout)
        $standardError = [IO.File]::ReadAllText($stderr)
        return [PSCustomObject]@{
            ExitCode = [int]$process.ExitCode
            StandardOutput = $standardOutput
            StandardError = $standardError
            Output = ($standardOutput + $standardError).Trim()
        }
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-BundledCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory
    )
    $result = Invoke-BundledCommandResult $FilePath $Arguments $WorkingDirectory
    if ($result.ExitCode -ne 0) {
        throw "包内程序执行失败（退出码 $($result.ExitCode)）：$FilePath $($Arguments -join ' ')`n$($result.Output)"
    }
    return $result.Output
}

function Invoke-BundledCommandWithoutCapture {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory
    )
    $process = New-Object Diagnostics.Process
    try {
        $startInfo = New-Object Diagnostics.ProcessStartInfo
        $startInfo.FileName = $FilePath
        $startInfo.Arguments = ConvertTo-NativeArgumentLine $Arguments
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            $startInfo.WorkingDirectory = $WorkingDirectory
        }
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "无法启动包内程序：$FilePath"
        }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "包内程序执行失败（退出码 $($process.ExitCode)）：$FilePath $($Arguments -join ' ')"
        }
    } finally {
        $process.Dispose()
    }
}

function Normalize-DirectoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar)
}

function Test-AsciiPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return $Path -notmatch '[^\x00-\x7F]'
}

function Get-PostgresAliasPath {
    param([Parameter(Mandatory = $true)]$Context)
    if (Test-AsciiPath $Context.Root) {
        return $Context.Root
    }
    $temporaryRoot = Normalize-DirectoryPath ([IO.Path]::GetTempPath())
    if (-not (Test-AsciiPath $temporaryRoot)) {
        throw "Windows TEMP 路径包含非 ASCII 字符，无法为 PostgreSQL 创建兼容路径别名：$temporaryRoot"
    }
    $manifest = Get-Content -LiteralPath $Context.Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $sourceCommit = [string]$manifest.source_commit
    if ($sourceCommit -notmatch '^[0-9A-Fa-f]{12,}$') {
        throw "release-manifest.json 的 source_commit 无法用于 PostgreSQL 路径别名。"
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $rootBytes = [Text.Encoding]::UTF8.GetBytes((Normalize-DirectoryPath $Context.Root).ToLowerInvariant())
        $pathHash = ([BitConverter]::ToString($sha.ComputeHash($rootBytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
    $aliasBase = Join-Path $temporaryRoot "alert-management-native"
    $alias = Join-Path $aliasBase ($sourceCommit.Substring(0, 12).ToLowerInvariant() + "-" +
        $pathHash.Substring(0, 12))
    if (-not (Test-AsciiPath $alias)) {
        throw "PostgreSQL 路径别名不是纯 ASCII：$alias"
    }
    return $alias
}

function Assert-PostgresAliasTarget {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Alias
    )
    $item = Get-Item -LiteralPath $Alias -Force -ErrorAction Stop
    if ($item.LinkType -ne "Junction" -or @($item.Target).Count -ne 1) {
        throw "PostgreSQL 路径别名不是预期的目录 Junction：$Alias"
    }
    $actualTarget = Normalize-DirectoryPath ([string]@($item.Target)[0])
    $expectedTarget = Normalize-DirectoryPath $Context.Root
    if (-not $actualTarget.Equals($expectedTarget, [StringComparison]::OrdinalIgnoreCase)) {
        throw "PostgreSQL 路径别名指向其他目录，拒绝复用：$Alias"
    }
}

function Initialize-PostgresWorkingRoot {
    param([Parameter(Mandatory = $true)]$Context)
    $alias = Get-PostgresAliasPath $Context
    if ((Normalize-DirectoryPath $alias).Equals((Normalize-DirectoryPath $Context.Root),
            [StringComparison]::OrdinalIgnoreCase)) {
        return $Context.Root
    }
    $aliasBase = Split-Path $alias -Parent
    if (Test-Path -LiteralPath $aliasBase) {
        if (-not (Test-Path -LiteralPath $aliasBase -PathType Container)) {
            throw "PostgreSQL 路径别名父路径不是目录：$aliasBase"
        }
    } else {
        New-Item -ItemType Directory -Path $aliasBase | Out-Null
    }
    $probe = Join-Path $aliasBase (".write-test-" + [Guid]::NewGuid().ToString("N"))
    try {
        [IO.File]::WriteAllText($probe, "ok", (New-Object Text.UTF8Encoding($false)))
    } finally {
        if (Test-Path -LiteralPath $probe) {
            Remove-Item -LiteralPath $probe -Force
        }
    }
    if (Test-Path -LiteralPath $alias) {
        Assert-PostgresAliasTarget $Context $alias
    } else {
        New-Item -ItemType Junction -Path $alias -Target $Context.Root | Out-Null
        Assert-PostgresAliasTarget $Context $alias
    }
    return $alias
}

function Remove-PostgresWorkingRoot {
    param([Parameter(Mandatory = $true)]$Context)
    $alias = Get-PostgresAliasPath $Context
    if ((Normalize-DirectoryPath $alias).Equals((Normalize-DirectoryPath $Context.Root),
            [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $alias)) {
        return
    }
    Assert-PostgresAliasTarget $Context $alias
    [IO.Directory]::Delete($alias, $false)
    if (Test-Path -LiteralPath $alias) {
        throw "未能删除 PostgreSQL 路径别名：$alias"
    }
    $aliasBase = Split-Path $alias -Parent
    if ((Test-Path -LiteralPath $aliasBase -PathType Container) -and
            @(Get-ChildItem -LiteralPath $aliasBase -Force).Count -eq 0) {
        [IO.Directory]::Delete($aliasBase, $false)
    }
}

function Start-BundledProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StandardOutput,
        [Parameter(Mandatory = $true)][string]$StandardError,
        [hashtable]$Environment = @{}
    )
    $previous = @{}
    foreach ($name in $Environment.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$Environment[$name], "Process")
    }
    try {
        $parameters = @{
            FilePath = $FilePath
            WorkingDirectory = $WorkingDirectory
            RedirectStandardOutput = $StandardOutput
            RedirectStandardError = $StandardError
            PassThru = $true
            WindowStyle = "Hidden"
        }
        if (-not [string]::IsNullOrWhiteSpace($Arguments)) {
            $parameters.ArgumentList = $Arguments
        }
        return Start-Process @parameters
    } finally {
        foreach ($name in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
        }
    }
}

function Get-CimProcess {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
}

function Write-PidRecord {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Executable,
        [string]$CommandLine,
        [string[]]$Logs = @(),
        [string]$WorkingDirectory
    )
    $record = [ordered]@{
        pid = $ProcessId
        executable_path = [IO.Path]::GetFullPath($Executable)
        command_line = $CommandLine
        working_directory = $WorkingDirectory
        release_root = $Context.Root
        started_at = [DateTimeOffset]::UtcNow.ToString("o")
        logs = $Logs
    }
    $path = Join-Path $Context.Pids ($Name + ".json")
    $record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path -Encoding UTF8
}

function Assert-OwnedProcess {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$ExpectedExecutable,
        [string]$RequiredCommandText
    )
    $recordPath = Join-Path $Context.Pids ($Name + ".json")
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        throw "$Name 的 PID 身份记录不存在，请先执行 scripts\start.ps1。"
    }
    $record = Get-OwnedPidRecord $Context $Name
    $processId = [int]$record.pid
    $process = Get-CimProcess $processId
    if ($null -eq $process) {
        throw "$Name 的 PID 身份记录已过期，请先执行 scripts\stop.ps1 再重新启动。"
    }
    $actualExecutable = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
    $expectedMatch = @($ExpectedExecutable | Where-Object {
            $actualExecutable.Equals([IO.Path]::GetFullPath($_), [StringComparison]::OrdinalIgnoreCase)
        }).Count -gt 0
    if (-not $expectedMatch) {
        throw "$Name 的 PID 已被复用，实际可执行文件不属于当前发布包。"
    }
    if (-not (Test-PidRecordProcessMatch $record $process)) {
        throw "$Name 的实际进程与当前发布实例记录不一致。"
    }
    if (-not [string]::IsNullOrWhiteSpace($RequiredCommandText) -and
            ([string]$process.CommandLine).IndexOf($RequiredCommandText, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "$Name 的命令行身份与当前发布包不一致。"
    }
    return $process
}

function Get-OwnedPidRecord {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $recordPath = Join-Path $Context.Pids ($Name + ".json")
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        throw "$Name 的 PID 身份记录不存在，请先执行 scripts\start.ps1。"
    }
    $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not (Normalize-DirectoryPath ([string]$record.release_root)).Equals(
            (Normalize-DirectoryPath $Context.Root), [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name 的 PID 身份记录不属于当前发布实例。"
    }
    $processId = 0
    if (-not [int]::TryParse([string]$record.pid, [ref]$processId) -or $processId -le 0 -or
            [string]::IsNullOrWhiteSpace([string]$record.executable_path) -or
            [string]::IsNullOrWhiteSpace([string]$record.command_line)) {
        throw "$Name 的 PID 身份记录字段无效，拒绝操作。"
    }
    return $record
}

function Test-PidRecordProcessMatch {
    param(
        [Parameter(Mandatory = $true)]$Record,
        [Parameter(Mandatory = $true)]$Process
    )
    if ($null -eq $Process -or [string]::IsNullOrWhiteSpace([string]$Process.ExecutablePath)) {
        return $false
    }
    return ([IO.Path]::GetFullPath([string]$Process.ExecutablePath)).Equals(
            [IO.Path]::GetFullPath([string]$Record.executable_path),
            [StringComparison]::OrdinalIgnoreCase) -and
        ([string]$Process.CommandLine).Trim().Equals(
            ([string]$Record.command_line).Trim(), [StringComparison]::Ordinal)
}

function Remove-PidRecord {
    param([Parameter(Mandatory = $true)]$Context, [Parameter(Mandatory = $true)][string]$Name)
    $path = Join-Path $Context.Pids ($Name + ".json")
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

function Stop-OwnedProcess {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$ExpectedExecutable,
        [string]$RequiredCommandText
    )
    $recordPath = Join-Path $Context.Pids ($Name + ".json")
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        return
    }
    $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq (Get-CimProcess ([int]$record.pid))) {
        Remove-Item -LiteralPath $recordPath -Force
        return
    }
    $process = Assert-OwnedProcess $Context $Name $ExpectedExecutable $RequiredCommandText
    $processId = [int]$process.ProcessId
    Stop-Process -Id $processId -Force
    try {
        Wait-Process -Id $processId -Timeout 15 -ErrorAction Stop
    } catch {
        if ($null -ne (Get-CimProcess $processId)) {
            throw "停止 $Name 超时，PID：$processId"
        }
    }
    Remove-Item -LiteralPath $recordPath -Force
}

function Wait-JsonHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][scriptblock]$Accept,
        [int]$TimeoutSeconds = 90,
        [string]$Name = "服务"
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = "尚未收到响应"
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $body = Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5 -UseBasicParsing
            if (& $Accept $body) {
                return $body
            }
            $lastError = "健康响应未达到 UP"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }
    throw "$Name 健康等待超时：$lastError"
}

function Get-PostgresExecutable {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$WorkingRoot
    )
    if ([string]::IsNullOrWhiteSpace($WorkingRoot)) {
        return Join-Path $Context.PgBin ($Name + ".exe")
    }
    return Join-Path $WorkingRoot ("runtime\postgresql\bin\" + $Name + ".exe")
}

function Get-PostgresExpectedExecutables {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$WorkingRoot,
        [Parameter(Mandatory = $true)][string]$Name
    )
    return @(
        (Get-PostgresExecutable $Context $Name $WorkingRoot),
        (Get-PostgresExecutable $Context $Name)
    ) | Select-Object -Unique
}
