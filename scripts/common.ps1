#requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Toolchain = Get-Content -LiteralPath (Join-Path $RepoRoot 'toolchain.lock.json') -Raw | ConvertFrom-Json

function Get-SimulatorPath {
    $path = Join-Path $RepoRoot ".tools/$($Toolchain.sdk.directory)/lingclaw-sdk.exe"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'SDK missing. Run: pwsh -File scripts/setup.ps1'
    }
    return $path
}

function Get-AdbPath {
    $path = Join-Path $RepoRoot ".tools/$($Toolchain.adb.directory)/adb.exe"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'ADB missing. Run: pwsh -File scripts/setup.ps1'
    }
    return $path
}

function Expand-ToolArchive {
    param([string]$Archive, [string]$Destination)
    # Skip identical files so setup remains usable while the simulator/ADB runs.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $root $entry.FullName))
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Archive entry is outside the tools directory: $($entry.FullName)"
            }
            if ($entry.Name -eq '') {
                $null = New-Item -ItemType Directory -Path $target -Force
                continue
            }
            if (Test-Path -LiteralPath $target -PathType Leaf) {
                $stream = $entry.Open()
                $hasher = [Security.Cryptography.SHA256]::Create()
                try { $expected = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
                finally { $stream.Dispose(); $hasher.Dispose() }
                if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $expected) { continue }
            }
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $zip.Dispose() }
}

function Invoke-Headless {
    param([string]$Source, [string]$Image)
    $simulator = Get-SimulatorPath
    # Explicit waiting also handles the simulator's Windows GUI subsystem.
    $process = Start-Process -FilePath $simulator -ArgumentList @(
        '--headless', ('"{0}"' -f $Source), ('"{0}"' -f $Image)
    ) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Simulator failed with exit code $($process.ExitCode): $Source"
    }
    if (-not (Test-Path -LiteralPath $Image -PathType Leaf)) {
        throw "Simulator did not create the expected PNG: $Image"
    }
}
