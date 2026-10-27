#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Serial)
. (Join-Path $PSScriptRoot 'common.ps1')
$adb = Get-AdbPath
$state = & $adb -s $Serial get-state 2>&1
if ($LASTEXITCODE -ne 0 -or "$state".Trim() -ne 'device') { throw 'The selected device is not ready.' }
& (Join-Path $PSScriptRoot 'build.ps1')
$backupDirectory = Join-Path $RepoRoot '.cache/device-backup'
$null = New-Item -ItemType Directory -Path $backupDirectory -Force
$backup = Join-Path $backupDirectory "before-deploy-$([Guid]::NewGuid().ToString('N')).lua"
$backupResult = & $adb -s $Serial pull /miniapp/miniapp.lua $backup 2>&1
if ($LASTEXITCODE -ne 0 -and "$backupResult" -notmatch 'does not exist') {
    throw 'Could not back up the current device app; upload stopped.'
}
$bundle = Join-Path $RepoRoot 'dist/ricestep.lua'
& $adb -s $Serial push $bundle /miniapp/ricestep.lua
if ($LASTEXITCODE -ne 0) { throw 'Device rejected the Lua app. See the ADB error above.' }
$readback = Join-Path $backupDirectory 'ricestep-readback.lua'
& $adb -s $Serial pull /miniapp/miniapp.lua $readback
if ($LASTEXITCODE -ne 0) { throw 'Upload succeeded, but device source readback failed.' }
if ((Get-FileHash -LiteralPath $readback).Hash -ne (Get-FileHash -LiteralPath $bundle).Hash) {
    throw 'The running device source does not match this build.'
}
Write-Host 'App accepted by the device; running-source hash matches this build.'
Write-Host 'Check the physical display and function button to finish the hardware check.'
