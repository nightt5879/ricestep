#requires -Version 7.0
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')

$source = Join-Path $RepoRoot 'app/main.lua'
$sourceSize = (Get-Item -LiteralPath $source).Length
if ($sourceSize -gt 65536) { throw 'Lua source exceeds the SDK default 64 KiB limit.' }
$sourceText = [IO.File]::ReadAllText($source)
if ([string]::IsNullOrWhiteSpace($sourceText)) { throw 'Lua source is empty.' }
$null = New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'dist') -Force
# New output name prevents a stale screenshot from passing a failed render.
$preview = Join-Path $RepoRoot "dist/preview-$([Guid]::NewGuid().ToString('N')).png"
try {
    Invoke-Headless -Source $source -Image $preview
    $bytes = [IO.File]::ReadAllBytes($preview)
    if ($bytes.Length -lt 24 -or [Convert]::ToHexString($bytes[0..7]) -ne '89504E470D0A1A0A') {
        throw 'Simulator output is not a valid PNG header.'
    }
    $width = [uint32]$bytes[16] * 16777216 + [uint32]$bytes[17] * 65536 + [uint32]$bytes[18] * 256 + $bytes[19]
    $height = [uint32]$bytes[20] * 16777216 + [uint32]$bytes[21] * 65536 + [uint32]$bytes[22] * 256 + $bytes[23]
    if ($width -ne 240 -or $height -ne 240) { throw "Unexpected canvas: ${width}x${height}" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $RepoRoot 'dist/ricestep.lua') -Force
    Move-Item -LiteralPath $preview -Destination (Join-Path $RepoRoot 'dist/preview.png') -Force
    $manifest = [ordered]@{
        app = 'ricestep'
        sourceBytes = $sourceSize
        sourceSha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        sdkTag = $Toolchain.sdk.tag
        sdkCommit = $Toolchain.sdk.commit
        validation = 'official simulator: on_start + 50 updates, 240x240 PNG'
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RepoRoot 'dist/build-info.json') -Encoding utf8
    Write-Host 'Build passed: dist/ricestep.lua, dist/preview.png, dist/build-info.json'
} finally {
    if (Test-Path -LiteralPath $preview) { Remove-Item -LiteralPath $preview }
}
