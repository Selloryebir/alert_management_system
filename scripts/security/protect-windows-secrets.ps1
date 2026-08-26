param(
    [Parameter(Mandatory = $true)][string]$SecretRoot
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$resolvedRoot = [IO.Path]::GetFullPath($SecretRoot)
if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
    throw "密钥目录不存在：$resolvedRoot"
}

$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
$allow = [Security.AccessControl.AccessControlType]::Allow

$directoryAcl = New-Object Security.AccessControl.DirectorySecurity
$directoryAcl.SetAccessRuleProtection($true, $false)
foreach ($sid in @($currentSid, $systemSid)) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit),
        [Security.AccessControl.PropagationFlags]::None,
        $allow)
    [void]$directoryAcl.AddAccessRule($rule)
}
[IO.Directory]::SetAccessControl($resolvedRoot, $directoryAcl)

foreach ($file in Get-ChildItem -LiteralPath $resolvedRoot -File -Force) {
    $fileAcl = New-Object Security.AccessControl.FileSecurity
    $fileAcl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($currentSid, $systemSid)) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, [Security.AccessControl.FileSystemRights]::FullControl, $allow)
        [void]$fileAcl.AddAccessRule($rule)
    }
    [IO.File]::SetAccessControl($file.FullName, $fileAcl)
}

$allowed = @($currentSid.Value, $systemSid.Value)
$protectedPaths = @($resolvedRoot)
$protectedPaths += @(Get-ChildItem -LiteralPath $resolvedRoot -File -Force |
    ForEach-Object { $_.FullName })
foreach ($path in $protectedPaths) {
    $acl = Get-Acl -LiteralPath $path
    if (-not $acl.AreAccessRulesProtected) {
        throw "密钥路径仍继承上级权限：$path"
    }
    $unexpected = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) |
        Where-Object { $_.AccessControlType -eq $allow -and $_.IdentityReference.Value -notin $allowed })
    if ($unexpected.Count -ne 0) {
        throw "密钥路径仍允许其他身份访问：$path"
    }
}
