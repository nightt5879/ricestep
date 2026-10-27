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
