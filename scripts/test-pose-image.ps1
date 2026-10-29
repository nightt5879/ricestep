#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$directory = Join-Path $repo '.cache/native-tests'
$null = New-Item -ItemType Directory -Path $directory -Force
$compilerPath = (Get-Command $Compiler -ErrorAction Stop).Source
$compilerTarget = (& $compilerPath '-dumpmachine').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the C compiler target.' }
$mathLibrary = @()
if ($compilerTarget -notmatch 'windows-msvc') { $mathLibrary = @('-lm') }
$source = Join-Path $repo 'firmware/components/pose_image'
$test = Join-Path $repo 'tests/native/pose_image_test.c'
$binary = Join-Path $directory 'pose-image-tests.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' "-I$source" (Join-Path $source 'pose_image.c') $test @mathLibrary '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose image test compilation failed.' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose image tests failed.' }
