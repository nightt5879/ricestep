#requires -Version 7.0
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')

$null = New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'dist') -Force
$source = Join-Path $RepoRoot "dist/build-$([Guid]::NewGuid().ToString('N')).lua"
$prefix = "-- RiceStep bundle; edit app/*.lua instead.`nRICESTEP_CONFIG={tts_enabled=true}`n"
$sourceText = $prefix
foreach ($module in @('game.lua', 'character.lua', 'main.lua')) {
    $sourceText += "`n-- Module: $module`n" + [IO.File]::ReadAllText((Join-Path $RepoRoot "app/$module")) + "`n"
}
$sourceSize = [Text.Encoding]::UTF8.GetByteCount($sourceText)
if ($sourceSize -gt 65536) { throw 'Bundled Lua source exceeds the SDK default 64 KiB limit.' }
[IO.File]::WriteAllText($source, $sourceText, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $source
if ($LASTEXITCODE -ne 0) { throw 'Lua source compaction failed.' }
$sourceSize = (Get-Item -LiteralPath $source).Length
# New output name prevents a stale screenshot from passing a failed render.
$preview = Join-Path $RepoRoot "dist/preview-$([Guid]::NewGuid().ToString('N')).png"
try {
    Invoke-Headless -Source $source -Image $preview
    $bytes = [IO.File]::ReadAllBytes($preview)
    if ($bytes.Length -lt 24 -or [BitConverter]::ToString($bytes, 0, 8) -ne '89-50-4E-47-0D-0A-1A-0A') {
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
    if (Test-Path -LiteralPath $source) { Remove-Item -LiteralPath $source }
}
