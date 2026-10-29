#requires -Version 7.4
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$VerifyArchivesOnly
)
. (Join-Path $PSScriptRoot 'common.ps1')

if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'The native tool lock targets Windows x64.'
}
if ($CheckOnly -and $VerifyArchivesOnly) { throw 'Choose only one verification mode.' }
$nativeLock = Get-Content -LiteralPath (Join-Path $RepoRoot 'toolchain.native.lock.json') -Raw | ConvertFrom-Json
if ($nativeLock.schemaVersion -ne 1 -or $nativeLock.platform -ne 'windows-x64') {
    throw 'Unsupported native tool lock.'
}

function Resolve-NativeCachePath([string]$RelativePath) {
    $cacheRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot '.cache')) + [IO.Path]::DirectorySeparatorChar
    $resolved = [IO.Path]::GetFullPath((Join-Path $RepoRoot $RelativePath))
    if (-not $resolved.StartsWith($cacheRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Native tool path must stay inside the repository cache: $RelativePath"
    }
    return $resolved
}

function Assert-NativeArchive($Package, [string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing verified archive: $Path" }
    if ((Get-Item -LiteralPath $Path).Length -ne $Package.bytes -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Package.sha256) {
        throw "Archive size/SHA-256 mismatch: $Path. Nothing from this archive was extracted or executed."
    }
}

function Get-NativeArchive($Package) {
    $archive = Resolve-NativeCachePath $Package.archive
    if ($Package.sha256 -notmatch '^[0-9a-f]{64}$' -or $Package.bytes -le 0 -or
        ([Uri]$Package.url).Scheme -ne 'https') { throw 'Invalid pinned archive metadata.' }
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        if ($CheckOnly -or $VerifyArchivesOnly) { throw "Missing archive: $archive. Run scripts/setup-native.ps1 to fetch it." }
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $archive) -Force
        $partial = "$archive.partial"
        $complete = (Test-Path -LiteralPath $partial -PathType Leaf) -and
                    (Get-Item -LiteralPath $partial).Length -eq $Package.bytes
        if (-not $complete) {
            if ((Test-Path -LiteralPath $partial -PathType Leaf) -and
                (Get-Item -LiteralPath $partial).Length -gt $Package.bytes) {
                throw "Partial download exceeds its pinned size: $partial"
            }
            $null = Get-Command curl.exe -ErrorAction Stop
            # Keep TLS verification enabled; resume only the matching pinned URL.
            & curl.exe --fail --location --http1.1 --proto '=https' --proto-redir '=https' --max-redirs 5 `
                --retry 3 --retry-all-errors --retry-delay 1 --retry-max-time 150 `
                --connect-timeout 15 --max-time 180 --continue-at - --output $partial $Package.url
            if ($LASTEXITCODE -ne 0) { throw "Download failed; partial bytes retained for retry: $partial" }
        }
        Assert-NativeArchive $Package $partial
        Move-Item -LiteralPath $partial -Destination $archive
    }
    Assert-NativeArchive $Package $archive
    return $archive
}

function Assert-NativeToolFiles($Package, [string]$Archive, [string]$Destination) {
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($relative in $Package.requiredFiles) {
            $entry = $zip.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq $relative } | Select-Object -First 1
            if ($null -eq $entry) { throw "Pinned ZIP lacks required file: $relative" }
            $installed = Join-Path $Destination $relative
            if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Native tool is not installed: $installed" }
            $stream = $entry.Open()
            try { $expected = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream)) }
            finally { $stream.Dispose() }
            if ((Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash -ne $expected) {
                throw "Installed file differs from the verified archive: $installed"
            }
        }
    } finally { $zip.Dispose() }
}

