#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$components = Join-Path $repo 'firmware/components'
$directory = Join-Path $repo '.cache/native-tests'
$null = New-Item -ItemType Directory -Path $directory -Force
$compilerPath = (Get-Command $Compiler -ErrorAction Stop).Source
$compilerTarget = (& $compilerPath '-dumpmachine').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine C compiler target.' }
$mathLibrary = @()
if ($compilerTarget -notmatch 'windows-msvc') { $mathLibrary = @('-lm') }
$include = @('pose_session', 'pose_image', 'motion_logic', 'pose_decode') | ForEach-Object { '-I' + (Join-Path $components $_) }
$source = @('pose_session/pose_session.c', 'pose_image/pose_image.c', 'motion_logic/motion_logic.c') | ForEach-Object { Join-Path $components $_ }
$binary = Join-Path $directory 'pose-session-tests.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' '-fno-fast-math' '-ffp-contract=off' @include @source (Join-Path $repo 'tests/native/pose_session_test.c') @mathLibrary '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose session test compilation failed.' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Pose session tests failed.' }
