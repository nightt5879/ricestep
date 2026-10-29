#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc', [string]$ModelWindow)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compilerPath = (Get-Command $Compiler -ErrorAction Stop).Source
$output = Join-Path $repo '.cache/native-tests'
$null = New-Item -ItemType Directory -Path $output -Force
$source = Join-Path $repo 'firmware/components/pose_resources'
$binary = Join-Path $output 'pose-resources-tests.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' "-I$source" (Join-Path $source 'pose_resources.c') (Join-Path $repo 'tests/native/pose_resources_test.c') '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose resource C test compilation failed.' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose resource C tests failed.' }
if ($ModelWindow) {
    $resolvedWindow = (Resolve-Path -LiteralPath $ModelWindow -ErrorAction Stop).Path
    # The C callback is deliberately a test double. Verify real bytes with the
    # production packager's actual SHA-256 check before this optional extra run.
    $verify = 'const fs=require("node:fs"),path=require("node:path");const api=require(path.join(process.argv[1],"scripts/prepare-pose-resources.cjs"));api.validateModelWindow(fs.readFileSync(process.argv[2]));console.log("Explicit model window passed real SHA-256 and wire-format validation.");'
    & node '-e' $verify $repo $resolvedWindow
    if ($LASTEXITCODE -ne 0) { throw 'Explicit model window failed SHA-256/header validation.' }
    & $binary $resolvedWindow
    if ($LASTEXITCODE -ne 0) { throw 'Prepared model-window C tests failed.' }
}
