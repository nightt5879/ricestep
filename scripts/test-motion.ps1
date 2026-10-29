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
# Clang targeting MSVC uses the CRT math functions; Unix/MinGW link libm.
$mathLibrary = @()
if ($compilerTarget -notmatch 'windows-msvc') { $mathLibrary = @('-lm') }
$source = Join-Path $repo 'firmware/components/motion_logic'
$test = Join-Path $repo 'tests/native/motion_logic_test.c'
$binary = Join-Path $directory 'motion-logic-tests.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' "-I$source" (Join-Path $source 'motion_logic.c') $test @mathLibrary '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'Native motion logic compilation failed.' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Native motion logic tests failed.' }
