#requires -Version 7.0
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')
$testDirectory = Join-Path $RepoRoot '.cache/tests'
$null = New-Item -ItemType Directory -Path $testDirectory -Force
$source = Join-Path $testDirectory 'rules.lua'
$rules = [IO.File]::ReadAllText((Join-Path $RepoRoot 'app/game.lua'))
$spec = [IO.File]::ReadAllText((Join-Path $RepoRoot 'tests/game_spec.lua'))
[IO.File]::WriteAllText($source, $rules + "`n" + $spec, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $source
if ($LASTEXITCODE -ne 0) { throw 'Rule test compaction failed.' }
Invoke-Headless -Source $source -Image (Join-Path $testDirectory 'rules.png')
Write-Host 'Lua rule tests passed in the official runtime.'
$uiSource = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=false}`n"
foreach ($file in @('app/game.lua', 'app/character.lua', 'app/main.lua', 'tests/ui_spec.lua')) {
    $uiSource += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
}
$uiPath = Join-Path $testDirectory 'ui.lua'
[IO.File]::WriteAllText($uiPath, $uiSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $uiPath
if ($LASTEXITCODE -ne 0) { throw 'UI test compaction failed.' }
Invoke-Headless -Source $uiPath -Image (Join-Path $testDirectory 'ui.png')
Write-Host 'Feeding, focus, walking and collection UI tests passed.'
$lifecycleSource = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=true}`n"
foreach ($file in @('app/game.lua', 'app/character.lua', 'app/main.lua', 'tests/lifecycle_spec.lua')) {
    $lifecycleSource += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
}
$lifecyclePath = Join-Path $testDirectory 'lifecycle.lua'
[IO.File]::WriteAllText($lifecyclePath, $lifecycleSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $lifecyclePath
if ($LASTEXITCODE -ne 0) { throw 'Lifecycle test compaction failed.' }
Invoke-Headless -Source $lifecyclePath -Image (Join-Path $testDirectory 'lifecycle.png')
Write-Host 'Idle save and measured-step restart tests passed.'
& node --test --test-reporter=dot (Join-Path $PSScriptRoot 'rectangle-encoder.test.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Sprite encoder tests failed.' }
$relaySpec = Join-Path $RepoRoot 'tests/step-relay.test.cjs'
if (Test-Path -LiteralPath $relaySpec) {
    & node --test --test-reporter=dot $relaySpec
    if ($LASTEXITCODE -ne 0) { throw 'Step relay tests failed.' }
}
