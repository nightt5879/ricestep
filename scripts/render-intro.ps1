#requires -Version 7.2
[CmdletBinding()]
param([switch]$StillsOnly, [string]$VoiceDirectory)
. (Join-Path $PSScriptRoot 'common.ps1')
# All rendering is local and silent. No ADB, device, voice server or playback.
& (Join-Path $PSScriptRoot 'build.ps1')
$source = Join-Path $PSScriptRoot 'video-intro'
$stage = Join-Path $RepoRoot 'work/filming/video'
$null = New-Item -ItemType Directory -Path $stage -Force
foreach ($name in @('package.json', 'package-lock.json', 'src.tsx', 'capture.cjs', 'render.cjs', 'design-spec.md')) {
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $stage $name) -Force
}
$resolvedVoiceDirectory = if ($VoiceDirectory) { (Resolve-Path -LiteralPath $VoiceDirectory).Path }
Push-Location $stage
$previousVoiceDirectory = $env:FEIYU_VIDEO_VOICE_DIR
try {
    if ($VoiceDirectory) {
        $env:FEIYU_VIDEO_VOICE_DIR = $resolvedVoiceDirectory
    } else {
        $env:FEIYU_VIDEO_VOICE_DIR = $null
    }
    $hash = (Get-FileHash -LiteralPath package-lock.json).Hash
    if ($hash.ToLowerInvariant() -ne $Toolchain.video.npmLockSha256) {
        throw 'Video package lock checksum does not match toolchain.lock.json.'
    }
    $stamp = '.installed-lock-sha256'
    if (-not (Test-Path -LiteralPath node_modules/@remotion/cli) -or
        -not (Test-Path -LiteralPath $stamp) -or
        (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Video dependency installation failed.' }
        Set-Content -LiteralPath $stamp -Value $hash -Encoding ascii
    }
    foreach ($binary in @(
        @{Path='node_modules/ffmpeg-static/ffmpeg.exe';Hash=$Toolchain.video.ffmpeg.binarySha256},
        @{Path='node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe';Hash=$Toolchain.video.ffprobe.binarySha256}
    )) {
        if ((Get-FileHash -LiteralPath $binary.Path).Hash.ToLowerInvariant() -ne $binary.Hash) {
            throw "Video binary checksum mismatch: $($binary.Path)"
        }
    }
    & node capture.cjs
    if ($LASTEXITCODE -ne 0) { throw 'Official simulator capture failed.' }
    if ($StillsOnly) { & node render.cjs --stills-only } else { & node render.cjs }
    if ($LASTEXITCODE -ne 0) { throw 'Video rendering failed.' }
} finally {
    $env:FEIYU_VIDEO_VOICE_DIR = $previousVoiceDirectory
    Pop-Location
}
