#requires -Version 7.0
[CmdletBinding()]
param([switch]$NoWatch)
. (Join-Path $PSScriptRoot 'common.ps1')
$simulator = Get-SimulatorPath
$source = Join-Path $RepoRoot 'dist/ricestep.lua'
& (Join-Path $PSScriptRoot 'build.ps1')
# This command intentionally opens the interactive development window.
$process = Start-Process -FilePath $simulator -ArgumentList ('"{0}"' -f $source) -WorkingDirectory $RepoRoot -PassThru
Write-Host 'Simulator opened. Edit app/*.lua; this terminal rebuilds changes. Ctrl+C stops watching.'
if (-not $NoWatch) {
    $stamp = (Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'app') -Filter '*.lua' | ForEach-Object { $_.LastWriteTimeUtc.Ticks }) -join ':'
    while (-not $process.HasExited) {
        Start-Sleep -Milliseconds 600
        $next = (Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'app') -Filter '*.lua' | ForEach-Object { $_.LastWriteTimeUtc.Ticks }) -join ':'
        if ($next -ne $stamp) {
            $stamp = $next
            try { & (Join-Path $PSScriptRoot 'build.ps1') } catch { Write-Warning $_ }
        }
    }
}
