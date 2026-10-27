#requires -Version 7.0
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')
$simulator = Get-SimulatorPath
$source = Join-Path $RepoRoot 'app/main.lua'
# This command intentionally opens the interactive development window.
Start-Process -FilePath $simulator -ArgumentList ('"{0}"' -f $source) -WorkingDirectory $RepoRoot | Out-Null
Write-Host 'Simulator opened. Edit app/main.lua; saving triggers hot reload.'
