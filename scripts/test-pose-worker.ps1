#requires -Version 7.0
[CmdletBinding()]
param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compilerPath = (Get-Command $Compiler -ErrorAction Stop).Source
$output = Join-Path $repo '.cache/native-tests'
$null = New-Item -ItemType Directory -Path $output -Force
$includes = @('tests/native/pose_worker_mock', 'firmware/standalone', 'firmware/components/motion_logic', 'firmware/components/pose_image', 'firmware/components/pose_decode', 'firmware/components/pose_engine', 'firmware/components/pose_resources', 'firmware/components/pose_session') | ForEach-Object { '-I' + (Join-Path $repo $_) }
foreach ($version in @(2, 3)) {
    $binary = Join-Path $output "pose-worker-v$version.exe"
    & $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' "-DMBEDTLS_VERSION_MAJOR=$version" @includes (Join-Path $repo 'tests/native/pose_worker_test.c') '-o' $binary
    if ($LASTEXITCODE -ne 0) { throw 'Pose worker test compilation failed.' }
    & $binary
    if ($LASTEXITCODE -ne 0) { throw 'Pose worker lifecycle tests failed.' }
}
$bridgeBinary = Join-Path $output 'pose-camera-bridge.exe'
& $compilerPath '-std=c11' '-Wall' '-Wextra' '-Werror' '-Wpedantic' '-O2' @includes (Join-Path $repo 'tests/native/pose_camera_test.c') '-o' $bridgeBinary
if ($LASTEXITCODE -ne 0) { throw 'Pose camera bridge compilation failed.' }
& $bridgeBinary
if ($LASTEXITCODE -ne 0) { throw 'Pose camera bridge lifecycle tests failed.' }