function Expand-NativeLua([string]$Archive, [string]$Destination, [bool]$VerifyOnly) {
    $parent = Split-Path -Parent $Destination
    $root = [IO.Path]::GetFullPath($Destination)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $file = [IO.File]::OpenRead($Archive)
    $gzip = [IO.Compression.GZipStream]::new($file, [IO.Compression.CompressionMode]::Decompress)
    $reader = [System.Formats.Tar.TarReader]::new($gzip)
    $regularCount = 0
    try {
        while ($null -ne ($entry = $reader.GetNextEntry())) {
            $name = $entry.Name.Replace('\', '/')
            if ($name.StartsWith('/') -or $name.Contains(':') -or $name -match '(^|/)\.\.(/|$)' -or
                ($name -ne $nativeLock.lua.archiveRoot -and -not $name.StartsWith("$($nativeLock.lua.archiveRoot)/"))) {
                throw "Unsafe Lua archive path: $name"
            }
            $target = [IO.Path]::GetFullPath((Join-Path $parent $name))
            if ($target -ne $root -and -not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Lua entry escaped its source directory: $name"
            }
            # Reject existing links as well as links encoded in the TAR.
            $ancestor = $target
            while ($ancestor -and $ancestor -ne $parent) {
                if ((Test-Path -LiteralPath $ancestor) -and
                    ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Lua extraction cannot traverse a filesystem link: $ancestor"
                }
                $ancestor = Split-Path -Parent $ancestor
            }
            if ($entry.EntryType -eq [System.Formats.Tar.TarEntryType]::Directory) {
                if (-not $VerifyOnly) { $null = New-Item -ItemType Directory -Path $target -Force }
                continue
            }
            if ($entry.EntryType -notin @([System.Formats.Tar.TarEntryType]::RegularFile,
                                        [System.Formats.Tar.TarEntryType]::V7RegularFile)) {
                throw "Unsupported Lua TAR entry type: $($entry.EntryType)"
            }
            $buffer = [IO.MemoryStream]::new()
            try {
                $entry.DataStream.CopyTo($buffer)
                $bytes = $buffer.ToArray()
            } finally { $buffer.Dispose() }
            $expected = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
            $matches = (Test-Path -LiteralPath $target -PathType Leaf) -and
                       (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $expected
            if (-not $matches) {
                if ($VerifyOnly) { throw "Lua file missing or modified: $target" }
                $null = New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force
                [IO.File]::WriteAllBytes($target, $bytes)
            }
            $regularCount++
        }
        if ($regularCount -eq 0 -or -not (Test-Path -LiteralPath (Join-Path $root 'src/lua.h') -PathType Leaf)) {
            throw 'Lua archive did not provide its expected source files.'
        }
    } finally { $reader.Dispose(); $gzip.Dispose(); $file.Dispose() }
}

# Verify every complete archive before extracting any package.
$nativeArchives = @{}
foreach ($package in $nativeLock.packages) { $nativeArchives[$package.id] = Get-NativeArchive $package }
$luaArchive = Get-NativeArchive $nativeLock.lua
if ($VerifyArchivesOnly) {
    Write-Host 'All three native archives match their pinned sizes and SHA-256 hashes. No installation changed.'
    return
}

$nativeTools = Resolve-NativeCachePath $nativeLock.toolsDirectory
$luaDirectory = Resolve-NativeCachePath $nativeLock.lua.directory
foreach ($package in $nativeLock.packages) {
    if (-not $CheckOnly) { Expand-ToolArchive -Archive $nativeArchives[$package.id] -Destination $nativeTools }
    Assert-NativeToolFiles $package $nativeArchives[$package.id] $nativeTools
}
Expand-NativeLua -Archive $luaArchive -Destination $luaDirectory -VerifyOnly ([bool]$CheckOnly)
if ((Get-Content -LiteralPath (Join-Path $nativeTools 'listenai-tools/VERSION') -Raw).Trim() -ne 'v0.1.0') {
    throw 'Unexpected ListenAI tools VERSION.'
}
Write-Host 'Native archive hashes, required installed tools and all Lua archive files verified. No downloaded tool was executed.'
[pscustomobject]@{
    ToolsRoot = $nativeTools
    Gcc = Join-Path $nativeTools 'gcc/bin/riscv64-unknown-elf-gcc.exe'
    CMake = Join-Path $nativeTools 'listenai-tools/cmake/bin/cmake.exe'
    Ninja = Join-Path $nativeTools 'listenai-tools/ninja/ninja.exe'
    LuaSource = Resolve-NativeCachePath $nativeLock.lua.sourceDirectory
    CheckedOnly = [bool]$CheckOnly
}
