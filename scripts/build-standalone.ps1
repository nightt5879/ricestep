#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$SdkRoot,
    [string]$ToolsRoot,
    [string]$LuaSource,
    [ValidatePattern('^build-[a-zA-Z0-9_-]+$')]
    [string]$BuildName
)
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $IsWindows) { throw 'This wrapper requires Windows and its verified native toolchain.' }

function Get-NativeSubstMappings {
    $mappings = @{}
    foreach ($line in (& subst.exe)) {
        if ($line -match '^(?<drive>[A-Za-z]):\\:\s*=>\s*(?<target>.+)$') {
            $mappings[$Matches.drive.ToUpperInvariant()] = [IO.Path]::GetFullPath($Matches.target).TrimEnd('\')
        }
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing SUBST mappings.' }
    return $mappings
}

$customPaths = ($SdkRoot -or $ToolsRoot -or $LuaSource)
if ($customPaths -and (-not $SdkRoot -or -not $ToolsRoot -or -not $LuaSource)) {
    throw 'When overriding paths, supply all three short paths: -SdkRoot R:/native-mini3-sdk -ToolsRoot R:/native-listenai-tools -LuaSource R:/lua-5.4.8/src. Caller-owned mappings are never changed.'
}
$cacheRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot '.cache')).TrimEnd('\')
$ownedDrive = $null
$locationPushed = $false
$envNames = @('LISTENAI_HOME', 'ARCS_BASE', 'LISTENAI_TOOLS_PATH', 'NUCLEI_TOOLCHAIN_PATH', 'PATH')
$previous = @{}
foreach ($name in $envNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    if (-not $customPaths) {
        if (-not (Test-Path -LiteralPath $cacheRoot -PathType Container)) {
            throw 'Native cache missing. Run scripts/setup-native.ps1 and prepare the pinned SDK checkout first.'
        }
        $existingMappings = Get-NativeSubstMappings
        $occupied = @([IO.Directory]::GetLogicalDrives() | ForEach-Object { $_.Substring(0, 1).ToUpperInvariant() })
        $occupied += @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name.ToUpperInvariant() })
        # Never reuse or remove a pre-existing drive, even when it targets this cache.
        foreach ($drive in @('R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', 'Q', 'P', 'O', 'N', 'M', 'L', 'K', 'J', 'I', 'H', 'G', 'F', 'E', 'D')) {
            if ($drive -in $occupied -or $existingMappings.ContainsKey($drive)) { continue }
            $mappingOutput = & subst.exe "${drive}:" $cacheRoot 2>&1
            if ($LASTEXITCODE -ne 0) { continue }
            $ownedDrive = $drive
            $shortRoot = "${drive}:\"
            $SdkRoot = Join-Path $shortRoot 'native-mini3-sdk'
            $ToolsRoot = Join-Path $shortRoot 'native-listenai-tools'
            $LuaSource = Join-Path $shortRoot 'lua-5.4.8/src'
            Write-Host "Temporary native build path: ${drive}: -> $cacheRoot"
            break
        }
        if (-not $ownedDrive) { throw 'No unused drive is available for the short native build path. Supply all three paths on an existing short checkout/mapping.' }
    }
    $SdkRoot = [IO.Path]::GetFullPath($SdkRoot).TrimEnd('\')
    $ToolsRoot = [IO.Path]::GetFullPath($ToolsRoot).TrimEnd('\')
    $LuaSource = [IO.Path]::GetFullPath($LuaSource).TrimEnd('\')
    foreach ($path in @($SdkRoot, $ToolsRoot, $LuaSource)) {
        # LongPathsEnabled does not remove CreateProcess's 32 KiB command limit.
        # ASCII/space checks also prevent upstream env.ps1 from remapping R:.
        if ($path.Length -gt 40 -or $path -notmatch '^[A-Za-z]:\\' -or $path -cmatch '[\s\x80-\uFFFF]' -or $path -match '(?i)(^|\\)OneDrive($|\\| - )') {
            throw "Native paths must be short local paths (at most 40 characters, ASCII, no spaces/OneDrive): $path. Omit all three path parameters for automatic SUBST, or supply all three paths on your own short mapping."
        }
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Native source/tool directory missing: $path" }
    }
    foreach ($required in @('gcc/bin/riscv64-unknown-elf-gcc.exe', 'listenai-tools/cmake/bin/cmake.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $ToolsRoot $required) -PathType Leaf)) {
            throw "Native tool missing: $required. Run scripts/setup-native.ps1 before building."
        }
    }
    # Include all visible roots in the cache identity: a different free drive or
    # custom tool path must not reuse a CMake cache containing old absolute paths.
    $pathIdentity = (@($SdkRoot, $ToolsRoot, $LuaSource) -join "`n").ToLowerInvariant()
    $pathHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($pathIdentity))).Substring(0, 8).ToLowerInvariant()
    if (-not $BuildName) { $BuildName = "build-standalone-$pathHash" }
    $buildDirectory = Join-Path $SdkRoot $BuildName
    & node (Join-Path $PSScriptRoot 'prepare-standalone.cjs') --sdk $SdkRoot --lua-source $LuaSource --apply
    if ($LASTEXITCODE -ne 0) { throw 'Standalone source preparation failed.' }
    Push-Location $SdkRoot
    $locationPushed = $true
    $env:LISTENAI_HOME = $ToolsRoot
    $env:LISTENAI_TOOLS_PATH = Join-Path $ToolsRoot 'listenai-tools'
    $env:NUCLEI_TOOLCHAIN_PATH = Join-Path $ToolsRoot 'gcc'
    & (Join-Path $SdkRoot 'arcs-sdk/env.ps1') check
    & (Join-Path $SdkRoot 'build.ps1') -S (Join-Path $SdkRoot 'apps/arcs-mini') -B $buildDirectory -DBOARD=arcs_mini3
    if ($LASTEXITCODE -ne 0) { throw 'Standalone Mini3 native compilation failed.' }
    $binary = Join-Path $buildDirectory 'arcs-mini.bin'
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'Build produced no CP firmware binary.' }
    # The reviewed Mini3 partition table reserves 0x600000..0xA00000 for CP.
    $size = (Get-Item -LiteralPath $binary).Length
    if ($size -le 0 -or $size -gt 0x400000) { throw 'CP firmware does not fit the reviewed 4 MiB app partition.' }
    $output = Join-Path $RepoRoot 'dist/native/standalone'
    $null = New-Item -ItemType Directory -Path $output -Force
    $elf = Join-Path $buildDirectory 'arcs-mini'
    & node (Join-Path $PSScriptRoot 'verify-native-image.cjs') --elf $elf --bin $binary --manifest (Join-Path $SdkRoot 'apps/arcs-mini/standalone/overlay-manifest.json') --report (Join-Path $output 'image-verification.json')
    if ($LASTEXITCODE -ne 0) { throw 'Native image integrity or Flash placement verification failed.' }
    Copy-Item -LiteralPath $binary -Destination (Join-Path $output 'arcs-mini3-ricestep.bin') -Force
    Copy-Item -LiteralPath $elf -Destination (Join-Path $output 'arcs-mini3-ricestep.elf') -Force
    Copy-Item -LiteralPath (Join-Path $SdkRoot 'apps/arcs-mini/standalone/overlay-manifest.json') -Destination $output -Force
    foreach ($name in @('arcs-mini.map', '.config')) {
        $file = Join-Path $buildDirectory $name
        if (Test-Path -LiteralPath $file -PathType Leaf) { Copy-Item -LiteralPath $file -Destination $output -Force }
    }
    [ordered]@{
        board = 'arcs_mini3'; sdkCommit = 'c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35'
        bytes = $size; sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
        flashOffset = '0x600000'; partitionBytes = 0x400000
        buildDirectory = $buildDirectory; nativePathIdentity = $pathHash
        verification = 'compiled; ELF/bin voice and Lua integrity, ROM placement and startup scatter tables verified; hardware untested'
        flashed = $false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'build-info.json') -Encoding utf8
    Write-Host 'Standalone firmware compiled and exported. No device was flashed.'
} finally {
    if ($locationPushed) { Pop-Location }
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    if ($ownedDrive) {
        # Recheck ownership so a mapping changed externally is never removed.
        try {
            $mappings = Get-NativeSubstMappings
            if ($mappings.ContainsKey($ownedDrive) -and $mappings[$ownedDrive].Equals($cacheRoot, [StringComparison]::OrdinalIgnoreCase)) {
                & subst.exe "${ownedDrive}:" /D
                if ($LASTEXITCODE -ne 0) { Write-Warning "Could not release the temporary ${ownedDrive}: mapping." }
            } else {
                Write-Warning "Temporary ${ownedDrive}: mapping changed or disappeared; leaving current mappings untouched."
            }
        } catch { Write-Warning "Could not verify temporary mapping ownership; no mapping removed: $_" }
    }
}
