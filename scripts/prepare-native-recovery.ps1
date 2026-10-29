#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$PackagePath,
    [switch]$CheckOnly
)
# Offline preparation only: no ADB, serial, network, firmware execution or flash commands.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$packageName = 'arcs-mini3-v3.1.0-0-g0d199e4b.lpk'
$packageBytes = 4965689
$packageSha256 = '6092464a094def4bab3dae01ce40c7f44dca1eb72a91e7de75e80292a6efe07e'
if (-not $PackagePath) { $PackagePath = Join-Path $repoRoot "../../work/lisem/firmware/$packageName" }
$PackagePath = [IO.Path]::GetFullPath($PackagePath)
$cacheRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '.cache'))
$destination = Join-Path $cacheRoot 'stock-recovery-mini3'

# This exact package uses manifest v2 / chip arcs, flat ZIP entries and ./file
# references, as produced by the reviewed SDK tools/make_package.py:129-157.
$expected = [ordered]@{
    boot =      @{ file = 'boot.bin';      addr = '0x000000'; bytes = 222604 }
    ap =        @{ file = 'ap.bin';        addr = '0x040000'; bytes = 438380 }
    tone =      @{ file = 'tone.bin';      addr = '0x100000'; bytes = 351232 }
    wake_word = @{ file = 'wake_word.bin'; addr = '0x200000'; bytes = 1295360 }
    emoji =     @{ file = 'emoji.bin';    addr = '0x380000'; bytes = 492544 }
    respak =    @{ file = 'respak.bin';   addr = '0x440000'; bytes = 983040 }
    app =       @{ file = 'arcs-mini.bin'; addr = '0x600000'; bytes = 3346892 }
}
$payloads = [ordered]@{}
$verifiedImages = [Collections.Generic.List[object]]::new()
$partitionImages = [Collections.Generic.List[object]]::new()

function Get-BytesHash([byte[]]$Bytes, [ValidateSet('SHA256', 'MD5')][string]$Algorithm) {
    $digest = if ($Algorithm -eq 'MD5') { [Security.Cryptography.MD5]::HashData($Bytes) }
              else { [Security.Cryptography.SHA256]::HashData($Bytes) }
    return [Convert]::ToHexString($digest).ToLowerInvariant()
}

function Assert-OrdinaryPath([string]$Path, [bool]$Directory) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.PSIsContainer -ne $Directory) {
        throw "Recovery output must use ordinary files/directories, without links: $Path"
    }
}

