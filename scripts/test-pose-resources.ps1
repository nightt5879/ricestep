#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
& node (Join-Path $repo 'scripts/prepare-pose-resources.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Pinned resource preparation failed.' }
& node '--test' (Join-Path $repo 'tests/pose-resources.test.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Resource pack tests failed.' }
$compilerPath = (Get-Command $Compiler -ErrorAction Stop).Source
$output = Join-Path $repo '.cache/native-tests'
$null = New-Item -ItemType Directory -Path $output -Force
$source = Join-Path $repo 'firmware/components/pose_resources'
$binary = Join-Path $output 'pose-resources-tests.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' "-I$source" (Join-Path $source 'pose_resources.c') (Join-Path $repo 'tests/native/pose_resources_test.c') '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose resource C test compilation failed.' }
& $binary (Join-Path $repo 'dist/native/pose-resources/pose.bin')
if ($LASTEXITCODE -ne 0) { throw 'Pose resource C tests failed.' }
