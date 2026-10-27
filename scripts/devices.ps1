#requires -Version 7.0
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'common.ps1')
$adb = Get-AdbPath
& $adb devices -l
if ($LASTEXITCODE -ne 0) { throw 'ADB device enumeration failed.' }
