#requires -Version 7.2
[CmdletBinding()]
param([switch]$LoopbackOnly)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'scripts/start-voice-server.ps1')
$script:checks = 0
function Assert-Voice([bool]$Condition, [string]$Description) {
    $script:checks++
    if (-not $Condition) { throw "Assertion failed: $Description" }
}
function Assert-VoiceFailure([scriptblock]$Action, [string]$Pattern) {
    $script:checks++
    try { & $Action | Out-Null }
    catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw "Unexpected failure: $($_.Exception.Message)" }
        return
    }
    throw "Expected a failure matching: $Pattern"
}
function Get-UnusedVoicePort([string]$Address) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse($Address), 0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}
function Wait-TestVoicePort($Process, [string]$Address, [int]$Port) {
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { throw 'Test voice fixture exited before listening.' }
        $socket = [Net.Sockets.TcpClient]::new()
        try {
            try { $null = $socket.ConnectAsync($Address, $Port).WaitAsync([TimeSpan]::FromMilliseconds(200)).GetAwaiter().GetResult() }
            catch { }
            if ($socket.Connected) { return }
        } finally { $socket.Dispose() }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Test voice fixture did not listen within five seconds.'
}

foreach ($ip in @('10.0.0.2', '172.16.0.2', '172.31.255.2', '192.168.1.2')) {
    Assert-Voice (Get-RiceVoiceEndpoint "http://${ip}:8770/voice/v1" @($ip)).CanStart "Local private $ip"
}
foreach ($ip in @('127.0.0.1', '169.254.1.2', '172.15.0.2', '172.32.0.2', '8.8.8.8')) {
    Assert-Voice (-not (Get-RiceVoiceEndpoint "http://${ip}:8770/voice/v1" @($ip)).CanStart) "Do not auto-start on $ip"
}
Assert-Voice (-not (Get-RiceVoiceEndpoint 'http://192.168.1.2:8770/voice/v1' @('192.168.1.3')).CanStart) 'Unassigned private address'
Assert-Voice (-not (Get-RiceVoiceEndpoint 'https://192.168.1.2:8770/voice/v1' @('192.168.1.2')).CanStart) 'Do not launch HTTP over an HTTPS configuration'
Assert-Voice (-not (Get-RiceVoiceEndpoint 'http://192.168.1.2/voice/v1' @('192.168.1.2')).CanStart) 'Do not auto-bind privileged port'
foreach ($url in @('http://user:pass@192.168.1.2:8770/voice/v1', 'http://192.168.1.2:8770/voice/v1?q=1',
    'http://192.168.1.2:8770/voice/v1#fragment', 'http://192.168.1.2:8770/another/path', 'file:///voice/v1')) {
    Assert-VoiceFailure { Get-RiceVoiceEndpoint $url @('192.168.1.2') } 'base_url must'
}

# Exercise real startup/reuse/HTTP verification on an isolated port and path with spaces.
# Only children started by this test are stopped; no board or production server is used.
$local = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
    Where-Object OperationalStatus -eq Up | ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
    ForEach-Object { $_.Address.ToString() } | Where-Object {
        $_ -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)'
    })
