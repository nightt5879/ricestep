#requires -Version 7.0
[CmdletBinding()]
param([switch]$SdkOnly)
. (Join-Path $PSScriptRoot 'common.ps1')

if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'This lock file currently targets Windows x64.'
}
$null = Get-Command gh -ErrorAction Stop
$cache = Join-Path $RepoRoot '.cache/downloads'
$toolsDirectory = Join-Path $RepoRoot '.tools'
$null = New-Item -ItemType Directory -Path $cache, $toolsDirectory -Force
$archive = Join-Path $cache $Toolchain.sdk.asset
if (-not (Test-Path -LiteralPath $archive)) {
    & gh release download $Toolchain.sdk.tag --repo $Toolchain.sdk.repository --pattern $Toolchain.sdk.asset --dir $cache
    if ($LASTEXITCODE -ne 0) { throw 'SDK download failed.' }
}
$actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $Toolchain.sdk.sha256) {
    throw 'SDK archive checksum mismatch. No downloaded executable was run.'
}
Expand-ToolArchive -Archive $archive -Destination $toolsDirectory
Write-Host "SDK $($Toolchain.sdk.tag) verified and installed: $(Get-SimulatorPath)"

if (-not $SdkOnly) {
    $adbArchive = Join-Path $cache $Toolchain.adb.asset
    if (-not (Test-Path -LiteralPath $adbArchive)) {
        Invoke-WebRequest -Uri $Toolchain.adb.url -OutFile $adbArchive -TimeoutSec 60
    }
    $adbHash = (Get-FileHash -LiteralPath $adbArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    $adbSha1 = (Get-FileHash -LiteralPath $adbArchive -Algorithm SHA1).Hash.ToLowerInvariant()
    if ($adbHash -ne $Toolchain.adb.sha256 -or $adbSha1 -ne $Toolchain.adb.upstreamSha1) {
        throw 'ADB archive checksum mismatch. No downloaded executable was run.'
    }
    Expand-ToolArchive -Archive $adbArchive -Destination $toolsDirectory
    $adb = Get-AdbPath
    & $adb version
    if ($LASTEXITCODE -ne 0) { throw 'ADB failed to start.' }
    Write-Host "Android Platform Tools $($Toolchain.adb.version) verified and installed."
}
