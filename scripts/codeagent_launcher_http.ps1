# Read-only REST and MCP protocol probes; never invokes a generative model.

function Invoke-LauncherHttp {
    param([string]$Method, [string]$Url, [hashtable]$Headers = @{}, [string]$Body = '', [int]$Timeout = 15)
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    if (([Uri]$Url).IsLoopback -or ([Uri]$Url).GetLeftPart([UriPartial]::Authority) -eq $env:DEBUGPLATFORM_DIRECT_ORIGIN) { $handler.UseProxy = $false }
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds($Timeout)
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), $Url)
    try {
        foreach ($name in $Headers.Keys) { [void]$request.Headers.TryAddWithoutValidation($name, [string]$Headers[$name]) }
        if ($Body) { $request.Content = [Net.Http.StringContent]::new($Body, [Text.Encoding]::UTF8, 'application/json') }
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        try {
            $responseHeaders = @{}
            foreach ($header in $response.Headers) { $responseHeaders[$header.Key] = ($header.Value -join ',') }
            return [pscustomobject]@{
                status = [int]$response.StatusCode
                body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                headers = $responseHeaders
            }
        } finally { $response.Dispose() }
    } catch { throw "Connection failed ($Method $Url). Check the service, proxy and certificate trust." }
    finally { $request.Dispose(); $client.Dispose(); $handler.Dispose() }
}

function ConvertFrom-LauncherRpc {
    param([object]$Response)
    if ($Response.status -ne 200) { throw "MCP request failed with HTTP $($Response.status). Check the platform token and /mcp endpoint." }
    try {
        $body = $Response.body.Trim()
        if ($body.StartsWith('{')) { return $body | ConvertFrom-Json }
        $data = @($body -split "`r?`n" | Where-Object { $_.StartsWith('data:') } |
            ForEach-Object { $_.Substring(5).Trim() } | Where-Object { $_ -and $_ -ne '[DONE]' })
        foreach ($line in $data) {
            $value = $line | ConvertFrom-Json
            if ($null -ne $value.id) { return $value }
        }
    } catch { throw 'MCP returned an invalid protocol response.' }
    throw 'MCP returned no JSON-RPC response.'
}

function Test-LauncherConnection {
    param([Uri]$McpUri, [string]$Token)
    $basePath = $McpUri.AbsolutePath.TrimEnd('/')
    $basePath = $basePath.Substring(0, $basePath.Length - 4)
    $origin = $McpUri.GetLeftPart([UriPartial]::Authority)
    $apiBase = "$origin$basePath/api/v1"
    if ($McpUri.IsLoopback) {
        # Reject an unrelated occupied local port before attaching any credential.
        $identityProbe = Invoke-LauncherHttp GET "$origin$basePath/openapi.json" @{} '' 5
        if ($identityProbe.status -ne 200) { throw 'The occupied local endpoint does not expose Debug Platform; it will not be stopped or reused.' }
        try { $openApi = $identityProbe.body | ConvertFrom-Json } catch { throw 'The occupied local endpoint is not a Debug Platform service.' }
        if ($openApi.info.title -ne 'GW/AP Intelligent Debug Platform' -or
                $null -eq $openApi.paths.'/api/v1/cases') {
            throw 'The occupied local endpoint belongs to another service; it will not be stopped or reused.'
        }
    }
    $headers = @{ Authorization = "Bearer $Token"; 'X-API-Key' = $Token }
    $rest = Invoke-LauncherHttp -Method GET -Url "$apiBase/system/client-info" -Headers $headers
    # Old local packages do not expose client-info. Their administrator-only
    # status endpoint is only a compatibility fallback, never the LAN handshake.
    if ($rest.status -eq 404 -and $McpUri.IsLoopback) {
        $rest = Invoke-LauncherHttp -Method GET -Url "$apiBase/system/status" -Headers $headers
    }
    if ($rest.status -ne 200) { throw "REST verification failed with HTTP $($rest.status). The token must work for REST and MCP; a dedicated MCP token alone may not authorize REST." }
    try { $restBody = $rest.body | ConvertFrom-Json } catch { throw 'The occupied endpoint is not a Debug Platform REST service.' }
    if ($restBody.app -ne 'GW/AP Intelligent Debug Platform') { throw 'The occupied endpoint belongs to another service; it will not be stopped or reused.' }
    if ($restBody.client_contract_version -and $restBody.supported_client_contract_majors -notcontains 1) {
        throw 'This server requires a newer Client Connector. Update the connector before connecting.'
    }
    $identity = Invoke-LauncherHttp -Method GET -Url "$apiBase/system/me" -Headers $headers
    if ($identity.status -ne 200) { throw "REST identity verification failed with HTTP $($identity.status)." }
    $mcpHeaders = @{ Authorization = "Bearer $Token"; Accept = 'application/json, text/event-stream' }
    $sessionId = $null
    try {
        $initBody = @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{
            protocolVersion = '2025-11-25'; capabilities = @{}; clientInfo = @{ name = 'codeagent-launcher'; version = '1.0.0' }
        } } | ConvertTo-Json -Depth 8 -Compress
        $initResponse = Invoke-LauncherHttp POST $McpUri.AbsoluteUri $mcpHeaders $initBody
        $initialized = ConvertFrom-LauncherRpc $initResponse
        if ($initialized.error -or -not $initialized.result.protocolVersion) { throw 'MCP initialization was rejected.' }
        $sessionId = $initResponse.headers['Mcp-Session-Id']
        if ($sessionId) { $mcpHeaders['Mcp-Session-Id'] = $sessionId }
        $mcpHeaders['MCP-Protocol-Version'] = $initialized.result.protocolVersion
        $notification = @{ jsonrpc = '2.0'; method = 'notifications/initialized' } | ConvertTo-Json -Compress
        $notified = Invoke-LauncherHttp POST $McpUri.AbsoluteUri $mcpHeaders $notification
        if ($notified.status -notin @(200, 202, 204)) { throw 'MCP initialization notification failed.' }
        $call = @{ jsonrpc = '2.0'; id = 2; method = 'tools/call'; params = @{ name = 'debug_status'; arguments = @{} } } |
            ConvertTo-Json -Depth 8 -Compress
        $called = ConvertFrom-LauncherRpc (Invoke-LauncherHttp POST $McpUri.AbsoluteUri $mcpHeaders $call)
        if ($called.error -or $called.result.isError) { throw 'MCP debug_status was rejected.' }
        $status = $called.result.structuredContent
        if (-not $status) {
            $textBlock = $called.result.content | Where-Object { $_.type -eq 'text' } | Select-Object -First 1
            try { $status = $textBlock.text | ConvertFrom-Json } catch { throw 'MCP debug_status returned invalid content.' }
        }
        if ($status.server -ne 'gw-ap-debug-platform' -or -not $status.ready -or
                $status.inference_owner -ne 'host_cli' -or $status.backend_chat_allowed -ne $false -or
                $status.backend_chat_calls -ne 0) {
            throw 'MCP server identity or host-model boundary did not pass verification.'
        }
        if ($restBody.server_id) {
            if ($status.server_instance_id -cne $restBody.server_id) {
                throw 'REST and MCP returned different server identities. Check the gateway routing.'
            }
        }
        return $status
    } finally {
        if ($sessionId) {
            try { $null = Invoke-LauncherHttp DELETE $McpUri.AbsoluteUri $mcpHeaders '' 3 } catch { }
        }
    }
}
