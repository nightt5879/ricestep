[CmdletBinding()]
param([switch]$Regenerate)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$voiceRoot = Join-Path $projectRoot 'assets/audio/v1'
$voicePython = Join-Path $projectRoot '.tools/voice-venv/Scripts/python.exe'
$voiceSpec = Get-Content -LiteralPath (Join-Path $voiceRoot 'lines.json') -Raw | ConvertFrom-Json
$manifestFile = Join-Path $voiceRoot 'manifest.json'
$previous = if (Test-Path -LiteralPath $manifestFile) { Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json } else { $null }
if ($previous -and -not $Regenerate) {
    foreach ($setting in @('voice', 'rate', 'pitch', 'volume')) {
        if ($previous.$setting -ne $voiceSpec.$setting) { throw 'Voice settings changed. Use -Regenerate to create matching audio.' }
    }
}

# Keep the voice tool isolated from the user's Python installation.
if (-not (Test-Path -LiteralPath $voicePython)) {
    & uv venv (Join-Path $projectRoot '.tools/voice-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the voice environment.' }
}
& uv pip install --python $voicePython 'edge-tts==7.3.1'
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned voice generator.' }
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$clips = @()
foreach ($line in $voiceSpec.lines.PSObject.Properties) {
    $id = $line.Name
    if ($id -cnotmatch '^[a-z][a-z0-9_]*$') { throw "Invalid voice line id: $id" }
    $file = Join-Path $voiceRoot "$id.mp3"
    if ($previous -and -not $Regenerate -and (Test-Path -LiteralPath $file)) {
        $saved = @($previous.clips | Where-Object { $_.id -eq $id })
        if ($saved.Count -ne 1 -or $saved[0].text -ne $line.Value) { throw "Voice line $id changed. Use -Regenerate." }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $saved[0].sha256) { throw "Saved audio changed: $id. Use -Regenerate." }
    }
    if ($Regenerate -or -not (Test-Path -LiteralPath $file) -or (Get-Item -LiteralPath $file).Length -eq 0) {
        $partial = "$file.partial"
        & $voicePython -m edge_tts --voice $voiceSpec.voice "--rate=$($voiceSpec.rate)" "--pitch=$($voiceSpec.pitch)" "--volume=$($voiceSpec.volume)" --text $line.Value --write-media $partial
        if ($LASTEXITCODE -ne 0) { throw "Voice generation failed: $id. Existing audio has not been replaced." }
        $probeText = & $ffprobe -v error -show_entries 'stream=codec_name,sample_rate,channels' -show_entries 'format=duration,size' -of json $partial
        if ($LASTEXITCODE -ne 0) { throw "Invalid generated audio: $id" }
        $probe = ($probeText -join "`n") | ConvertFrom-Json
        if ($probe.streams[0].codec_name -ne 'mp3' -or [double]$probe.format.duration -le 0) { throw "Invalid audio format: $id" }
        Move-Item -LiteralPath $partial -Destination $file -Force
    }
    $probeText = & $ffprobe -v error -show_entries 'stream=codec_name,sample_rate,channels' -show_entries 'format=duration,size' -of json $file
    if ($LASTEXITCODE -ne 0) { throw "Invalid saved audio: $id" }
    $probe = ($probeText -join "`n") | ConvertFrom-Json
    $clips += [ordered]@{
        id = $id; file = "$id.mp3"; text = $line.Value
        duration_ms = [int][Math]::Round([double]$probe.format.duration * 1000)
        bytes = (Get-Item -LiteralPath $file).Length
        sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        sample_rate = [int]$probe.streams[0].sample_rate
        channels = [int]$probe.streams[0].channels
    }
    Write-Host "$id : $($clips[-1].duration_ms) ms"
}
$manifest = [ordered]@{
    version = 1
    provider = 'Microsoft Edge Read Aloud'
    generator = @{ package = 'edge-tts'; version = '7.3.1' }
    voice = $voiceSpec.voice; rate = $voiceSpec.rate; pitch = $voiceSpec.pitch; volume = $voiceSpec.volume
    clips = $clips
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestFile -Encoding utf8NoBOM