if ($LoopbackOnly) { $local = @() }
$address = if ($local.Count) { $local[0] } else { '127.0.0.1' }
$fixture = Join-Path $repo ".cache/voice helper tests/$([Guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path (Join-Path $fixture 'scripts'), (Join-Path $fixture 'assets/audio') -Force
Copy-Item -LiteralPath (Join-Path $repo 'scripts/voice-server.cjs') -Destination (Join-Path $fixture 'scripts/voice-server.cjs')
Copy-Item -LiteralPath (Join-Path $repo 'assets/audio/v1') -Destination (Join-Path $fixture 'assets/audio') -Recurse
$port = Get-UnusedVoicePort $address
$config = Join-Path $fixture '.voice-config.json'
@{ base_url = "http://${address}:$port/voice/v1" } | ConvertTo-Json | Set-Content -LiteralPath $config
$originalConfig = (Get-FileHash -LiteralPath $config).Hash
$ownedIds = [Collections.Generic.List[int]]::new()
try {
    if ($local.Count) {
        $started = Start-RiceVoiceServer -Root $fixture
        if ($started.Started) { $ownedIds.Add($started.ProcessId) }
        Assert-Voice ($started.Started -and $started.ProcessId -gt 0) 'Hidden server starts from a path with spaces'
        Assert-Voice (Test-Path -LiteralPath (Join-Path $fixture '.cache/voice-server/process.json')) 'PID metadata recorded locally'
    } else {
        # CI machines need no private LAN or user configuration. Bind a test-owned
        # loopback service manually; production auto-start remains private-IP-only.
        $nodePath = (Get-Command node -CommandType Application | Select-Object -First 1).Source
        $loopback = Start-Process -FilePath $nodePath -ArgumentList @(
            ('"{0}"' -f (Join-Path $fixture 'scripts/voice-server.cjs')), '--host', $address, '--port', [string]$port
        ) -WindowStyle Hidden -PassThru
        $ownedIds.Add($loopback.Id)
        Wait-TestVoicePort $loopback $address $port
        Write-Host 'No private IPv4: testing existing loopback service; private-IP eligibility was checked above.'
    }
    $reused = Start-RiceVoiceServer -Root $fixture -CheckOnly
    Assert-Voice (-not $reused.Started) 'Correct existing voice server is reused'
    Assert-Voice ((Get-FileHash -LiteralPath $config).Hash -eq $originalConfig) 'Startup does not change configuration'
    $feed = Join-Path $fixture 'assets/audio/v1/feed.mp3'
    [IO.File]::WriteAllText($feed, 'not the approved voice')
    Assert-VoiceFailure { Start-RiceVoiceServer -Root $fixture -CheckOnly } 'Local feed.mp3 does not match'
    Copy-Item -LiteralPath (Join-Path $repo 'assets/audio/v1/feed.mp3') -Destination $feed -Force

    $deadPort = Get-UnusedVoicePort '127.0.0.1'
    @{ base_url = "http://127.0.0.1:$deadPort/voice/v1" } | ConvertTo-Json | Set-Content -LiteralPath $config
    Assert-VoiceFailure { Start-RiceVoiceServer -Root $fixture } 'not an eligible local HTTP endpoint'

    $wrongPort = Get-UnusedVoicePort $address
    $wrongServer = Join-Path $fixture 'wrong voice.cjs'
    [IO.File]::WriteAllText($wrongServer, @'
const data = process.argv[4] ? require('node:fs').readFileSync(process.argv[4]) : Buffer.from('WRONG VOICE');
require('node:http').createServer((req,res)=>{
  res.writeHead(200, {'Content-Type':'audio/mpeg'}); res.end(data);
}).listen(Number(process.argv[3]), process.argv[2]);
'@)
    $nodePath = (Get-Command node -CommandType Application | Select-Object -First 1).Source
    $wrong = Start-Process -FilePath $nodePath -ArgumentList @(('"{0}"' -f $wrongServer), $address, [string]$wrongPort) -WindowStyle Hidden -PassThru
    $ownedIds.Add($wrong.Id)
    Wait-TestVoicePort $wrong $address $wrongPort
    @{ base_url = "http://${address}:$wrongPort/voice/v1" } | ConvertTo-Json | Set-Content -LiteralPath $config
    Assert-VoiceFailure { Start-RiceVoiceServer -Root $fixture } 'wrong feed clip'
    Assert-Voice (-not (Get-Process -Id $wrong.Id).HasExited) 'Unhealthy existing process is not killed'
    $widePort = Get-UnusedVoicePort $address
    $wide = Start-Process -FilePath $nodePath -ArgumentList @(
        ('"{0}"' -f $wrongServer), $address, [string]$widePort, ('"{0}"' -f $feed)
    ) -WindowStyle Hidden -PassThru
    $ownedIds.Add($wide.Id)
    Wait-TestVoicePort $wide $address $widePort
    @{ base_url = "http://${address}:$widePort/voice/v1" } | ConvertTo-Json | Set-Content -LiteralPath $config
    Assert-VoiceFailure { Start-RiceVoiceServer -Root $fixture } 'HTTP allowlist check'
    Assert-Voice (-not (Get-Process -Id $wide.Id).HasExited) 'A server with overly broad routes is not killed'
} finally {
    foreach ($ownedId in $ownedIds) { Stop-Process -Id $ownedId -ErrorAction SilentlyContinue }
}
Write-Host "Voice helper: $script:checks assertions passed. Isolated logs remain in .cache/voice helper tests/."
