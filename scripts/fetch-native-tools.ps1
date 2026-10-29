#requires -Version 7.2
[CmdletBinding()]
param([string]$Destination = '.cache/native-tool-download')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($Destination)
$null = New-Item -ItemType Directory -Path $root -Force
# URLs are the Windows packages declared by the reviewed ARCS SDK env.ps1.
$packages = @(
    @{ name = 'listenai-tools-windows-v0.1.0.zip'; url = 'https://listenai-firmware-delivery.oss-cn-beijing.aliyuncs.com/ARCS/tools/dev-tools/windows-amd64/v0.1.0/listenai-tools-windows-v0.1.0.zip'; required = 'listenai-tools/cmake/bin/cmake.exe' },
    @{ name = 'nuclei_riscv_newlibc_prebuilt_win64_2025.10_slim.zip'; url = 'https://listenai-firmware-delivery.oss-cn-beijing.aliyuncs.com/ARCS/tools/toolchain/windows-amd64/nuclei_riscv_newlibc_prebuilt_win64_2025.10_slim.zip'; required = 'gcc/bin/riscv64-unknown-elf-gcc.exe' }
)
$records = @()
foreach ($package in $packages) {
    $target = Join-Path $root $package.name
    $partial = "$target.partial"
    & curl.exe --fail --location --retry 2 --connect-timeout 20 --max-time 900 --output $partial $package.url
    if ($LASTEXITCODE -ne 0) { throw "Official tool download failed: $($package.name)" }
    $zip = [IO.Compression.ZipFile]::OpenRead($partial)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
        foreach ($name in $names) {
            if ($name.StartsWith('/') -or $name -match '(^|/)\.\.(/|$)' -or $name.Contains(':')) {
                throw "Unsafe archive entry in $($package.name)"
            }
        }
        if ($package.required -notin $names) { throw "Unexpected archive layout: $($package.name)" }
    } finally { $zip.Dispose() }
    Move-Item -LiteralPath $partial -Destination $target -Force
    $records += [ordered]@{
        file = $package.name; source = $package.url
        bytes = (Get-Item -LiteralPath $target).Length
        sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
[ordered]@{ sdk_snapshot = 'c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35'; packages = $records } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'download-manifest.json') -Encoding utf8
Write-Host 'Official native tools downloaded and ZIP layouts checked; no tools executed or installed.'