if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) { throw "Pinned local recovery package missing: $PackagePath. This script does not download firmware." }
$archiveFile = [IO.File]::Open($PackagePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
$zip = $null
try {
    $actualSha256 = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($archiveFile)).ToLowerInvariant()
    if ($archiveFile.Length -ne $packageBytes -or $actualSha256 -ne $packageSha256) {
        throw 'Recovery package size/SHA-256 mismatch. No output was written.'
    }
    $archiveFile.Position = 0
    $zip = [IO.Compression.ZipArchive]::new($archiveFile, [IO.Compression.ZipArchiveMode]::Read, $true)
    $entries = @{}
    $allowedFiles = @('manifest.json') + @($expected.Values | ForEach-Object { $_.file })
    foreach ($entry in $zip.Entries) {
        $name = $entry.FullName
        $unixType = ($entry.ExternalAttributes -shr 16) -band 0xF000
        if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $name -cnotin $allowedFiles -or
            $entries.ContainsKey($name) -or $unixType -eq 0xA000) {
            throw "Unexpected, duplicate or unsafe archive entry: $name"
        }
        $limit = if ($name -eq 'manifest.json') { 8192 } else { 4194304 }
        if ($entry.Length -le 0 -or $entry.Length -gt $limit) { throw "Invalid archive entry size: $name" }
        $entries[$name] = $entry
    }
    if ($entries.Count -ne 8) { throw 'Pinned recovery archive must contain manifest.json and exactly seven images.' }
    foreach ($name in $allowedFiles) {
        if (-not $entries.ContainsKey($name)) { throw "Recovery archive entry missing: $name" }
        $stream = $entries[$name].Open()
        $buffer = [IO.MemoryStream]::new()
        try { $stream.CopyTo($buffer); $payloads[$name] = $buffer.ToArray() }
        finally { $stream.Dispose(); $buffer.Dispose() }
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $manifest = $utf8.GetString($payloads['manifest.json']) | ConvertFrom-Json -AsHashtable
    if ($manifest.manifest -ne 2 -or $manifest.chip -cne 'arcs' -or $manifest.images -isnot [array] -or $manifest.images.Count -ne 7) {
        throw 'Unexpected recovery manifest schema, chip or image count.'
    }
    $seen = @{}
    foreach ($image in $manifest.images) {
        $name = [string]$image.name
        if ($name -cnotin $expected.Keys -or $seen.ContainsKey($name)) { throw "Unknown or duplicate manifest image: $name" }
        $seen[$name] = $true
        $pin = $expected[$name]
        if ($image.file -cne "./$($pin.file)" -or $image.addr -cne $pin.addr -or $image.md5 -cnotmatch '^[0-9a-f]{32}$') {
            throw "Unexpected file path, address or MD5 for $name"
        }
        [byte[]]$bytes = $payloads[$pin.file]
        $md5 = Get-BytesHash $bytes 'MD5'
        if ($bytes.Length -ne $pin.bytes -or $md5 -cne $image.md5) { throw "Recovery image size/MD5 mismatch: $name" }
        $verifiedImages.Add([ordered]@{
            name = $name; file = $pin.file; addr = $pin.addr; bytes = $bytes.Length
            md5 = $md5; sha256 = (Get-BytesHash $bytes 'SHA256'); includedInRestoreTable = ($name -ne 'boot')
        })
        if ($name -ne 'boot') {
            $partitionImages.Add([ordered]@{ name = $name; addr = $pin.addr; file = $pin.file; tags = @('lpk') })
        }
    }
} finally {
    if ($null -ne $zip) { $zip.Dispose() }
    $archiveFile.Dispose()
}

# There is no boot/KV/boot_control/OTA entry, even if a later caller selects
# the upstream script's boot mode. boot.bin is retained only as verified material.
$table = [ordered]@{ flash_base = '0x30000000'; images = $partitionImages.ToArray(); lpk = @{} }
$report = [ordered]@{
    schemaVersion = 1; board = 'arcs_mini3'; stockVersion = '3.1.0-0-g0d199e4b'
    package = $packageName; packageBytes = $packageBytes; packageSha256 = $packageSha256
    manifestVersion = 2; images = $verifiedImages.ToArray()
    excludedPartitions = @('boot', 'boot_control', 'ota', 'kv')
    status = 'prepared-only'; deviceAccessed = $false; flashed = $false
}
$payloads['partition_table.json'] = [Text.Encoding]::UTF8.GetBytes(($table | ConvertTo-Json -Depth 8) + "`n")
$payloads['verification.json'] = [Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json -Depth 8) + "`n")

Assert-OrdinaryPath $cacheRoot $true
Assert-OrdinaryPath $destination $true
$prefix = [IO.Path]::GetFullPath($destination) + [IO.Path]::DirectorySeparatorChar
# Preflight every existing output before writing anything; never replace edits.
foreach ($name in $payloads.Keys) {
    $target = [IO.Path]::GetFullPath((Join-Path $destination $name))
    if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Output path escaped recovery directory: $name" }
    Assert-OrdinaryPath $target $false
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne (Get-BytesHash $payloads[$name] 'SHA256')) {
            throw "Recovery output differs from verified bytes; refusing to overwrite: $target"
        }
    } elseif ($CheckOnly) { throw "Prepared recovery output missing: $target" }
}
if (-not $CheckOnly) {
    $null = New-Item -ItemType Directory -Path $destination -Force
    foreach ($name in $payloads.Keys) {
        $target = Join-Path $destination $name
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $file = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try { $file.Write($payloads[$name], 0, $payloads[$name].Length) }
            finally { $file.Dispose() }
        }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne (Get-BytesHash $payloads[$name] 'SHA256')) {
            throw "Recovery output readback failed: $target"
        }
    }
}
Write-Host 'Stock Mini3 recovery material verified. Offline preparation only; no device was accessed or flashed.'
[pscustomobject]@{ Directory = $destination; PartitionTable = (Join-Path $destination 'partition_table.json'); ImagesToRestore = 6; CheckedOnly = [bool]$CheckOnly }
