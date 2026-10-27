#requires -Version 7.0
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')
$testDirectory = Join-Path $RepoRoot '.cache/tests'
$null = New-Item -ItemType Directory -Path $testDirectory -Force
foreach ($apiVersion in @(4, 5)) {
    # Count real main.lua draw calls against each profile; the host remains API 4.
    # Voice scheduling is deliberately stubbed by render_budget_spec.lua.
    $source = "RICESTEP_CONFIG={tts_enabled=false,sound_enabled=false,storage_enabled=true,clock_enabled=false}`nRENDER_TEST_API=$apiVersion`n"
    foreach ($file in @('app/game.lua', 'app/character.lua', 'app/main.lua', 'tests/render_budget_spec.lua')) {
        $source += [IO.File]::ReadAllText((Join-Path $RepoRoot $file)) + "`n"
    }
    $sourcePath = Join-Path $testDirectory "render-budget-api$apiVersion.lua"
    [IO.File]::WriteAllText($sourcePath, $source, [Text.UTF8Encoding]::new($false))
    & node (Join-Path $PSScriptRoot 'minify-lua.cjs') $sourcePath
    if ($LASTEXITCODE -ne 0) { throw 'Render budget test compaction failed.' }
    $imagePath = Join-Path $testDirectory "render-budget-api$apiVersion.png"
    # Do not accept an old result if the simulator stops before the final report.
    if (Test-Path -LiteralPath $imagePath) { Remove-Item -LiteralPath $imagePath }
    Invoke-Headless -Source $sourcePath -Image $imagePath
    Write-Host "API $apiVersion geometry/text budget and canvas bounds passed (counting screen)."
}
