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
# Behavior fixtures omit only sprite rendering to stay within the runtime's
# 64 KiB source limit. verify-render.ps1 tests the real character and full UI
# separately for both API profiles; production always includes real sprites.
$uiSource = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=false,clock_enabled=false}`nfunction draw_character() end`n"
foreach ($file in @('app/game.lua', 'app/voice.lua', 'app/main.lua', 'tests/ui_spec.lua')) {
    $uiSource += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
}
$uiPath = Join-Path $testDirectory 'ui.lua'
[IO.File]::WriteAllText($uiPath, $uiSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $uiPath
if ($LASTEXITCODE -ne 0) { throw 'UI test compaction failed.' }
Invoke-Headless -Source $uiPath -Image (Join-Path $testDirectory 'ui.png')
Write-Host 'Feeding, focus, cooking and collection UI tests passed.'
$lifecycleSource = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=true,clock_enabled=false}`nfunction draw_character() end`n"
foreach ($file in @('app/game.lua', 'app/voice.lua', 'app/main.lua', 'tests/lifecycle_spec.lua')) {
    $lifecycleSource += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
}
$lifecyclePath = Join-Path $testDirectory 'lifecycle.lua'
[IO.File]::WriteAllText($lifecyclePath, $lifecycleSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $lifecyclePath
if ($LASTEXITCODE -ne 0) { throw 'Lifecycle test compaction failed.' }
Invoke-Headless -Source $lifecyclePath -Image (Join-Path $testDirectory 'lifecycle.png')
Write-Host 'Companion save, cooking restart and legacy migration tests passed.'
$exerciseSource = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=true,clock_enabled=false}`nfunction draw_character() end`n"
foreach ($file in @('app/game.lua', 'app/voice.lua', 'app/main.lua', 'tests/exercise_spec.lua')) {
    $exerciseSource += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
}
$exercisePath = Join-Path $testDirectory 'exercise.lua'
[IO.File]::WriteAllText($exercisePath, $exerciseSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $exercisePath
if ($LASTEXITCODE -ne 0) { throw 'Exercise test compaction failed.' }
Invoke-Headless -Source $exercisePath -Image (Join-Path $testDirectory 'exercise.png')
Write-Host 'Mock motion sessions, retirement and reward deduplication tests passed.'
$clockSource = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=false,clock_enabled=true}`nfunction draw_character() end`n"
foreach ($file in @('app/game.lua', 'app/voice.lua', 'app/main.lua', 'tests/clock_spec.lua')) {
    $clockSource += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
}
$clockPath = Join-Path $testDirectory 'clock.lua'
[IO.File]::WriteAllText($clockPath, $clockSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $clockPath
if ($LASTEXITCODE -ne 0) { throw 'Clock test compaction failed.' }
Invoke-Headless -Source $clockPath -Image (Join-Path $testDirectory 'clock.png')
Write-Host 'Calibrated clock, pause and time-jump tests passed.'
$exitPath = Join-Path $testDirectory 'standalone-exit.lua'
& node -e 'const f=require("node:fs");f.writeFileSync(process.argv[3],require(process.argv[1]).bundleFiles().source+"\n"+f.readFileSync(process.argv[2],"utf8"));' (Join-Path $PSScriptRoot 'prepare-standalone.cjs') (Join-Path $RepoRoot 'tests/standalone_exit_spec.lua') $exitPath
if ($LASTEXITCODE -ne 0) { throw 'Standalone exit fixture bundling failed.' }
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $exitPath
if ($LASTEXITCODE -ne 0) { throw 'Standalone exit fixture compaction failed.' }
Invoke-Headless -Source $exitPath -Image (Join-Path $testDirectory 'standalone-exit.png')
Write-Host 'Actual standalone exit wrapper stops motion before persisting the ledger.'
$exercisePreview = Join-Path $testDirectory 'exercise-preview.lua'
& node -e 'const f=require("node:fs");f.writeFileSync(process.argv[3],require(process.argv[1]).bundleFiles().source+"\n"+f.readFileSync(process.argv[2],"utf8"));' (Join-Path $PSScriptRoot 'prepare-standalone.cjs') (Join-Path $RepoRoot 'tests/exercise_preview.lua') $exercisePreview
if ($LASTEXITCODE -ne 0) { throw 'Exercise preview fixture bundling failed.' }
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $exercisePreview
if ($LASTEXITCODE -ne 0) { throw 'Exercise preview compaction failed.' }
Invoke-Headless -Source $exercisePreview -Image (Join-Path $testDirectory 'exercise-preview.png')
Write-Host 'Silent exercise page visual fixture saved (explicitly labelled synthetic data).'
$voiceSource = [IO.File]::ReadAllText((Join-Path $RepoRoot 'app/voice.lua')) + "`n" + [IO.File]::ReadAllText((Join-Path $RepoRoot 'tests/voice_spec.lua'))
$voicePath = Join-Path $testDirectory 'voice.lua'
[IO.File]::WriteAllText($voicePath, $voiceSource, [Text.UTF8Encoding]::new($false))
& node (Join-Path $PSScriptRoot 'minify-lua.cjs') $voicePath
if ($LASTEXITCODE -ne 0) { throw 'Voice test compaction failed.' }
Invoke-Headless -Source $voicePath -Image (Join-Path $testDirectory 'voice.png')
Write-Host 'Voice queue, interruption and no-system-voice regression tests passed.'
& (Join-Path $PSScriptRoot 'verify-render.ps1')
& node --test --test-reporter=dot (Join-Path $PSScriptRoot 'rectangle-encoder.test.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Sprite encoder tests failed.' }
& node --test --test-reporter=dot (Join-Path $RepoRoot 'tests/voice-server.test.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Voice server tests failed.' }
& (Join-Path $RepoRoot 'tests/start-voice-server.test.ps1')
$relaySpec = Join-Path $RepoRoot 'tests/step-relay.test.cjs'
if (Test-Path -LiteralPath $relaySpec) {
    & node --test --test-reporter=dot $relaySpec
    if ($LASTEXITCODE -ne 0) { throw 'Step relay tests failed.' }
}
