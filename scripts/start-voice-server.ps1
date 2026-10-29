#requires -Version 7.2
[CmdletBinding()]
param([switch]$CheckOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RiceVoiceEndpoint {
    param([string]$BaseUrl, [string[]]$LocalAddresses)
    $uri = $null
    if (-not $BaseUrl -or $BaseUrl.Length -gt 400 -or
        $BaseUrl -notmatch '^https?://[A-Za-z0-9:./_-]+$' -or
        -not [Uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$uri) -or
        $uri.AbsolutePath.TrimEnd('/') -ne '/voice/v1' -or
        $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
        throw 'Voice base_url must be an HTTP(S) URL ending in /voice/v1, without credentials, query or fragment.'
    }
    $ip = $null
    $private = $false
    if ([Net.IPAddress]::TryParse($uri.Host, [ref]$ip) -and
        $ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
        $ip.ToString() -ceq $uri.Host) {
        $octets = $ip.GetAddressBytes()
        $private = $octets[0] -eq 10 -or ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
            ($octets[0] -eq 192 -and $octets[1] -eq 168)
    }
    [pscustomobject]@{
        BaseUrl = $uri.AbsoluteUri.TrimEnd('/')
        HostAddress = $uri.Host
        Port = $uri.Port
        CanStart = $private -and $LocalAddresses -contains $uri.Host -and $uri.Scheme -eq 'http' -and
            $uri.Port -ge 1024 -and $uri.Port -le 65535
    }
}

function Get-RiceVoiceApprovedClip {
    param([string]$Root)
    # These are the exact feed bytes approved in the board listening test.
    $approvedHash = 'b31c044dbbcd7c6b81ce93e5370336fec8dcabce468d748add05bd4a0834666c'
    $approvedBytes = 27792
    $manifest = Get-Content -LiteralPath (Join-Path $Root 'assets/audio/v1/manifest.json') -Raw | ConvertFrom-Json
    $clips = @($manifest.clips | Where-Object { $_.id -eq 'feed' })
    if ($manifest.version -ne 1 -or $clips.Count -ne 1 -or $clips[0].file -cne 'feed.mp3' -or
        $clips[0].sha256 -cne $approvedHash -or $clips[0].bytes -ne $approvedBytes) {
        throw 'Voice manifest does not identify the approved feed clip. Restore the reviewed voice pack before deploying.'
    }
    $file = Join-Path $Root 'assets/audio/v1/feed.mp3'
    if ((Get-Item -LiteralPath $file).Length -ne $approvedBytes -or
        (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -cne $approvedHash) {
        throw 'Local feed.mp3 does not match the approved voice bytes.'
    }
    [pscustomobject]@{ Hash = $approvedHash; Bytes = $approvedBytes }
}

function Get-RiceVoiceHttp {
    param([Net.Http.HttpClient]$Client, [string]$Url)
    $response = $Client.GetAsync($Url).GetAwaiter().GetResult()
    try {
        [pscustomobject]@{
            Status = [int]$response.StatusCode
            Type = [string]$response.Content.Headers.ContentType
            Bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
        }
    } finally { $response.Dispose() }
}

function Test-RiceVoiceEndpoint {
    param([Net.Http.HttpClient]$Client, [string]$BaseUrl, $Approved)
    try { $clip = Get-RiceVoiceHttp $Client "$BaseUrl/feed.mp3" }
    catch { return $false }
    if ($clip.Status -ne 200 -or $clip.Type -ne 'audio/mpeg' -or $clip.Bytes.Length -ne $Approved.Bytes -or
        [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($clip.Bytes)).ToLowerInvariant() -cne $Approved.Hash) {
        throw 'Voice endpoint returned the wrong feed clip, MIME type or HTTP status. Deployment stopped; no process was replaced.'
    }
    $origin = ([Uri]$BaseUrl).GetLeftPart([UriPartial]::Authority)
    foreach ($url in @("$origin/manifest.json", "$BaseUrl/__health_unlisted__.mp3", "$BaseUrl/feed.mp3?health=1")) {
        try { $denied = Get-RiceVoiceHttp $Client $url }
        catch { throw 'Voice endpoint became unreachable during the HTTP allowlist check.' }
        if ($denied.Status -ne 404) { throw 'Voice endpoint failed its HTTP allowlist check. Deployment stopped.' }
    }
    return $true
}

function Start-RiceVoiceServer {
    param([string]$Root, [switch]$CheckOnly)
    if (-not $IsWindows) { throw 'This hidden persistent voice-server helper requires Windows.' }
    $configPath = Join-Path $Root '.voice-config.json'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw 'Missing .voice-config.json. Configure the intended voice base_url before deploying; no device voice fallback is used.'
    }
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $addresses = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object OperationalStatus -eq Up | ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
        ForEach-Object { $_.Address.ToString() })
    $endpoint = Get-RiceVoiceEndpoint -BaseUrl $config.base_url -LocalAddresses $addresses
    $approved = Get-RiceVoiceApprovedClip -Root $Root
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    $client.MaxResponseContentBufferSize = 1048576
    try {
        if (Test-RiceVoiceEndpoint $client $endpoint.BaseUrl $approved) {
            Write-Host 'Voice ready: approved feed clip and HTTP allowlist verified; existing service reused.'
            return [pscustomobject]@{ BaseUrl = $endpoint.BaseUrl; Started = $false; ProcessId = $null }
        }
        if ($CheckOnly) { throw 'Voice endpoint is unreachable. Run scripts/start-voice-server.ps1 before deploying.' }
        if (-not $endpoint.CanStart) {
            throw 'Voice endpoint is unreachable and is not an eligible local HTTP endpoint. Automatic startup requires a private IPv4 assigned to this PC, port 1024..65535. Configuration was not changed.'
        }
        # A listening but unhealthy service must never be replaced or killed.
        $socket = [Net.Sockets.TcpClient]::new()
        try {
            try { $null = $socket.ConnectAsync($endpoint.HostAddress, $endpoint.Port).WaitAsync([TimeSpan]::FromSeconds(1)).GetAwaiter().GetResult() }
            catch { }
            if ($socket.Connected) { throw 'The configured voice port is occupied but failed health verification. No process was replaced or killed.' }
        } finally { $socket.Dispose() }
        $nodePath = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $directory = Join-Path $Root '.cache/voice-server'
        $null = New-Item -ItemType Directory -Path $directory -Force
        $run = [Guid]::NewGuid().ToString('N')
        $outLog = Join-Path $directory "$run.stdout.log"
        $errLog = Join-Path $directory "$run.stderr.log"
        $server = Join-Path $Root 'scripts/voice-server.cjs'
        $process = Start-Process -FilePath $nodePath -ArgumentList @(
            ('"{0}"' -f $server), '--host', $endpoint.HostAddress, '--port', [string]$endpoint.Port
        ) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
        [ordered]@{
            process_id = $process.Id; base_url = $endpoint.BaseUrl; started_utc = [DateTime]::UtcNow.ToString('o')
            script = $server; stdout = $outLog; stderr = $errLog
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'process.json') -Encoding utf8
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do {
            $process.Refresh()
            if ($process.HasExited) { throw "Voice server exited during startup. Inspect $errLog" }
            if (Test-RiceVoiceEndpoint $client $endpoint.BaseUrl $approved) {
                Write-Host 'Voice ready: hidden server started; approved feed clip and HTTP allowlist verified.'
                return [pscustomobject]@{ BaseUrl = $endpoint.BaseUrl; Started = $true; ProcessId = $process.Id }
            }
            Start-Sleep -Milliseconds 200
        } while ([DateTime]::UtcNow -lt $deadline)
        throw "Voice server failed health verification after startup. Inspect $errLog"
    } finally { $client.Dispose(); $handler.Dispose() }
}

if ($MyInvocation.InvocationName -ne '.') {
    Start-RiceVoiceServer -Root (Split-Path -Parent $PSScriptRoot) -CheckOnly:$CheckOnly
}
