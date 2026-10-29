#requires -Version 7.2
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $repo '.cache/native-listenai-tools/gcc/bin/riscv64-unknown-elf-gcc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'Run setup-native.ps1 first.' }
$output = Join-Path $repo '.cache/pose-target'
$null = New-Item -ItemType Directory -Path $output -Force
# Match the verified Mini3 CP's actual soft-float ISA/ABI, overriding the SDK's
# global fast-math setting: the decoder requires exact float32/rounding behavior.
$flags = @('-std=c11', '-Wall', '-Wextra', '-Werror', '-Wpedantic', '-Os',
    '-mtune=nuclei-300-series', '-march=rv32imac_zba_zbb_zbc_zbs', '-mabi=ilp32',
    '-fno-fast-math', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections')
$results = @()
foreach ($name in @('motion_logic/motion_logic', 'pose_image/pose_image', 'pose_decode/pose_movenet')) {
    $source = Join-Path $repo "firmware/components/$name.c"
    $object = Join-Path $output ((Split-Path $name -Leaf) + '.o')
    & $compiler @flags '-c' $source '-o' $object
    if ($LASTEXITCODE -ne 0) { throw "Target compilation failed: $name" }
    $bytes = [IO.File]::ReadAllBytes($object)
    if ($bytes.Length -lt 52 -or [Text.Encoding]::ASCII.GetString($bytes, 1, 3) -ne 'ELF' -or
        $bytes[0] -ne 127 -or $bytes[4] -ne 1 -or $bytes[5] -ne 1 -or
        [BitConverter]::ToUInt16($bytes, 18) -ne 243) { throw 'Object is not ELF32 little-endian RISC-V.' }
    $results += [ordered]@{ source = "firmware/components/$name.c"; bytes = $bytes.Length
        sourceSha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        objectSha256 = (Get-FileHash -LiteralPath $object -Algorithm SHA256).Hash.ToLowerInvariant() }
}
[ordered]@{ scope = 'RISC-V object compilation only; not linked, run or flashed'
    flags = $flags; results = $results; boardValidated = $false
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'report.json') -Encoding utf8
Write-Host 'Three pose/motion components compiled as Mini3-compatible RISC-V objects. No firmware was linked or flashed.'
