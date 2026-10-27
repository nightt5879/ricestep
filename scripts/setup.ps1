#requires -Version 7.0
[CmdletBinding()]
param()
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
Expand-Archive -LiteralPath $archive -DestinationPath $toolsDirectory -Force
Write-Host "SDK $($Toolchain.sdk.tag) verified and installed: $(Get-SimulatorPath)"
