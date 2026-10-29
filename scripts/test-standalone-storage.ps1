#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$directory = Join-Path $repo '.cache/native-tests'
$null = New-Item -ItemType Directory -Path $directory -Force
$compilerPath = (Get-Command $Compiler -ErrorAction Stop).Source
$test = Join-Path $repo 'tests/native/standalone_storage_test.c'
$stubs = Join-Path $repo 'tests/native/standalone_stubs'
$binary = Join-Path $directory 'standalone-storage-tests.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' "-I$stubs" $test '-o' $binary
if ($LASTEXITCODE -ne 0) { throw 'Standalone storage test compilation failed.' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Standalone storage tests failed.' }
