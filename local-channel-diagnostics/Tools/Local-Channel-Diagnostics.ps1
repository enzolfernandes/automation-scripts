[CmdletBinding()]
param(
    [ValidateSet("Interface", "Diagnosticar", "Heartbeat", "HeartbeatProbe", "Sugerir", "Monitorar")]
    [string]$Acao = "Interface",

    [ValidateCount(2, 2)]
    [ValidateRange(1, 65535)]
    [int[]]$Portas = @(3838, 4838),

    [ValidateRange(1024, 49151)]
    [int]$Inicio = 12000,

    [ValidateRange(1024, 49151)]
    [int]$Fim = 48000,

    [ValidateRange(1, 30000)]
    [int]$Diferenca = 1000,

    [ValidateRange(1, 30)]
    [int]$Quantidade = 8,

    [ValidateRange(5, 3600)]
    [int]$Segundos = 60,

    [ValidateRange(1, 30)]
    [int]$IntervaloSegundos = 2,

    [ValidateRange(1, 65535)]
    [int]$PortaTeste = 3838,

    [string]$ProcessoEsperado = "AMPro"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$script:DiagnosticScriptPath = $PSCommandPath

function Assert-NetworkCmdlets {
    $requiredCommands = @("Get-NetTCPConnection", "Get-NetUDPEndpoint")

    foreach ($command in $requiredCommands) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "O comando $command nao esta disponivel nesta versao do Windows/PowerShell."
        }
    }
}

function Get-ProcessInformation {
    param(
        [int[]]$ProcessIds,
        [string]$ExpectedProcess = ""
    )

    $result = foreach ($processId in ($ProcessIds | Where-Object { $_ -gt 0 } | Sort-Object -Unique)) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue

        if ($null -ne $process) {
            $displayName = $process.ProcessName
            if (-not [string]::IsNullOrWhiteSpace($ExpectedProcess) -and $process.ProcessName -like "*$ExpectedProcess*") {
                $displayName = "Processo alvo"
            }

            try {
                $startedAt = $process.StartTime.ToString("yyyy-MM-dd HH:mm:ss")
            }
            catch {
                $startedAt = "inicio desconhecido"
            }

            [PSCustomObject]@{
                Id         = $processId
                Name       = $process.ProcessName
                Responding = $process.Responding
                Label      = "{0} (PID {1}, {2}, responde={3})" -f $displayName, $processId, $startedAt, $process.Responding
            }
        }
        else {
            [PSCustomObject]@{
                Id         = $processId
                Name       = "Desconhecido"
                Responding = $false
                Label      = "PID $processId"
            }
        }
    }

    return @($result)
}

function Test-PortBind {
    param(
        [ValidateRange(1, 65535)]
        [int]$Port,

        [ValidateSet("TCP", "UDP")]
        [string]$Protocol
    )

    $socket = $null

    try {
        if ($Protocol -eq "TCP") {
            $socketType = [System.Net.Sockets.SocketType]::Stream
            $protocolType = [System.Net.Sockets.ProtocolType]::Tcp
        }
        else {
            $socketType = [System.Net.Sockets.SocketType]::Dgram
            $protocolType = [System.Net.Sockets.ProtocolType]::Udp
        }

        $socket = [System.Net.Sockets.Socket]::new(
            [System.Net.Sockets.AddressFamily]::InterNetwork,
            $socketType,
            $protocolType
        )
        $socket.ExclusiveAddressUse = $true
        $endpoint = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Any, $Port)
        $socket.Bind($endpoint)
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $socket) {
            $socket.Dispose()
        }
    }
}

function Get-ApplicationResponseAssessment {
    param(
        [AllowNull()]
        [string]$Response,

        [int]$ElapsedMilliseconds
    )

    $responded = -not [string]::IsNullOrWhiteSpace($Response)
    if ($responded -and $Response -match '(?i)InvalidCMD') {
        return [PSCustomObject]@{
            Responded = $true
            Result = "Inconclusive"
            Diagnosis = "INCONCLUSIVO: canal respondeu em $ElapsedMilliseconds ms; comando de diagnostico nao suportado"
        }
    }
    if ($responded) {
        return [PSCustomObject]@{
            Responded = $true
            Result = "Inconclusive"
            Diagnosis = "INCONCLUSIVO: canal respondeu em $ElapsedMilliseconds ms; conteudo da resposta nao validado"
        }
    }

    return [PSCustomObject]@{
        Responded = $false
        Result = "Failure"
        Diagnosis = "FALHA: servico local conectou, mas nao respondeu ao teste"
    }
}

function Invoke-ApplicationHeartbeat {
    param(
        [ValidateRange(1, 65535)]
        [int]$Port
    )

    $assemblyDirectories = @(
        "C:\Program Files (x86)\Vebko\AMPro Stable",
        "C:\Program Files (x86)\Vebko\AMPro Test"
    )
    $assemblyDirectory = $assemblyDirectories |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ "AMProLibrary.dll") } |
        Select-Object -First 1

    if ([string]::IsNullOrWhiteSpace($assemblyDirectory)) {
        return [PSCustomObject]@{
            Porta       = $Port
            Conectou    = $false
            Respondeu   = $false
            Resultado   = "Failure"
            LatenciaMs  = $null
            Resposta    = ""
            Diagnostico = "FALHA: componente de comunicacao nao encontrado"
            Momento     = Get-Date
        }
    }

    $socket = $null

    try {
        [void][Reflection.Assembly]::LoadFrom((Join-Path $assemblyDirectory "AsyncIO.dll"))
        [void][Reflection.Assembly]::LoadFrom((Join-Path $assemblyDirectory "NetMQ.dll"))
        $assembly = [Reflection.Assembly]::LoadFrom((Join-Path $assemblyDirectory "AMProLibrary.dll"))
        $socketType = $assembly.GetType(
            "AMProLibrary.CommunicationManager.Internal.Controller.ClientSocket",
            $true
        )
        $commandType = $assembly.GetType("AMProLibrary.InterCommunicationCommands", $true)
        $messageSent = [Enum]::Parse($commandType, "MessageSent")

        $socket = [Activator]::CreateInstance($socketType, [object[]]@($Port))
        $connectMethod = $socketType.GetMethod(
            "Connect",
            [Reflection.BindingFlags]"Public,NonPublic,Instance"
        )
        $connectMethod.Invoke($socket, $null) | Out-Null

        $joinProperty = $socketType.GetProperty("IsJoindToSocket")
        $joinDeadline = (Get-Date).AddSeconds(3)
        while (-not $joinProperty.GetValue($socket, $null) -and (Get-Date) -lt $joinDeadline) {
            Start-Sleep -Milliseconds 100
        }

        $joined = [bool]$joinProperty.GetValue($socket, $null)
        if (-not $joined) {
            return [PSCustomObject]@{
                Porta       = $Port
                Conectou    = $false
                Respondeu   = $false
                Aplicavel   = $true
                Resultado   = "Inconclusive"
                LatenciaMs  = $null
                Resposta    = ""
                Diagnostico = "INCONCLUSIVO: cliente de diagnostico nao concluiu a conexao dentro do limite"
                Momento     = Get-Date
            }
        }

        $method = $socketType.GetMethod("SendAndReceiveString")
        $started = Get-Date
        $response = [string]$method.Invoke(
            $socket,
            [object[]]@($messageSent, "LOCAL_CHANNEL_HEALTHCHECK")
        )
        $elapsedMilliseconds = [int]((Get-Date) - $started).TotalMilliseconds
        $assessment = Get-ApplicationResponseAssessment -Response $response -ElapsedMilliseconds $elapsedMilliseconds

        return [PSCustomObject]@{
            Porta       = $Port
            Conectou    = $joined
            Respondeu   = [bool]$assessment.Responded
            Resultado   = [string]$assessment.Result
            LatenciaMs  = $elapsedMilliseconds
            Resposta    = $response
            Diagnostico = [string]$assessment.Diagnosis
            Momento     = Get-Date
        }
    }
    catch {
        $message = $_.Exception.Message
        if ($null -ne $_.Exception.InnerException) {
            $message = $_.Exception.InnerException.Message
        }

        return [PSCustomObject]@{
            Porta       = $Port
            Conectou    = $false
            Respondeu   = $false
            Resultado   = "Failure"
            LatenciaMs  = $null
            Resposta    = ""
            Diagnostico = "FALHA no teste de resposta: $message"
            Momento     = Get-Date
        }
    }
    finally {
        if ($null -ne $socket) {
            try {
                $socket.GetType().GetMethod("Dispose").Invoke($socket, $null) | Out-Null
            }
            catch {
            }
        }
    }
}

function Invoke-ApplicationHeartbeatWithTimeout {
    param(
        [ValidateRange(1, 65535)]
        [int]$Port,

        [ValidateRange(2, 30)]
        [int]$TimeoutSeconds = 6,

        [scriptblock]$CancellationCheck
    )

    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $process = $null
    try {
        $powerShellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $quotedScriptPath = '"' + $script:DiagnosticScriptPath.Replace('"', '""') + '"'
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $powerShellPath
        $startInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File $quotedScriptPath -Acao HeartbeatProbe -PortaTeste $Port"
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "Nao foi possivel iniciar o teste isolado."
        }

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
            [System.Windows.Forms.Application]::DoEvents()
            if ($null -ne $CancellationCheck -and (& $CancellationCheck)) {
                try {
                    $process.Kill()
                    $process.WaitForExit(1500) | Out-Null
                }
                catch {
                }

                return [PSCustomObject]@{
                    Porta       = $Port
                    Conectou    = $false
                    Respondeu   = $false
                    Aplicavel   = $false
                    Cancelado   = $true
                    Tipo        = "Service"
                    Resultado   = "Canceled"
                    LatenciaMs  = $null
                    Resposta    = ""
                    Diagnostico = "CANCELADO: verificacao interrompida pelo usuario"
                    Momento     = Get-Date
                }
            }
            Start-Sleep -Milliseconds 50
        }

        if (-not $process.HasExited) {
            try {
                $process.Kill()
                $process.WaitForExit(1500) | Out-Null
            }
            catch {
            }

            return [PSCustomObject]@{
                Porta       = $Port
                Conectou    = $false
                Respondeu   = $false
                Aplicavel   = $true
                Cancelado   = $false
                Tipo        = "Service"
                Resultado   = "Failure"
                LatenciaMs  = $null
                Resposta    = ""
                Diagnostico = "FALHA: tempo limite de $TimeoutSeconds segundos excedido"
                Momento     = Get-Date
            }
        }

        $standardOutput = $process.StandardOutput.ReadToEnd().Trim()
        $standardError = $process.StandardError.ReadToEnd().Trim()
        if ($process.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($standardOutput)) {
            if ([string]::IsNullOrWhiteSpace($standardError)) {
                $standardError = "teste isolado encerrado sem resultado"
            }
            throw $standardError
        }

        $probeResult = $standardOutput | ConvertFrom-Json
        return [PSCustomObject]@{
            Porta       = [int]$probeResult.Porta
            Conectou    = [bool]$probeResult.Conectou
            Respondeu   = [bool]$probeResult.Respondeu
            Aplicavel   = $true
            Cancelado   = $false
            Tipo        = "Service"
            Resultado   = if ($probeResult.PSObject.Properties.Name -contains "Resultado") { [string]$probeResult.Resultado } elseif ([bool]$probeResult.Respondeu) { "Inconclusive" } else { "Failure" }
            LatenciaMs  = if ($null -eq $probeResult.LatenciaMs) { $null } else { [int]$probeResult.LatenciaMs }
            Resposta    = [string]$probeResult.Resposta
            Diagnostico = [string]$probeResult.Diagnostico
            Momento     = Get-Date
        }
    }
    catch {
        return [PSCustomObject]@{
            Porta       = $Port
            Conectou    = $false
            Respondeu   = $false
            Aplicavel   = $true
            Cancelado   = $false
            Tipo        = "Service"
            Resultado   = "Failure"
            LatenciaMs  = $null
            Resposta    = ""
            Diagnostico = "FALHA no teste de resposta: $($_.Exception.Message)"
            Momento     = Get-Date
        }
    }
    finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
    }
}

function Invoke-GenericTcpProbe {
    param(
        [ValidateRange(1, 65535)]
        [int]$Port,

        [ValidateRange(1, 10)]
        [int]$TimeoutSeconds = 2,

        [scriptblock]$CancellationCheck
    )

    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $asyncResult = $client.BeginConnect([System.Net.IPAddress]::Loopback, $Port, $null, $null)
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while (-not $asyncResult.IsCompleted -and (Get-Date) -lt $deadline) {
            [System.Windows.Forms.Application]::DoEvents()
            if ($null -ne $CancellationCheck -and (& $CancellationCheck)) {
                return [PSCustomObject]@{
                    Porta = $Port; Conectou = $false; Respondeu = $false; Aplicavel = $false; Cancelado = $true; Tipo = "Tcp"
                    LatenciaMs = $null; Resposta = ""; Resultado = "Canceled"; Diagnostico = "CANCELADO: verificacao interrompida pelo usuario"; Momento = Get-Date
                }
            }
            Start-Sleep -Milliseconds 40
        }

        if (-not $asyncResult.IsCompleted) {
            return [PSCustomObject]@{
                Porta = $Port; Conectou = $false; Respondeu = $false; Aplicavel = $true; Cancelado = $false; Tipo = "Tcp"
                LatenciaMs = $null; Resposta = ""; Resultado = "Failure"; Diagnostico = "FALHA TCP: nenhuma escuta respondeu dentro do limite"; Momento = Get-Date
            }
        }

        $client.EndConnect($asyncResult)
        return [PSCustomObject]@{
            Porta = $Port; Conectou = $true; Respondeu = $true; Aplicavel = $true; Cancelado = $false; Tipo = "Tcp"
            LatenciaMs = $null; Resposta = ""; Resultado = "Success"; Diagnostico = "TCP GENERICO: conexao local aceita; protocolo nao testado"; Momento = Get-Date
        }
    }
    catch {
        return [PSCustomObject]@{
            Porta = $Port; Conectou = $false; Respondeu = $false; Aplicavel = $true; Cancelado = $false; Tipo = "Tcp"
            LatenciaMs = $null; Resposta = ""; Resultado = "Failure"; Diagnostico = "FALHA TCP: nenhuma escuta aceitou a conexao"; Momento = Get-Date
        }
    }
    finally {
        $client.Close()
        $client.Dispose()
    }
}

function Test-IsAdministrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $false
    }
}

function Get-ExcludedPortRanges {
    param([ValidateSet("tcp", "udp")][string]$Protocol)

    $ranges = @()
    try {
        $lines = @(& "$env:SystemRoot\System32\netsh.exe" interface ipv4 show excludedportrange protocol=$Protocol 2>$null)
        foreach ($line in $lines) {
            if ($line -match '^\s*(\d+)\s+(\d+)') {
                $ranges += [PSCustomObject]@{ Start = [int]$Matches[1]; End = [int]$Matches[2] }
            }
        }
    }
    catch {
    }
    return @($ranges)
}

function Test-PortInRanges {
    param([int]$Port, [object[]]$Ranges)
    return @($Ranges | Where-Object { $Port -ge $_.Start -and $Port -le $_.End }).Count -gt 0
}

function Test-PortSpecification {
    param([int]$Port, [string]$Specification)

    if ([string]::IsNullOrWhiteSpace($Specification) -or $Specification -eq "Any") {
        return $false
    }
    foreach ($part in $Specification.Split(',')) {
        $trimmed = $part.Trim()
        if ($trimmed -match '^(\d+)-(\d+)$' -and $Port -ge [int]$Matches[1] -and $Port -le [int]$Matches[2]) {
            return $true
        }
        if ($trimmed -match '^\d+$' -and $Port -eq [int]$trimmed) {
            return $true
        }
    }
    return $false
}

function Get-WindowsResourceContext {
    $firewallFilters = @()
    $firewallError = ""
    if (Get-Command Get-NetFirewallPortFilter -ErrorAction SilentlyContinue) {
        try {
            $firewallFilters = @(Get-NetFirewallPortFilter -PolicyStore ActiveStore -ErrorAction Stop)
        }
        catch {
            $firewallError = $_.Exception.Message
        }
    }
    else {
        $firewallError = "cmdlet indisponivel"
    }

    $portProxyLines = @()
    try {
        $portProxyLines = @(& "$env:SystemRoot\System32\netsh.exe" interface portproxy show all 2>$null)
    }
    catch {
    }

    return [PSCustomObject]@{
        IsAdministrator = Test-IsAdministrator
        TcpExcluded     = @(Get-ExcludedPortRanges -Protocol tcp)
        UdpExcluded     = @(Get-ExcludedPortRanges -Protocol udp)
        FirewallFilters = $firewallFilters
        FirewallError   = $firewallError
        PortProxyLines  = $portProxyLines
    }
}

function Get-WindowsPortResourceInfo {
    param(
        [int]$Port,
        [object]$Context,
        [string]$ProcessSummary
    )

    $matchingFilters = @($Context.FirewallFilters | Where-Object {
        Test-PortSpecification -Port $Port -Specification ([string]$_.LocalPort)
    })
    $firewallNames = @()
    foreach ($filter in $matchingFilters | Select-Object -First 8) {
        try {
            $rules = @(Get-NetFirewallRule -AssociatedNetFirewallPortFilter $filter -ErrorAction Stop)
            $firewallNames += @($rules | ForEach-Object { "$($_.DisplayName) [$($_.Direction)/$($_.Action)]" })
        }
        catch {
        }
    }

    $proxyEntries = @($Context.PortProxyLines | Where-Object {
        $parts = @($_ -split '\s+' | Where-Object { $_ })
        $parts.Count -ge 4 -and $parts[1] -eq [string]$Port
    })

    return [PSCustomObject]@{
        Porta          = $Port
        Processo       = $ProcessSummary
        TcpExcluida    = Test-PortInRanges -Port $Port -Ranges $Context.TcpExcluded
        UdpExcluida    = Test-PortInRanges -Port $Port -Ranges $Context.UdpExcluded
        RegrasFirewall = @($firewallNames | Sort-Object -Unique)
        FirewallErro   = [string]$Context.FirewallError
        PortProxy      = @($proxyEntries)
        Administrador  = [bool]$Context.IsAdministrator
    }
}

function Get-PortSnapshot {
    param(
        [ValidateRange(1, 65535)]
        [int]$Port,

        [string]$ExpectedProcess
    )

    $tcpConnections = @(Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue)
    $tcpClientConnections = @(
        Get-NetTCPConnection -RemotePort $Port -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq "Established" }
    )
    $udpEndpoints = @(Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue)

    $processIds = @(
        $tcpConnections | ForEach-Object { $_.OwningProcess }
        $udpEndpoints | ForEach-Object { $_.OwningProcess }
    )
    $processes = @(Get-ProcessInformation -ProcessIds $processIds -ExpectedProcess $ExpectedProcess)
    $clientProcesses = @(
        Get-ProcessInformation -ProcessIds @(
            $tcpClientConnections | ForEach-Object { $_.OwningProcess }
        ) -ExpectedProcess $ExpectedProcess
    )

    $tcpBindable = $false
    $udpBindable = $false

    if ($tcpConnections.Count -eq 0) {
        $tcpBindable = Test-PortBind -Port $Port -Protocol TCP
    }

    if ($udpEndpoints.Count -eq 0) {
        $udpBindable = Test-PortBind -Port $Port -Protocol UDP
    }

    if ($tcpConnections.Count -gt 0) {
        $tcpStatus = ($tcpConnections.State | Sort-Object -Unique) -join ","
    }
    elseif ($tcpBindable) {
        $tcpStatus = "Livre"
    }
    else {
        $tcpStatus = "Reservada/Bloqueada"
    }

    if ($udpEndpoints.Count -gt 0) {
        $udpStatus = "Em uso"
    }
    elseif ($udpBindable) {
        $udpStatus = "Livre"
    }
    else {
        $udpStatus = "Reservada/Bloqueada"
    }

    $expectedOwners = @($processes | Where-Object { $_.Name -like "*$ExpectedProcess*" })
    $unexpectedOwners = @($processes | Where-Object { $_.Name -notlike "*$ExpectedProcess*" })
    $expectedClients = @($clientProcesses | Where-Object { $_.Name -like "*$ExpectedProcess*" })
    $unexpectedClients = @($clientProcesses | Where-Object { $_.Name -notlike "*$ExpectedProcess*" })
    $unresponsiveClients = @($expectedClients | Where-Object { -not $_.Responding })

    $hasEstablishedConnection = @($tcpConnections | Where-Object { $_.State -eq "Established" }).Count -gt 0
    $hasListeningSocket = @($tcpConnections | Where-Object { $_.State -eq "Listen" }).Count -gt 0

    $hasExpectedEstablishedConnection = $hasEstablishedConnection -and
        $expectedOwners.Count -gt 0 -and
        $expectedClients.Count -gt 0

    if ($unexpectedOwners.Count -gt 0 -or $unexpectedClients.Count -gt 0) {
        $diagnosis = "CONFLITO: outro processo e o proprietario"
    }
    elseif ($unresponsiveClients.Count -gt 0) {
        $diagnosis = "FALHA: um processo cliente nao responde ao Windows"
    }
    elseif ($expectedOwners.Count -gt 0) {
        if ($hasExpectedEstablishedConnection) {
            $diagnosis = "CANAL REAL: conexao esperada estabelecida; teste ativo nao necessario"
        }
        elseif ($hasListeningSocket) {
            $diagnosis = "ATENCAO: servico local escutando, mas sem cliente conectado"
        }
        else {
            $diagnosis = "ATENCAO: porta assumida pelo servico, sem conexao TCP ativa"
        }
    }
    elseif ($tcpBindable -and $udpBindable) {
        $diagnosis = "LIVRE: candidata para configuracao"
    }
    else {
        $diagnosis = "INDISPONIVEL: ocupada, reservada ou bloqueada"
    }

    [PSCustomObject]@{
        Porta       = $Port
        TCP         = $tcpStatus
        UDP         = $udpStatus
        Processos   = "Servidor: {0} | Clientes: {1}" -f `
            (($processes | ForEach-Object { $_.Label }) -join "; "), `
            (($clientProcesses | ForEach-Object { $_.Label }) -join "; ")
        Diagnostico = $diagnosis
        CanalRealEstabelecido = $hasExpectedEstablishedConnection
        EscutaAtiva = $hasListeningSocket
    }
}

function Show-PortDiagnosis {
    param(
        [int[]]$Ports,
        [string]$ExpectedProcess
    )

    $snapshot = foreach ($port in $Ports) {
        Get-PortSnapshot -Port $port -ExpectedProcess $ExpectedProcess
    }

    $snapshot | Format-Table Porta, TCP, UDP, Processos, Diagnostico -AutoSize -Wrap | Out-Host
    return @($snapshot)
}

function Show-TargetProcesses {
    param([string]$ProcessNameFilter)

    $processes = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -like "*$ProcessNameFilter*" } |
            Sort-Object ProcessName, Id
    )

    if ($processes.Count -eq 0) {
        Write-Host "Nenhum processo alvo esta em execucao."
        return
    }

    $rows = foreach ($process in $processes) {
        try {
            $startedAt = $process.StartTime.ToString("yyyy-MM-dd HH:mm:ss")
        }
        catch {
            $startedAt = "Desconhecido"
        }

        [PSCustomObject]@{
            Processo = "Processo alvo"
            PID      = $process.Id
            Inicio   = $startedAt
        }
    }

    Write-Host "Processos alvo encontrados:"
    $rows | Format-Table Processo, PID, Inicio -AutoSize | Out-Host

    $serverProcesses = @($processes | Where-Object { $_.ProcessName -like "*AppLuncher*" })
    if ($serverProcesses.Count -gt 1) {
        Write-Host "ALERTA: ha mais de um processo servidor. Uma instancia antiga pode estar segurando as portas."
    }
}

function Test-CandidatePort {
    param([int]$Port)

    return (
        (Test-PortBind -Port $Port -Protocol TCP) -and
        (Test-PortBind -Port $Port -Protocol UDP)
    )
}

function Find-PortPairs {
    param(
        [int]$StartPort,
        [int]$EndPort,
        [int]$Offset,
        [int]$Count
    )

    $found = 0

    for ($firstPort = $StartPort; ($firstPort + $Offset) -le $EndPort; $firstPort += 100) {
        $secondPort = $firstPort + $Offset

        if ((Test-CandidatePort -Port $firstPort) -and (Test-CandidatePort -Port $secondPort)) {
            [PSCustomObject]@{
                Porta1   = $firstPort
                Porta2   = $secondPort
                Diferenca = $Offset
                Estado   = "Livre em TCP e UDP neste momento"
            }

            $found++
            if ($found -ge $Count) {
                break
            }
        }
    }
}

function Get-SnapshotSignature {
    param([object[]]$Snapshot)

    return (($Snapshot | ForEach-Object {
        "{0}|{1}|{2}|{3}|{4}" -f $_.Porta, $_.TCP, $_.UDP, $_.Processos, $_.Diagnostico
    }) -join "||")
}

function Watch-PortPair {
    param(
        [int[]]$Ports,
        [string]$ExpectedProcess,
        [int]$DurationSeconds,
        [int]$RefreshSeconds
    )

    Write-Host "Monitorando as portas $($Ports -join ', ') por $DurationSeconds segundos."
    Write-Host "Execute uma operacao no aplicativo durante o monitoramento. Somente mudancas serao exibidas."

    $endTime = (Get-Date).AddSeconds($DurationSeconds)
    $lastSignature = $null

    while ((Get-Date) -lt $endTime) {
        $snapshot = @(
            foreach ($port in $Ports) {
                Get-PortSnapshot -Port $port -ExpectedProcess $ExpectedProcess
            }
        )
        $signature = Get-SnapshotSignature -Snapshot $snapshot

        if ($signature -ne $lastSignature) {
            Write-Host ""
            Write-Host (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
            $snapshot | Format-Table Porta, TCP, UDP, Processos, Diagnostico -AutoSize -Wrap
            $lastSignature = $signature
        }

        Start-Sleep -Seconds $RefreshSeconds
    }

    Write-Host "Monitoramento concluido."
    Write-Host "TCP Listen = o servico abriu a porta; TCP Established = houve conexao ativa."
    Write-Host "Para UDP, 'Em uso' confirma a abertura, mas nao confirma trafego de dados."
}

function New-DataGridView {
    param(
        [string[]]$Columns,
        [System.Drawing.Point]$Location,
        [System.Drawing.Size]$Size
    )

    $grid = [System.Windows.Forms.DataGridView]::new()
    $grid.Location = $Location
    $grid.Size = $Size
    $grid.Anchor = "Top,Bottom,Left,Right"
    $grid.AllowUserToAddRows = $false
    $grid.AllowUserToDeleteRows = $false
    $grid.AllowUserToResizeRows = $true
    $grid.AutoSizeColumnsMode = "Fill"
    $grid.AutoSizeRowsMode = "AllCellsExceptHeaders"
    $grid.BackgroundColor = [System.Drawing.Color]::White
    $grid.BorderStyle = "Fixed3D"
    $grid.ColumnHeadersHeightSizeMode = "AutoSize"
    $grid.MultiSelect = $false
    $grid.ReadOnly = $true
    $grid.RowHeadersVisible = $false
    $grid.RowTemplate.MinimumHeight = 28
    $grid.SelectionMode = "FullRowSelect"
    $grid.DefaultCellStyle.WrapMode = "True"
    $grid.DefaultCellStyle.Padding = [System.Windows.Forms.Padding]::new(3, 2, 3, 2)
    $grid.ColumnHeadersDefaultCellStyle.WrapMode = "True"

    foreach ($columnName in $Columns) {
        $null = $grid.Columns.Add($columnName, $columnName)
    }

    return $grid
}

function New-StatusCard {
    param(
        [string]$Title,
        [System.Drawing.Point]$Location,
        [System.Drawing.Size]$Size
    )

    $panel = [System.Windows.Forms.Panel]::new()
    $panel.Location = $Location
    $panel.Size = $Size
    $panel.BackColor = [System.Drawing.Color]::White
    $panel.BorderStyle = "FixedSingle"
    $panel.Anchor = "Top,Left"

    $titleLabel = [System.Windows.Forms.Label]::new()
    $titleLabel.Text = $Title
    $titleLabel.AutoSize = $true
    $titleLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 9)
    $titleLabel.ForeColor = [System.Drawing.Color]::FromArgb(75, 85, 99)
    $titleLabel.Location = [System.Drawing.Point]::new(12, 9)
    $panel.Controls.Add($titleLabel)

    $stateLabel = [System.Windows.Forms.Label]::new()
    $stateLabel.Text = "NÃO VERIFICADO"
    $stateLabel.AutoSize = $true
    $stateLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 12)
    $stateLabel.ForeColor = [System.Drawing.Color]::FromArgb(100, 116, 139)
    $stateLabel.Location = [System.Drawing.Point]::new(12, 31)
    $panel.Controls.Add($stateLabel)

    $detailLabel = [System.Windows.Forms.Label]::new()
    $detailLabel.Text = "Aguardando diagnóstico"
    $detailLabel.AutoEllipsis = $true
    $detailLabel.ForeColor = [System.Drawing.Color]::DimGray
    $detailLabel.Location = [System.Drawing.Point]::new(12, 57)
    $detailLabel.Size = [System.Drawing.Size]::new($Size.Width - 25, 18)
    $detailLabel.Anchor = "Top,Left,Right"
    $panel.Controls.Add($detailLabel)

    return [PSCustomObject]@{
        Panel  = $panel
        Title  = $titleLabel
        State  = $stateLabel
        Detail = $detailLabel
    }
}

function Set-StatusCardState {
    param(
        [object]$Card,
        [string]$State,
        [string]$Detail,
        [ValidateSet("Neutral", "Info", "Success", "Warning", "Error")]
        [string]$Level = "Neutral"
    )

    $colors = @{
        Neutral = [System.Drawing.Color]::FromArgb(100, 116, 139)
        Info    = [System.Drawing.Color]::FromArgb(26, 115, 181)
        Success = [System.Drawing.Color]::FromArgb(42, 135, 84)
        Warning = [System.Drawing.Color]::FromArgb(180, 110, 15)
        Error   = [System.Drawing.Color]::FromArgb(185, 45, 45)
    }

    $Card.State.Text = $State
    $Card.State.ForeColor = $colors[$Level]
    $Card.Detail.Text = $Detail
}

function Convert-DiagnosisText {
    param(
        [string]$Text,
        [ValidateSet("pt-BR", "en-US")]
        [string]$Language
    )

    if ($Language -eq "en-US") {
        switch -Regex ($Text) {
            '^FALHA: componente de comunicacao nao encontrado$' { return "FAILURE: communication component not found" }
            '^FALHA: servico local nao aceitou o cliente de diagnostico$' { return "INCONCLUSIVE: diagnostic client did not complete the connection within the limit" }
            '^INCONCLUSIVO: cliente de diagnostico nao concluiu a conexao dentro do limite$' { return "INCONCLUSIVE: diagnostic client did not complete the connection within the limit" }
            '^INCONCLUSIVO: canal respondeu em (\d+) ms; comando de diagnostico nao suportado$' { return "INCONCLUSIVE: channel responded in $($Matches[1]) ms; diagnostic command is not supported" }
            '^INCONCLUSIVO: canal respondeu em (\d+) ms; conteudo da resposta nao validado$' { return "INCONCLUSIVE: channel responded in $($Matches[1]) ms; response content was not validated" }
            '^INCONCLUSIVO: teste ativo nao concluiu; canal real permanece conectado$' { return "INCONCLUSIVE: active test did not complete; the real channel remains connected" }
            '^INCONCLUSIVO: falha isolada do teste ativo \((\d+)/3\); repetir para confirmar$' { return "INCONCLUSIVE: isolated active-test failure ($($Matches[1])/3); repeat to confirm" }
            '^CAMADA APP: servico local respondeu em (\d+) ms; operacao funcional nao comprovada$' { return "APP LAYER: local service responded in $($Matches[1]) ms; functional operation not confirmed" }
            '^FALHA: servico local conectou, mas nao respondeu ao teste$' { return "FAILURE: local service connected but did not answer the test" }
            '^FALHA: tempo limite de (\d+) segundos excedido$' { return "FAILURE: $($Matches[1])-second timeout exceeded" }
            '^FALHA no teste de resposta: (.*)$' { return "RESPONSE TEST FAILURE: $($Matches[1])" }
            '^CONFLITO: outro processo e o proprietario$' { return "CONFLICT: another process owns the port" }
            '^FALHA: um processo cliente nao responde ao Windows$' { return "FAILURE: a client process is not responding to Windows" }
            '^SOCKET: TCP conectado; operacao nao comprovada$' { return "SOCKET: TCP connected; operation not confirmed" }
            '^CANAL REAL: conexao esperada estabelecida; teste ativo nao necessario$' { return "REAL CHANNEL: expected connection established; active test not required" }
            '^CANAL REAL: conexao esperada estabelecida; teste ativo inconclusivo$' { return "REAL CHANNEL: expected connection established; active test was inconclusive" }
            '^INCONCLUSIVO: canal real nao foi observado conectado$' { return "INCONCLUSIVE: no connected real channel was observed" }
            '^ATENCAO: servico local escutando, mas sem cliente conectado$' { return "WARNING: local service is listening, but no client is connected" }
            '^ATENCAO: porta assumida pelo servico, sem conexao TCP ativa$' { return "WARNING: port owned by the service, without an active TCP connection" }
            '^LIVRE: candidata para configuracao$' { return "FREE: candidate for configuration" }
            '^DISPONIVEL: porta livre; teste de servico nao aplicavel$' { return "AVAILABLE: free port; service test does not apply yet" }
            '^TCP GENERICO: conexao local aceita; protocolo nao testado$' { return "GENERIC TCP: local connection accepted; protocol not tested" }
            '^FALHA TCP: nenhuma escuta respondeu dentro do limite$' { return "TCP FAILURE: no listener responded within the timeout" }
            '^FALHA TCP: nenhuma escuta aceitou a conexao$' { return "TCP FAILURE: no listener accepted the connection" }
            '^CANCELADO: verificacao interrompida pelo usuario$' { return "CANCELED: verification interrupted by the user" }
            '^INDISPONIVEL: ocupada, reservada ou bloqueada$' { return "UNAVAILABLE: busy, reserved, or blocked" }
            default { return $Text }
        }
    }

    switch -Regex ($Text) {
        '^FALHA: componente de comunicacao nao encontrado$' { return "FALHA: componente de comunicação não encontrado" }
        '^FALHA: servico local nao aceitou o cliente de diagnostico$' { return "INCONCLUSIVO: cliente de diagnóstico não concluiu a conexão dentro do limite" }
        '^INCONCLUSIVO: cliente de diagnostico nao concluiu a conexao dentro do limite$' { return "INCONCLUSIVO: cliente de diagnóstico não concluiu a conexão dentro do limite" }
        '^INCONCLUSIVO: canal respondeu em (\d+) ms; comando de diagnostico nao suportado$' { return "INCONCLUSIVO: canal respondeu em $($Matches[1]) ms; comando de diagnóstico não suportado" }
        '^INCONCLUSIVO: canal respondeu em (\d+) ms; conteudo da resposta nao validado$' { return "INCONCLUSIVO: canal respondeu em $($Matches[1]) ms; conteúdo da resposta não validado" }
        '^INCONCLUSIVO: teste ativo nao concluiu; canal real permanece conectado$' { return "INCONCLUSIVO: teste ativo não concluiu; o canal real permanece conectado" }
        '^INCONCLUSIVO: falha isolada do teste ativo \((\d+)/3\); repetir para confirmar$' { return "INCONCLUSIVO: falha isolada do teste ativo ($($Matches[1])/3); repita para confirmar" }
        '^CAMADA APP: servico local respondeu em (\d+) ms; operacao funcional nao comprovada$' { return "CAMADA DO APLICATIVO: serviço local respondeu em $($Matches[1]) ms; operação funcional não comprovada" }
        '^FALHA: servico local conectou, mas nao respondeu ao teste$' { return "FALHA: serviço local conectou, mas não respondeu ao teste" }
        '^FALHA: tempo limite de (\d+) segundos excedido$' { return "FALHA: tempo limite de $($Matches[1]) segundos excedido" }
        '^FALHA no teste de resposta: (.*)$' { return "FALHA no teste de resposta: $($Matches[1])" }
        '^CONFLITO: outro processo e o proprietario$' { return "CONFLITO: outro processo é o proprietário" }
        '^FALHA: um processo cliente nao responde ao Windows$' { return "FALHA: um processo cliente não responde ao Windows" }
        '^SOCKET: TCP conectado; operacao nao comprovada$' { return "SOCKET: TCP conectado; operação não comprovada" }
        '^CANAL REAL: conexao esperada estabelecida; teste ativo nao necessario$' { return "CANAL REAL: conexão esperada estabelecida; teste ativo não necessário" }
        '^CANAL REAL: conexao esperada estabelecida; teste ativo inconclusivo$' { return "CANAL REAL: conexão esperada estabelecida; teste ativo inconclusivo" }
        '^INCONCLUSIVO: canal real nao foi observado conectado$' { return "INCONCLUSIVO: nenhum canal real conectado foi observado" }
        '^ATENCAO: servico local escutando, mas sem cliente conectado$' { return "ATENÇÃO: serviço local escutando, mas sem cliente conectado" }
        '^ATENCAO: porta assumida pelo servico, sem conexao TCP ativa$' { return "ATENÇÃO: porta assumida pelo serviço, sem conexão TCP ativa" }
        '^LIVRE: candidata para configuracao$' { return "LIVRE: candidata para configuração" }
        '^DISPONIVEL: porta livre; teste de servico nao aplicavel$' { return "DISPONÍVEL: porta livre; teste do serviço ainda não aplicável" }
        '^TCP GENERICO: conexao local aceita; protocolo nao testado$' { return "TCP GENÉRICO: conexão local aceita; protocolo não testado" }
        '^FALHA TCP: nenhuma escuta respondeu dentro do limite$' { return "FALHA TCP: nenhuma escuta respondeu dentro do limite" }
        '^FALHA TCP: nenhuma escuta aceitou a conexao$' { return "FALHA TCP: nenhuma escuta aceitou a conexão" }
        '^CANCELADO: verificacao interrompida pelo usuario$' { return "CANCELADO: verificação interrompida pelo usuário" }
        '^INDISPONIVEL: ocupada, reservada ou bloqueada$' { return "INDISPONÍVEL: ocupada, reservada ou bloqueada" }
        default { return $Text }
    }
}

function Convert-PortStateText {
    param([string]$Text, [string]$Language)

    if ($Language -eq "en-US") {
        return $Text.Replace("Livre", "Free").Replace("Reservada/Bloqueada", "Reserved/Blocked").Replace("Em uso", "In use")
    }

    return $Text
}

function Convert-ProcessDisplayText {
    param([string]$Text, [string]$Language)

    if ($Language -eq "en-US") {
        return $Text.Replace("Servidor:", "Server:").Replace("Clientes:", "Clients:").Replace("Processo alvo", "Target process").Replace("responde=", "responding=").Replace("inicio desconhecido", "unknown start").Replace("início desconhecido", "unknown start")
    }

    return $Text.Replace("inicio desconhecido", "início desconhecido")
}

function Get-LocalChannelSettingsPath {
    return Join-Path $env:APPDATA "LocalChannelDoctor\settings.json"
}

function Read-LocalChannelSettings {
    $path = Get-LocalChannelSettingsPath
    if (-not (Test-Path -LiteralPath $path)) {
        return $null
    }

    try {
        return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Save-LocalChannelSettings {
    param(
        [int]$Port1,
        [int]$Port2,
        [int]$StartPort,
        [int]$Offset,

        [ValidateSet("pt-BR", "en-US")]
        [string]$Language = "pt-BR",

        [ValidateSet("Full", "Availability", "Tcp", "Service")]
        [string]$Mode = "Full"
    )

    $path = Get-LocalChannelSettingsPath
    $directory = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    [PSCustomObject]@{
        Port1     = $Port1
        Port2     = $Port2
        StartPort = $StartPort
        Offset    = $Offset
        Language  = $Language
        Mode      = $Mode
    } | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
}

function Show-LocalChannelInterface {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $storedSettings = Read-LocalChannelSettings
    $initialLanguage = "pt-BR"
    if ($null -ne $storedSettings -and $storedSettings.PSObject.Properties.Name -contains "Language") {
        if ([string]$storedSettings.Language -eq "en-US") {
            $initialLanguage = "en-US"
        }
    }

    $uiState = @{
        Functional           = "NaoVerificado"
        LastHistorySignature = ""
        ForceHistoryEntry    = $false
        SuppressHistory      = $false
        LastSnapshot         = @()
        Language             = $initialLanguage
        CancelRequested      = $false
        BeforeSnapshot       = @()
        BeforeCapturedAt     = $null
        ComparisonRows       = @()
        WindowsResources     = @()
        Mode                 = "Full"
        ApplyingLanguage     = $false
        ProbeFailureStreak   = @{}
    }
    if ($null -ne $storedSettings -and $storedSettings.PSObject.Properties.Name -contains "Mode") {
        if ([string]$storedSettings.Mode -in @("Full", "Availability", "Tcp", "Service")) {
            $uiState.Mode = [string]$storedSettings.Mode
        }
    }

    $translations = @{
        "pt-BR" = @{
            AppTitle = "Diagnóstico de Canais Locais"; Subtitle = "Verificação em camadas: transporte, comunicação observada e operação funcional"; Language = "Idioma"
            TransportTitle = "1. CAMADA DE TRANSPORTE"; ServiceTitle = "2. TESTE DE COMUNICAÇÃO"; FunctionalTitle = "3. TESTE FUNCIONAL"
            Worked = "Funcionou"; Failed = "Falhou"; Overview = "Visão geral"; Technical = "Detalhes técnicos"; WindowsResources = "Recursos do Windows"; Comparison = "Antes e depois"
            PortsGroup = "Portas configuradas no aplicativo"; Port1 = "Porta 1"; Port2 = "Porta 2"; VerifyNow = "Verificar agora"
            StartMonitoring = "Iniciar monitoramento"; StopMonitoring = "Parar monitoramento"; Cancel = "Cancelar"; Canceling = "Cancelando..."; PortsHint = "Use os mesmos valores configurados no aplicativo analisado."; DiagnosticMode = "Modo"; ModeFull = "Seguro (recomendado)"; ModeAvailability = "Somente portas"; ModeTcp = "Conexão TCP"; ModeService = "Teste ativo (avançado)"
            SuggestGroup = "Localizar novo par de portas"; Start = "Início"; Difference = "Diferença"; Suggestions = "Sugestões"
            FindPairs = "Procurar pares livres"; UsePair = "Usar par selecionado"; SuggestHint = "A ferramenta encontra e valida; a alteração deve ser aplicada no aplicativo."
            PortStatus = "Estado das portas"; SuggestedPairs = "Pares sugeridos"; Port = "Porta"; TCP = "TCP"; UDP = "UDP"; Processes = "Processos"; Diagnosis = "Diagnóstico"; State = "Estado"
            CopyPair = "Copiar par"; ExportReport = "Exportar relatório"; NextAction = "Conclusão simples e próxima ação"; Ready = "Pronto para diagnosticar."
            History = "Histórico desta sessão"; ClearHistory = "Limpar histórico da sessão"; NotVerified = "NÃO VERIFICADO"; WaitingDiagnosis = "Aguardando diagnóstico"
            TransportFailure = "FALHA"; TransportFailureDetail = "Conflito ou processo sem resposta"; Connected = "CONECTADO"; TwoTcpChannels = "Dois canais TCP estabelecidos"
            NoTarget = "Nenhum processo alvo em execução"; Warning = "ATENÇÃO"; NotAllChannels = "Nem todos os canais estão estabelecidos"; Available = "DISPONÍVEL"; BothPortsFree = "As duas portas estão livres e aguardam configuração"; OnePortFree = "Uma das portas está livre e aguarda configuração"
            Responding = "RESPONDENDO"; ResponseLatency = "Teste concluído; maior latência: {0} ms"; IncompleteResponse = "Resposta incompleta em um ou mais canais"; UseVerify = "Use o botão Verificar agora"; AwaitingConfiguration = "AGUARDANDO CONFIGURAÇÃO"; FreePortsNeedConfig = "Portas livres; aplique a configuração antes de testar o serviço"; Partial = "PARCIAL"; OnePortAwaiting = "Um canal respondeu e o outro aguarda configuração"
            ObservedOperating = "OPERANTE POR OBSERVAÇÃO"; RealChannelsObserved = "Os dois canais reais estão conectados; nenhum cliente de teste adicional foi aberto"; InconclusiveState = "INCONCLUSIVO"; ActiveTestInconclusive = "O teste auxiliar não comprovou falha no canal real"; PassiveSafeAction = "Os canais reais estão conectados. Confirme o funcionamento no teste funcional; o teste ativo foi dispensado."; ActiveAdvancedAction = "Teste ativo avançado concluído. Resultados inconclusivos não substituem a conexão real nem o teste funcional."
            Working = "FUNCIONANDO"; ConfirmedByUser = "Confirmado pelo usuário nesta sessão"; FunctionalFailure = "FALHOU"; RealOperationFailure = "Falha confirmada em uma operação real"; ExecuteRealOperation = "Execute uma operação real e informe o resultado"
            FailureOrConflict = "Falha ou conflito detectado. Consulte o diagnóstico e os processos exibidos na tabela."; MultipleServers = "Mais de um processo servidor está aberto. Uma instância antiga pode estar utilizando as portas."
            ServiceRespondedAction = "O serviço respondeu. Execute uma operação real no aplicativo e registre o resultado no terceiro indicador."; InfrastructureFunctionalFailure = "A infraestrutura respondeu, mas o teste funcional falhou. Exporte o relatório e investigue a camada da aplicação."
            ThreeValidated = "As três etapas foram validadas nesta sessão. Continue monitorando se a falha for intermitente."; TcpConnectedAction = "TCP conectado, mas a operação ainda não foi comprovada. Use Verificar agora."
            OpenApplication = "Abra o aplicativo que utiliza os canais e execute o diagnóstico novamente."; DiagnosisComplete = "Diagnóstico concluído. Verifique os estados individuais apresentados na tabela."; DiagnosisError = "Erro no diagnóstico: {0}"; FreePortAction = "Uma ou mais portas estão disponíveis. Configure-as no aplicativo, reinicie-o e execute Verificar agora novamente."
            SearchingPairs = "Procurando pares disponíveis..."; PairsFound = "{0} pares encontrados. O primeiro par é a recomendação inicial."; NoPairs = "Nenhum par livre foi encontrado no intervalo escolhido."; SearchError = "Erro ao procurar portas: {0}"
            Recommended = "RECOMENDADO — livre em TCP e UDP neste momento"; FreeNow = "Livre em TCP e UDP neste momento"; PairLoaded = "Par carregado. Aplique os mesmos números no aplicativo, reinicie-o e execute os testes novamente."
            Analyzing = "Analisando as portas, conexões e processos locais..."; TestingService = "Executando teste ativo avançado nos dois canais..."; TestingPort = "Testando ativamente a porta {0} — tempo limite de 6 segundos..."; ServiceBoth = "Os canais responderam ao teste auxiliar, mas a resposta não é uma confirmação funcional. Confirme o resultado no terceiro indicador."
            ServiceFailureOne = "O teste auxiliar foi inconclusivo ou não respondeu em pelo menos um canal."; AvailableResult = "Portas livres foram identificadas e não receberam teste de resposta. Aplique a configuração antes de validar o serviço."; VerificationError = "Erro na verificação: {0}"; FunctionalConfirmed = "Operação funcional confirmada. As informações foram registradas no histórico da sessão."
            FunctionalFailureRecorded = "Falha funcional registrada. A infraestrutura pode responder sem que a operação real esteja funcionando."; PairCopied = "Par {0} / {1} copiado para a área de transferência."; CopyError = "Não foi possível copiar o par: {0}"
            ExportDialog = "Exportar relatório de diagnóstico"; TextFile = "Arquivo de texto (*.txt)|*.txt"; ReportSuccess = "Relatório exportado com sucesso."; ReportError = "Não foi possível exportar o relatório: {0}"
            MonitoringStopped = "Monitoramento interrompido."; MonitoringActive = "Monitoramento ativo. Execute operações no aplicativo para acompanhar as conexões."; HistoryCleared = "Histórico da sessão limpo."
            ReportHeading = "DIAGNÓSTICO DE CANAIS LOCAIS"; GeneratedAt = "Gerado em"; Summary = "RESUMO"; Transport = "Transporte"; LocalService = "Serviço local"; FunctionalTest = "Teste funcional"; AnalyzedPorts = "PORTAS ANALISADAS"; Details = "DETALHES"; SuggestedPairsHeading = "PARES SUGERIDOS"; SessionHistory = "HISTÓRICO DA SESSÃO"
            VerificationCanceled = "Verificação cancelada pelo usuário."; CanceledState = "CANCELADO"; NotExecuted = "NÃO EXECUTADO"; AvailabilityOnly = "Modo de disponibilidade: nenhum teste ativo foi enviado"; TcpAccepted = "TCP ACEITO"; BothTcpAccepted = "As duas portas aceitaram conexão TCP local"; TestingTcpPort = "Testando conexão TCP na porta {0}..."; TcpFailure = "Uma ou mais portas não aceitaram conexão TCP."; TcpBothAction = "As duas portas aceitaram conexão TCP. O transporte está acessível, mas o protocolo do serviço não foi validado."
            RevalidatingPair = "Revalidando o par selecionado..."; PairNoLongerFree = "O par não está mais livre. Execute uma nova pesquisa."; PairRevalidatedLoaded = "Par revalidado e carregado. Aplique os números no aplicativo e reinicie-o."
            ResourcesHint = "Verifica exclusões do Windows, regras específicas de firewall, redirecionamentos e privilégios."; Administrator = "Privilégio administrativo"; AdminYes = "Sim"; AdminNo = "Não — algumas informações podem ficar limitadas"; Owner = "Processo proprietário"; TcpReserved = "TCP excluída"; UdpReserved = "UDP excluída"; Firewall = "Firewall"; PortProxy = "Redirecionamento"; Yes = "Sim"; No = "Não"; None = "Nenhum"; NotAvailable = "Indisponível"; WindowsScanComplete = "Recursos do Windows verificados para as portas atuais."
            ComparisonHint = "Capture o estado antes de reiniciar o aplicativo e compare depois da reinicialização."; CaptureBefore = "Capturar estado anterior"; CompareAfter = "Comparar estado atual"; NoBefore = "Capture o estado anterior antes de comparar."; BeforeCaptured = "Estado anterior capturado às {0}. Reinicie o aplicativo e clique em Comparar estado atual."; ComparisonComplete = "Comparação concluída. As alterações estão destacadas na tabela."; Before = "Antes"; After = "Depois"; Change = "Mudança"; Changed = "ALTERADO"; Unchanged = "Sem alteração"; WindowsResourcesHeading = "RECURSOS DO WINDOWS"; ComparisonHeading = "COMPARAÇÃO ANTES E DEPOIS"
            LegendSuccess = "Normal"; LegendInfo = "Informação"; LegendWarning = "Atenção"; LegendError = "Falha"
        }
        "en-US" = @{
            AppTitle = "Local Channel Diagnostics"; Subtitle = "Layered verification: transport, observed communication, and functional operation"; Language = "Language"
            TransportTitle = "1. TRANSPORT LAYER"; ServiceTitle = "2. COMMUNICATION TEST"; FunctionalTitle = "3. FUNCTIONAL TEST"
            Worked = "Worked"; Failed = "Failed"; Overview = "Overview"; Technical = "Technical details"; WindowsResources = "Windows resources"; Comparison = "Before and after"
            PortsGroup = "Ports configured in the application"; Port1 = "Port 1"; Port2 = "Port 2"; VerifyNow = "Verify now"
            StartMonitoring = "Start monitoring"; StopMonitoring = "Stop monitoring"; Cancel = "Cancel"; Canceling = "Canceling..."; PortsHint = "Use the same values configured in the application being analyzed."; DiagnosticMode = "Mode"; ModeFull = "Safe (recommended)"; ModeAvailability = "Ports only"; ModeTcp = "TCP connection"; ModeService = "Active test (advanced)"
            SuggestGroup = "Find a new port pair"; Start = "Start"; Difference = "Difference"; Suggestions = "Suggestions"
            FindPairs = "Find free pairs"; UsePair = "Use selected pair"; SuggestHint = "The tool finds and validates; apply the change in the application."
            PortStatus = "Port status"; SuggestedPairs = "Suggested pairs"; Port = "Port"; TCP = "TCP"; UDP = "UDP"; Processes = "Processes"; Diagnosis = "Diagnosis"; State = "Status"
            CopyPair = "Copy pair"; ExportReport = "Export report"; NextAction = "Plain-language conclusion and next action"; Ready = "Ready to diagnose."
            History = "Session history"; ClearHistory = "Clear session history"; NotVerified = "NOT VERIFIED"; WaitingDiagnosis = "Waiting for diagnosis"
            TransportFailure = "FAILURE"; TransportFailureDetail = "Conflict or unresponsive process"; Connected = "CONNECTED"; TwoTcpChannels = "Two TCP channels established"
            NoTarget = "No target process is running"; Warning = "WARNING"; NotAllChannels = "Not all channels are established"; Available = "AVAILABLE"; BothPortsFree = "Both ports are free and awaiting configuration"; OnePortFree = "One port is free and awaiting configuration"
            Responding = "RESPONDING"; ResponseLatency = "Test completed; highest latency: {0} ms"; IncompleteResponse = "Incomplete response on one or more channels"; UseVerify = "Use the Verify now button"; AwaitingConfiguration = "AWAITING CONFIGURATION"; FreePortsNeedConfig = "Free ports; apply the configuration before testing the service"; Partial = "PARTIAL"; OnePortAwaiting = "One channel responded and the other is awaiting configuration"
            ObservedOperating = "OPERATING BY OBSERVATION"; RealChannelsObserved = "Both real channels are connected; no additional test client was opened"; InconclusiveState = "INCONCLUSIVE"; ActiveTestInconclusive = "The auxiliary test did not prove a failure in the real channel"; PassiveSafeAction = "The real channels are connected. Confirm operation in the functional test; the active test was skipped."; ActiveAdvancedAction = "Advanced active test completed. Inconclusive results do not replace the real connection or the functional test."
            Working = "WORKING"; ConfirmedByUser = "Confirmed by the user in this session"; FunctionalFailure = "FAILED"; RealOperationFailure = "Failure confirmed during a real operation"; ExecuteRealOperation = "Perform a real operation and report the result"
            FailureOrConflict = "Failure or conflict detected. Review the diagnosis and processes shown in the table."; MultipleServers = "More than one server process is open. An old instance may be using the ports."
            ServiceRespondedAction = "The service responded. Perform a real operation in the application and record the result in the third indicator."; InfrastructureFunctionalFailure = "The infrastructure responded, but the functional test failed. Export the report and investigate the application layer."
            ThreeValidated = "All three stages were validated in this session. Keep monitoring if the failure is intermittent."; TcpConnectedAction = "TCP is connected, but the operation has not been confirmed. Use Verify now."
            OpenApplication = "Open the application that uses the channels and run the diagnosis again."; DiagnosisComplete = "Diagnosis complete. Review the individual states shown in the table."; DiagnosisError = "Diagnosis error: {0}"; FreePortAction = "One or more ports are available. Configure them in the application, restart it, and run Verify now again."
            SearchingPairs = "Searching for available pairs..."; PairsFound = "{0} pairs found. The first pair is the initial recommendation."; NoPairs = "No free pair was found in the selected range."; SearchError = "Port search error: {0}"
            Recommended = "RECOMMENDED — free for TCP and UDP at this time"; FreeNow = "Free for TCP and UDP at this time"; PairLoaded = "Pair loaded. Apply the same numbers in the application, restart it, and run the tests again."
            Analyzing = "Analyzing local ports, connections, and processes..."; TestingService = "Running the advanced active test on both channels..."; TestingPort = "Actively testing port {0} — 6-second timeout..."; ServiceBoth = "The channels answered the auxiliary test, but that response is not functional confirmation. Confirm the result in the third indicator."
            ServiceFailureOne = "The auxiliary test was inconclusive or did not respond on at least one channel."; AvailableResult = "Free ports were identified and did not receive a response test. Apply the configuration before validating the service."; VerificationError = "Verification error: {0}"; FunctionalConfirmed = "Functional operation confirmed. The information was added to the session history."
            FunctionalFailureRecorded = "Functional failure recorded. The infrastructure may respond even when the real operation is not working."; PairCopied = "Pair {0} / {1} copied to the clipboard."; CopyError = "Could not copy the pair: {0}"
            ExportDialog = "Export diagnostic report"; TextFile = "Text file (*.txt)|*.txt"; ReportSuccess = "Report exported successfully."; ReportError = "Could not export the report: {0}"
            MonitoringStopped = "Monitoring stopped."; MonitoringActive = "Monitoring active. Perform operations in the application to track the connections."; HistoryCleared = "Session history cleared."
            ReportHeading = "LOCAL CHANNEL DIAGNOSTICS"; GeneratedAt = "Generated at"; Summary = "SUMMARY"; Transport = "Transport"; LocalService = "Local service"; FunctionalTest = "Functional test"; AnalyzedPorts = "PORTS ANALYZED"; Details = "DETAILS"; SuggestedPairsHeading = "SUGGESTED PAIRS"; SessionHistory = "SESSION HISTORY"
            VerificationCanceled = "Verification canceled by the user."; CanceledState = "CANCELED"; NotExecuted = "NOT RUN"; AvailabilityOnly = "Availability mode: no active test was sent"; TcpAccepted = "TCP ACCEPTED"; BothTcpAccepted = "Both ports accepted a local TCP connection"; TestingTcpPort = "Testing TCP connection on port {0}..."; TcpFailure = "One or more ports did not accept a TCP connection."; TcpBothAction = "Both ports accepted a TCP connection. Transport is reachable, but the service protocol was not validated."
            RevalidatingPair = "Revalidating the selected pair..."; PairNoLongerFree = "The pair is no longer free. Run a new search."; PairRevalidatedLoaded = "Pair revalidated and loaded. Apply the numbers in the application and restart it."
            ResourcesHint = "Checks Windows exclusions, specific firewall rules, port forwarding, and privileges."; Administrator = "Administrative privilege"; AdminYes = "Yes"; AdminNo = "No — some information may be limited"; Owner = "Owning process"; TcpReserved = "TCP excluded"; UdpReserved = "UDP excluded"; Firewall = "Firewall"; PortProxy = "Port forwarding"; Yes = "Yes"; No = "No"; None = "None"; NotAvailable = "Unavailable"; WindowsScanComplete = "Windows resources checked for the current ports."
            ComparisonHint = "Capture the state before restarting the application and compare it after the restart."; CaptureBefore = "Capture previous state"; CompareAfter = "Compare current state"; NoBefore = "Capture the previous state before comparing."; BeforeCaptured = "Previous state captured at {0}. Restart the application and click Compare current state."; ComparisonComplete = "Comparison complete. Changes are highlighted in the table."; Before = "Before"; After = "After"; Change = "Change"; Changed = "CHANGED"; Unchanged = "No change"; WindowsResourcesHeading = "WINDOWS RESOURCES"; ComparisonHeading = "BEFORE AND AFTER COMPARISON"
            LegendSuccess = "Normal"; LegendInfo = "Information"; LegendWarning = "Warning"; LegendError = "Failure"
        }
    }

    $tr = {
        param([string]$Key)
        return [string]$translations[$uiState.Language][$Key]
    }.GetNewClosure()

    $form = [System.Windows.Forms.Form]::new()
    $form.Text = "Diagnóstico de Canais Locais"
    $form.StartPosition = "CenterScreen"
    $form.Size = [System.Drawing.Size]::new(1180, 840)
    $form.MinimumSize = [System.Drawing.Size]::new(1180, 760)
    $form.Font = [System.Drawing.Font]::new("Segoe UI", 9)
    $form.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $form.AutoScaleMode = "Dpi"

    $headerPanel = [System.Windows.Forms.Panel]::new()
    $headerPanel.Location = [System.Drawing.Point]::new(0, 0)
    $headerPanel.Size = [System.Drawing.Size]::new(1180, 76)
    $headerPanel.Anchor = "Top,Left,Right"
    $headerPanel.BackColor = [System.Drawing.Color]::FromArgb(26, 71, 118)
    $form.Controls.Add($headerPanel)

    $titleLabel = [System.Windows.Forms.Label]::new()
    $titleLabel.Text = "Diagnóstico de Canais Locais"
    $titleLabel.ForeColor = [System.Drawing.Color]::White
    $titleLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 18)
    $titleLabel.AutoSize = $true
    $titleLabel.Location = [System.Drawing.Point]::new(20, 10)
    $headerPanel.Controls.Add($titleLabel)

    $subtitleLabel = [System.Windows.Forms.Label]::new()
    $subtitleLabel.Text = "Verificação em camadas: transporte, serviço local e operação funcional"
    $subtitleLabel.ForeColor = [System.Drawing.Color]::FromArgb(220, 232, 244)
    $subtitleLabel.AutoSize = $true
    $subtitleLabel.Location = [System.Drawing.Point]::new(23, 47)
    $headerPanel.Controls.Add($subtitleLabel)

    $languageLabel = [System.Windows.Forms.Label]::new()
    $languageLabel.ForeColor = [System.Drawing.Color]::White
    $languageLabel.AutoSize = $true
    $languageLabel.Location = [System.Drawing.Point]::new(925, 16)
    $languageLabel.Anchor = "Top,Right"
    $headerPanel.Controls.Add($languageLabel)

    $languageCombo = [System.Windows.Forms.ComboBox]::new()
    $languageCombo.DropDownStyle = "DropDownList"
    $languageCombo.Location = [System.Drawing.Point]::new(995, 13)
    $languageCombo.Size = [System.Drawing.Size]::new(145, 25)
    $languageCombo.Anchor = "Top,Right"
    [void]$languageCombo.Items.AddRange([object[]]@("Português", "English"))
    $languageCombo.SelectedIndex = if ($uiState.Language -eq "en-US") { 1 } else { 0 }
    $headerPanel.Controls.Add($languageCombo)

    $legendPanel = [System.Windows.Forms.Panel]::new()
    $legendPanel.Location = [System.Drawing.Point]::new(610, 43)
    $legendPanel.Size = [System.Drawing.Size]::new(530, 27)
    $legendPanel.Anchor = "Top,Right"
    $legendPanel.BackColor = $headerPanel.BackColor
    $headerPanel.Controls.Add($legendPanel)

    $legendLabels = @{}
    $legendItems = @(
        [PSCustomObject]@{ Key = "LegendSuccess"; Text = "Normal"; X = 0; Color = [System.Drawing.Color]::FromArgb(224, 245, 232) }
        [PSCustomObject]@{ Key = "LegendInfo"; Text = "Informação"; X = 118; Color = [System.Drawing.Color]::FromArgb(226, 239, 252) }
        [PSCustomObject]@{ Key = "LegendWarning"; Text = "Atenção"; X = 258; Color = [System.Drawing.Color]::FromArgb(255, 244, 204) }
        [PSCustomObject]@{ Key = "LegendError"; Text = "Falha"; X = 386; Color = [System.Drawing.Color]::FromArgb(255, 224, 224) }
    )
    foreach ($legendItem in $legendItems) {
        $swatch = [System.Windows.Forms.Panel]::new()
        $swatch.Location = [System.Drawing.Point]::new($legendItem.X, 5)
        $swatch.Size = [System.Drawing.Size]::new(14, 14)
        $swatch.BackColor = $legendItem.Color
        $swatch.BorderStyle = "FixedSingle"
        $legendPanel.Controls.Add($swatch)

        $legendLabel = [System.Windows.Forms.Label]::new()
        $legendLabel.Text = $legendItem.Text
        $legendLabel.ForeColor = [System.Drawing.Color]::White
        $legendLabel.AutoSize = $true
        $legendLabel.Location = [System.Drawing.Point]::new(($legendItem.X + 20), 4)
        $legendPanel.Controls.Add($legendLabel)
        $legendLabels[$legendItem.Key] = $legendLabel
    }

    $transportCard = New-StatusCard `
        -Title "1. CAMADA DE TRANSPORTE" `
        -Location ([System.Drawing.Point]::new(18, 88)) `
        -Size ([System.Drawing.Size]::new(360, 82))
    $form.Controls.Add($transportCard.Panel)

    $serviceCard = New-StatusCard `
        -Title "2. SERVIÇO LOCAL" `
        -Location ([System.Drawing.Point]::new(397, 88)) `
        -Size ([System.Drawing.Size]::new(360, 82))
    $form.Controls.Add($serviceCard.Panel)

    $functionalCard = New-StatusCard `
        -Title "3. TESTE FUNCIONAL" `
        -Location ([System.Drawing.Point]::new(776, 88)) `
        -Size ([System.Drawing.Size]::new(360, 82))
    $form.Controls.Add($functionalCard.Panel)

    $functionalOkButton = [System.Windows.Forms.Button]::new()
    $functionalOkButton.Text = "Funcionou"
    $functionalOkButton.Location = [System.Drawing.Point]::new(190, 27)
    $functionalOkButton.Size = [System.Drawing.Size]::new(75, 27)
    $functionalOkButton.FlatStyle = "Flat"
    $functionalOkButton.BackColor = [System.Drawing.Color]::FromArgb(224, 245, 232)
    $functionalCard.Panel.Controls.Add($functionalOkButton)

    $functionalFailButton = [System.Windows.Forms.Button]::new()
    $functionalFailButton.Text = "Falhou"
    $functionalFailButton.Location = [System.Drawing.Point]::new(272, 27)
    $functionalFailButton.Size = [System.Drawing.Size]::new(70, 27)
    $functionalFailButton.FlatStyle = "Flat"
    $functionalFailButton.BackColor = [System.Drawing.Color]::FromArgb(255, 224, 224)
    $functionalCard.Panel.Controls.Add($functionalFailButton)

    $tabs = [System.Windows.Forms.TabControl]::new()
    $tabs.Location = [System.Drawing.Point]::new(18, 184)
    $tabs.Size = [System.Drawing.Size]::new(1118, 600)
    $tabs.Anchor = "Top,Bottom,Left,Right"
    $form.Controls.Add($tabs)

    $overviewTab = [System.Windows.Forms.TabPage]::new()
    $overviewTab.Text = "Visão geral"
    $overviewTab.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $tabs.TabPages.Add($overviewTab)

    $technicalTab = [System.Windows.Forms.TabPage]::new()
    $technicalTab.Text = "Detalhes técnicos"
    $technicalTab.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $tabs.TabPages.Add($technicalTab)

    $windowsTab = [System.Windows.Forms.TabPage]::new()
    $windowsTab.Text = "Recursos do Windows"
    $windowsTab.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $tabs.TabPages.Add($windowsTab)

    $comparisonTab = [System.Windows.Forms.TabPage]::new()
    $comparisonTab.Text = "Antes e depois"
    $comparisonTab.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $tabs.TabPages.Add($comparisonTab)

    $portsGroup = [System.Windows.Forms.GroupBox]::new()
    $portsGroup.Text = "Portas configuradas no aplicativo"
    $portsGroup.Location = [System.Drawing.Point]::new(18, 18)
    $portsGroup.Size = [System.Drawing.Size]::new(520, 142)
    $portsGroup.Anchor = "Top,Left"
    $overviewTab.Controls.Add($portsGroup)

    $port1Label = [System.Windows.Forms.Label]::new()
    $port1Label.Text = "Porta 1"
    $port1Label.AutoSize = $true
    $port1Label.Location = [System.Drawing.Point]::new(18, 30)
    $portsGroup.Controls.Add($port1Label)

    $port1Input = [System.Windows.Forms.NumericUpDown]::new()
    $port1Input.Minimum = 1
    $port1Input.Maximum = 65535
    $port1Input.Value = $Portas[0]
    $port1Input.Location = [System.Drawing.Point]::new(75, 27)
    $port1Input.Width = 90
    $portsGroup.Controls.Add($port1Input)

    $port2Label = [System.Windows.Forms.Label]::new()
    $port2Label.Text = "Porta 2"
    $port2Label.AutoSize = $true
    $port2Label.Location = [System.Drawing.Point]::new(185, 30)
    $portsGroup.Controls.Add($port2Label)

    $port2Input = [System.Windows.Forms.NumericUpDown]::new()
    $port2Input.Minimum = 1
    $port2Input.Maximum = 65535
    $port2Input.Value = $Portas[1]
    $port2Input.Location = [System.Drawing.Point]::new(242, 27)
    $port2Input.Width = 90
    $portsGroup.Controls.Add($port2Input)

    $modeLabel = [System.Windows.Forms.Label]::new()
    $modeLabel.Text = "Modo"
    $modeLabel.AutoSize = $true
    $modeLabel.Location = [System.Drawing.Point]::new(350, 20)
    $portsGroup.Controls.Add($modeLabel)

    $modeCombo = [System.Windows.Forms.ComboBox]::new()
    $modeCombo.DropDownStyle = "DropDownList"
    $modeCombo.Location = [System.Drawing.Point]::new(350, 38)
    $modeCombo.Size = [System.Drawing.Size]::new(137, 25)
    $modeCombo.Items.AddRange([object[]]@("Completo", "Somente portas", "Conexão TCP", "Somente serviço"))
    $modeCombo.SelectedIndex = switch ($uiState.Mode) { "Availability" { 1 } "Tcp" { 2 } "Service" { 3 } default { 0 } }
    $portsGroup.Controls.Add($modeCombo)

    $diagnoseButton = [System.Windows.Forms.Button]::new()
    $diagnoseButton.Text = "Verificar agora"
    $diagnoseButton.Location = [System.Drawing.Point]::new(18, 70)
    $diagnoseButton.Size = [System.Drawing.Size]::new(155, 34)
    $diagnoseButton.BackColor = [System.Drawing.Color]::FromArgb(26, 115, 181)
    $diagnoseButton.ForeColor = [System.Drawing.Color]::White
    $diagnoseButton.FlatStyle = "Flat"
    $portsGroup.Controls.Add($diagnoseButton)

    $monitorButton = [System.Windows.Forms.Button]::new()
    $monitorButton.Text = "Iniciar monitoramento"
    $monitorButton.Location = [System.Drawing.Point]::new(182, 70)
    $monitorButton.Size = [System.Drawing.Size]::new(190, 34)
    $monitorButton.FlatStyle = "Flat"
    $portsGroup.Controls.Add($monitorButton)

    $cancelButton = [System.Windows.Forms.Button]::new()
    $cancelButton.Text = "Cancelar"
    $cancelButton.Location = [System.Drawing.Point]::new(381, 70)
    $cancelButton.Size = [System.Drawing.Size]::new(106, 34)
    $cancelButton.Enabled = $false
    $cancelButton.FlatStyle = "Flat"
    $cancelButton.BackColor = [System.Drawing.Color]::FromArgb(255, 238, 238)
    $cancelButton.ForeColor = [System.Drawing.Color]::FromArgb(155, 35, 35)
    $portsGroup.Controls.Add($cancelButton)

    $portsHintLabel = [System.Windows.Forms.Label]::new()
    $portsHintLabel.Text = "Use os mesmos valores configurados no aplicativo analisado."
    $portsHintLabel.AutoSize = $true
    $portsHintLabel.ForeColor = [System.Drawing.Color]::DimGray
    $portsHintLabel.Location = [System.Drawing.Point]::new(18, 113)
    $portsGroup.Controls.Add($portsHintLabel)

    $suggestGroup = [System.Windows.Forms.GroupBox]::new()
    $suggestGroup.Text = "Localizar novo par de portas"
    $suggestGroup.Location = [System.Drawing.Point]::new(557, 18)
    $suggestGroup.Size = [System.Drawing.Size]::new(545, 142)
    $suggestGroup.Anchor = "Top,Left,Right"
    $overviewTab.Controls.Add($suggestGroup)

    $startLabel = [System.Windows.Forms.Label]::new()
    $startLabel.Text = "Início"
    $startLabel.AutoSize = $true
    $startLabel.Location = [System.Drawing.Point]::new(18, 30)
    $suggestGroup.Controls.Add($startLabel)

    $startInput = [System.Windows.Forms.NumericUpDown]::new()
    $startInput.Minimum = 1024
    $startInput.Maximum = 49151
    $startInput.Value = $Inicio
    $startInput.Location = [System.Drawing.Point]::new(62, 27)
    $startInput.Width = 88
    $suggestGroup.Controls.Add($startInput)

    $offsetLabel = [System.Windows.Forms.Label]::new()
    $offsetLabel.Text = "Diferença"
    $offsetLabel.AutoSize = $true
    $offsetLabel.Location = [System.Drawing.Point]::new(168, 30)
    $suggestGroup.Controls.Add($offsetLabel)

    $offsetInput = [System.Windows.Forms.NumericUpDown]::new()
    $offsetInput.Minimum = 1
    $offsetInput.Maximum = 30000
    $offsetInput.Value = $Diferenca
    $offsetInput.Location = [System.Drawing.Point]::new(230, 27)
    $offsetInput.Width = 88
    $suggestGroup.Controls.Add($offsetInput)

    $countLabel = [System.Windows.Forms.Label]::new()
    $countLabel.Text = "Sugestões"
    $countLabel.AutoSize = $true
    $countLabel.Location = [System.Drawing.Point]::new(338, 30)
    $suggestGroup.Controls.Add($countLabel)

    $countInput = [System.Windows.Forms.NumericUpDown]::new()
    $countInput.Minimum = 1
    $countInput.Maximum = 30
    $countInput.Value = $Quantidade
    $countInput.Location = [System.Drawing.Point]::new(405, 27)
    $countInput.Width = 55
    $suggestGroup.Controls.Add($countInput)

    if ($null -ne $storedSettings) {
        try {
            if ([int]$storedSettings.Port1 -ge 1 -and [int]$storedSettings.Port1 -le 65535) {
                $port1Input.Value = [int]$storedSettings.Port1
            }
            if ([int]$storedSettings.Port2 -ge 1 -and [int]$storedSettings.Port2 -le 65535) {
                $port2Input.Value = [int]$storedSettings.Port2
            }
            if ([int]$storedSettings.StartPort -ge 1024 -and [int]$storedSettings.StartPort -le 49151) {
                $startInput.Value = [int]$storedSettings.StartPort
            }
            if ([int]$storedSettings.Offset -ge 1 -and [int]$storedSettings.Offset -le 30000) {
                $offsetInput.Value = [int]$storedSettings.Offset
            }
        }
        catch {
        }
    }

    $suggestButton = [System.Windows.Forms.Button]::new()
    $suggestButton.Text = "Procurar pares livres"
    $suggestButton.Location = [System.Drawing.Point]::new(18, 70)
    $suggestButton.Size = [System.Drawing.Size]::new(155, 34)
    $suggestButton.BackColor = [System.Drawing.Color]::FromArgb(42, 135, 84)
    $suggestButton.ForeColor = [System.Drawing.Color]::White
    $suggestButton.FlatStyle = "Flat"
    $suggestGroup.Controls.Add($suggestButton)

    $usePairButton = [System.Windows.Forms.Button]::new()
    $usePairButton.Text = "Carregar par selecionado"
    $usePairButton.Location = [System.Drawing.Point]::new(184, 70)
    $usePairButton.Size = [System.Drawing.Size]::new(180, 34)
    $usePairButton.Enabled = $false
    $usePairButton.FlatStyle = "Flat"
    $suggestGroup.Controls.Add($usePairButton)

    $suggestHintLabel = [System.Windows.Forms.Label]::new()
    $suggestHintLabel.Text = "A ferramenta encontra e valida; a alteração deve ser aplicada no aplicativo."
    $suggestHintLabel.AutoSize = $true
    $suggestHintLabel.ForeColor = [System.Drawing.Color]::DimGray
    $suggestHintLabel.Location = [System.Drawing.Point]::new(18, 113)
    $suggestGroup.Controls.Add($suggestHintLabel)

    $diagnosisLabel = [System.Windows.Forms.Label]::new()
    $diagnosisLabel.Text = "Estado das portas"
    $diagnosisLabel.AutoSize = $true
    $diagnosisLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 11)
    $diagnosisLabel.Location = [System.Drawing.Point]::new(18, 18)
    $technicalTab.Controls.Add($diagnosisLabel)

    $diagnosisGrid = New-DataGridView `
        -Columns @("Porta", "TCP", "UDP", "Processos", "Diagnóstico") `
        -Location ([System.Drawing.Point]::new(18, 45)) `
        -Size ([System.Drawing.Size]::new(1064, 205))
    $diagnosisGrid.Anchor = "Top,Left,Right"
    $diagnosisGrid.Columns[0].FillWeight = 40
    $diagnosisGrid.Columns[1].FillWeight = 70
    $diagnosisGrid.Columns[2].FillWeight = 55
    $diagnosisGrid.Columns[3].FillWeight = 180
    $diagnosisGrid.Columns[4].FillWeight = 190
    $technicalTab.Controls.Add($diagnosisGrid)

    $suggestionsLabel = [System.Windows.Forms.Label]::new()
    $suggestionsLabel.Text = "Pares sugeridos"
    $suggestionsLabel.AutoSize = $true
    $suggestionsLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 11)
    $suggestionsLabel.Location = [System.Drawing.Point]::new(18, 270)
    $technicalTab.Controls.Add($suggestionsLabel)

    $suggestionsGrid = New-DataGridView `
        -Columns @("Porta 1", "Porta 2", "Diferenca", "Estado") `
        -Location ([System.Drawing.Point]::new(18, 297)) `
        -Size ([System.Drawing.Size]::new(1064, 205))
    $suggestionsGrid.Anchor = "Top,Bottom,Left,Right"
    $suggestionsGrid.Columns[0].FillWeight = 55
    $suggestionsGrid.Columns[1].FillWeight = 55
    $suggestionsGrid.Columns[2].FillWeight = 55
    $suggestionsGrid.Columns[3].FillWeight = 210
    $technicalTab.Controls.Add($suggestionsGrid)

    $suggestGroup.Controls.Remove($usePairButton)
    $usePairButton.Text = "Usar par selecionado"
    $usePairButton.Location = [System.Drawing.Point]::new(18, 518)
    $technicalTab.Controls.Add($usePairButton)

    $copyPairButton = [System.Windows.Forms.Button]::new()
    $copyPairButton.Text = "Copiar par"
    $copyPairButton.Location = [System.Drawing.Point]::new(210, 518)
    $copyPairButton.Size = [System.Drawing.Size]::new(120, 34)
    $copyPairButton.Enabled = $false
    $copyPairButton.FlatStyle = "Flat"
    $technicalTab.Controls.Add($copyPairButton)

    $exportButton = [System.Windows.Forms.Button]::new()
    $exportButton.Text = "Exportar relatório"
    $exportButton.Location = [System.Drawing.Point]::new(342, 518)
    $exportButton.Size = [System.Drawing.Size]::new(140, 34)
    $exportButton.FlatStyle = "Flat"
    $technicalTab.Controls.Add($exportButton)

    $resourcesHintLabel = [System.Windows.Forms.Label]::new()
    $resourcesHintLabel.AutoSize = $true
    $resourcesHintLabel.Location = [System.Drawing.Point]::new(18, 18)
    $windowsTab.Controls.Add($resourcesHintLabel)

    $adminStatusLabel = [System.Windows.Forms.Label]::new()
    $adminStatusLabel.AutoSize = $true
    $adminStatusLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 9)
    $adminStatusLabel.Location = [System.Drawing.Point]::new(18, 48)
    $windowsTab.Controls.Add($adminStatusLabel)

    $windowsCheckedLabel = [System.Windows.Forms.Label]::new()
    $windowsCheckedLabel.AutoSize = $true
    $windowsCheckedLabel.ForeColor = [System.Drawing.Color]::DimGray
    $windowsCheckedLabel.Location = [System.Drawing.Point]::new(400, 48)
    $windowsTab.Controls.Add($windowsCheckedLabel)

    $resourcesGrid = New-DataGridView `
        -Columns @("Porta", "Processo proprietário", "TCP excluída", "UDP excluída", "Firewall", "Redirecionamento") `
        -Location ([System.Drawing.Point]::new(18, 78)) `
        -Size ([System.Drawing.Size]::new(1064, 470))
    $resourcesGrid.Columns[0].FillWeight = 40
    $resourcesGrid.Columns[1].FillWeight = 170
    $resourcesGrid.Columns[2].FillWeight = 60
    $resourcesGrid.Columns[3].FillWeight = 60
    $resourcesGrid.Columns[4].FillWeight = 190
    $resourcesGrid.Columns[5].FillWeight = 150
    $windowsTab.Controls.Add($resourcesGrid)

    $comparisonHintLabel = [System.Windows.Forms.Label]::new()
    $comparisonHintLabel.AutoSize = $true
    $comparisonHintLabel.Location = [System.Drawing.Point]::new(18, 18)
    $comparisonTab.Controls.Add($comparisonHintLabel)

    $captureBeforeButton = [System.Windows.Forms.Button]::new()
    $captureBeforeButton.Location = [System.Drawing.Point]::new(18, 48)
    $captureBeforeButton.Size = [System.Drawing.Size]::new(185, 34)
    $captureBeforeButton.FlatStyle = "Flat"
    $captureBeforeButton.BackColor = [System.Drawing.Color]::FromArgb(226, 239, 252)
    $comparisonTab.Controls.Add($captureBeforeButton)

    $compareAfterButton = [System.Windows.Forms.Button]::new()
    $compareAfterButton.Location = [System.Drawing.Point]::new(215, 48)
    $compareAfterButton.Size = [System.Drawing.Size]::new(185, 34)
    $compareAfterButton.FlatStyle = "Flat"
    $comparisonTab.Controls.Add($compareAfterButton)

    $comparisonStatusLabel = [System.Windows.Forms.Label]::new()
    $comparisonStatusLabel.AutoEllipsis = $true
    $comparisonStatusLabel.Location = [System.Drawing.Point]::new(420, 56)
    $comparisonStatusLabel.Size = [System.Drawing.Size]::new(655, 24)
    $comparisonStatusLabel.Anchor = "Top,Left,Right"
    $comparisonTab.Controls.Add($comparisonStatusLabel)

    $comparisonGrid = New-DataGridView `
        -Columns @("Porta", "Antes", "Depois", "Mudança") `
        -Location ([System.Drawing.Point]::new(18, 98)) `
        -Size ([System.Drawing.Size]::new(1064, 450))
    $comparisonGrid.Columns[0].FillWeight = 35
    $comparisonGrid.Columns[1].FillWeight = 180
    $comparisonGrid.Columns[2].FillWeight = 180
    $comparisonGrid.Columns[3].FillWeight = 65
    $comparisonTab.Controls.Add($comparisonGrid)

    $statusPanel = [System.Windows.Forms.Panel]::new()
    $statusPanel.Location = [System.Drawing.Point]::new(18, 180)
    $statusPanel.Size = [System.Drawing.Size]::new(1084, 88)
    $statusPanel.Anchor = "Top,Left,Right"
    $statusPanel.BackColor = [System.Drawing.Color]::White
    $statusPanel.BorderStyle = "FixedSingle"
    $overviewTab.Controls.Add($statusPanel)

    $statusTitleLabel = [System.Windows.Forms.Label]::new()
    $statusTitleLabel.Text = "Próxima ação recomendada"
    $statusTitleLabel.Font = [System.Drawing.Font]::new("Segoe UI Semibold", 9)
    $statusTitleLabel.AutoSize = $true
    $statusTitleLabel.Location = [System.Drawing.Point]::new(10, 8)
    $statusPanel.Controls.Add($statusTitleLabel)

    $statusLabel = [System.Windows.Forms.Label]::new()
    $statusLabel.Text = "Pronto para diagnosticar."
    $statusLabel.AutoEllipsis = $true
    $statusLabel.Location = [System.Drawing.Point]::new(10, 30)
    $statusLabel.Size = [System.Drawing.Size]::new(1055, 45)
    $statusLabel.Anchor = "Top,Left,Right"
    $statusPanel.Controls.Add($statusLabel)

    $historyGroup = [System.Windows.Forms.GroupBox]::new()
    $historyGroup.Text = "Histórico desta sessão"
    $historyGroup.Location = [System.Drawing.Point]::new(18, 286)
    $historyGroup.Size = [System.Drawing.Size]::new(1084, 240)
    $historyGroup.Anchor = "Top,Bottom,Left,Right"
    $overviewTab.Controls.Add($historyGroup)

    $historyList = [System.Windows.Forms.ListBox]::new()
    $historyList.Location = [System.Drawing.Point]::new(12, 60)
    $historyList.Size = [System.Drawing.Size]::new(1058, 162)
    $historyList.Anchor = "Top,Bottom,Left,Right"
    $historyList.HorizontalScrollbar = $true
    $historyGroup.Controls.Add($historyList)

    $clearHistoryButton = [System.Windows.Forms.Button]::new()
    $clearHistoryButton.Location = [System.Drawing.Point]::new(12, 22)
    $clearHistoryButton.Size = [System.Drawing.Size]::new(210, 30)
    $clearHistoryButton.Anchor = "Top,Left"
    $clearHistoryButton.FlatStyle = "Flat"
    $clearHistoryButton.BackColor = [System.Drawing.Color]::FromArgb(255, 238, 238)
    $clearHistoryButton.ForeColor = [System.Drawing.Color]::FromArgb(155, 35, 35)
    $historyGroup.Controls.Add($clearHistoryButton)
    $clearHistoryButton.BringToFront()

    $timer = [System.Windows.Forms.Timer]::new()
    $timer.Interval = [Math]::Max(1000, $IntervaloSegundos * 1000)
    $heartbeatResults = @{}

    $getSelectedMode = {
        switch ($modeCombo.SelectedIndex) {
            "1" { return "Availability" }
            "2" { return "Tcp" }
            "3" { return "Service" }
            default { return "Full" }
        }
    }.GetNewClosure()

    $renderWindowsResources = {
        $resourcesGrid.Rows.Clear()
        if ($uiState.WindowsResources.Count -eq 0) {
            $adminStatusLabel.Text = "$(& $tr 'Administrator'): —"
            return
        }

        $isAdmin = [bool]$uiState.WindowsResources[0].Administrador
        $adminStatusLabel.Text = "$(& $tr 'Administrator'): $(if ($isAdmin) { & $tr 'AdminYes' } else { & $tr 'AdminNo' })"
        foreach ($resource in $uiState.WindowsResources) {
            $firewallText = if ($resource.RegrasFirewall.Count -gt 0) {
                ($resource.RegrasFirewall -join "; ")
            }
            elseif (-not [string]::IsNullOrWhiteSpace($resource.FirewallErro)) {
                & $tr "NotAvailable"
            }
            else {
                & $tr "None"
            }
            $proxyText = if ($resource.PortProxy.Count -gt 0) { ($resource.PortProxy -join "; ") } else { & $tr "None" }
            $null = $resourcesGrid.Rows.Add([object[]]@(
                $resource.Porta,
                ((Convert-ProcessDisplayText -Text $resource.Processo -Language $uiState.Language) -replace ' \| ', [Environment]::NewLine),
                $(if ($resource.TcpExcluida) { & $tr "Yes" } else { & $tr "No" }),
                $(if ($resource.UdpExcluida) { & $tr "Yes" } else { & $tr "No" }),
                $firewallText,
                $proxyText
            ))
        }
    }.GetNewClosure()

    $refreshWindowsResources = {
        $context = Get-WindowsResourceContext
        $resources = @()
        foreach ($port in @([int]$port1Input.Value, [int]$port2Input.Value)) {
            $snapshotItem = @($uiState.LastSnapshot | Where-Object { [int]$_.Porta -eq $port } | Select-Object -First 1)
            $processSummary = if ($snapshotItem.Count -gt 0) { [string]$snapshotItem[0].Processos } else { "" }
            $resources += Get-WindowsPortResourceInfo -Port $port -Context $context -ProcessSummary $processSummary
        }
        $uiState.WindowsResources = @($resources)
        & $renderWindowsResources
        $windowsCheckedLabel.Text = "$(Get-Date -Format 'HH:mm:ss') — $(& $tr 'WindowsScanComplete')"
    }.GetNewClosure()

    $formatSnapshotBrief = {
        param([object]$Item)
        if ($null -eq $Item) {
            return "—"
        }
        $tcpText = Convert-PortStateText -Text ([string]$Item.TCP) -Language $uiState.Language
        $udpText = Convert-PortStateText -Text ([string]$Item.UDP) -Language $uiState.Language
        $diagnosisText = Convert-DiagnosisText -Text ([string]$Item.Diagnostico) -Language $uiState.Language
        return "TCP: $tcpText | UDP: $udpText | $diagnosisText"
    }.GetNewClosure()

    $renderComparison = {
        $comparisonGrid.Rows.Clear()
        foreach ($comparison in @($uiState.ComparisonRows)) {
            $changeText = if ($comparison.Changed) { & $tr "Changed" } else { & $tr "Unchanged" }
            $rowIndex = $comparisonGrid.Rows.Add([object[]]@(
                $comparison.Porta,
                (& $formatSnapshotBrief $comparison.Before),
                (& $formatSnapshotBrief $comparison.After),
                $changeText
            ))
            if ($comparison.Changed) {
                $comparisonGrid.Rows[$rowIndex].DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 244, 204)
            }
            else {
                $comparisonGrid.Rows[$rowIndex].DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(224, 245, 232)
            }
        }
    }.GetNewClosure()

    $applyLanguage = {
        $form.Text = & $tr "AppTitle"
        $titleLabel.Text = & $tr "AppTitle"
        $subtitleLabel.Text = & $tr "Subtitle"
        $languageLabel.Text = & $tr "Language"
        foreach ($legendKey in @("LegendSuccess", "LegendInfo", "LegendWarning", "LegendError")) {
            $legendLabels[$legendKey].Text = & $tr $legendKey
        }
        $transportCard.Title.Text = & $tr "TransportTitle"
        $serviceCard.Title.Text = & $tr "ServiceTitle"
        $functionalCard.Title.Text = & $tr "FunctionalTitle"
        $functionalOkButton.Text = & $tr "Worked"
        $functionalFailButton.Text = & $tr "Failed"
        $overviewTab.Text = & $tr "Overview"
        $technicalTab.Text = & $tr "Technical"
        $windowsTab.Text = & $tr "WindowsResources"
        $comparisonTab.Text = & $tr "Comparison"
        $portsGroup.Text = & $tr "PortsGroup"
        $port1Label.Text = & $tr "Port1"
        $port2Label.Text = & $tr "Port2"
        $modeLabel.Text = & $tr "DiagnosticMode"
        $uiState.ApplyingLanguage = $true
        $modeIndex = $modeCombo.SelectedIndex
        $modeCombo.BeginUpdate()
        $modeCombo.Items.Clear()
        [void]$modeCombo.Items.AddRange([object[]]@(
            (& $tr "ModeFull"),
            (& $tr "ModeAvailability"),
            (& $tr "ModeTcp"),
            (& $tr "ModeService")
        ))
        $modeCombo.SelectedIndex = if ($modeIndex -ge 0) { $modeIndex } else { 0 }
        $modeCombo.EndUpdate()
        $uiState.ApplyingLanguage = $false
        $diagnoseButton.Text = & $tr "VerifyNow"
        $monitorButton.Text = if ($timer.Enabled) { & $tr "StopMonitoring" } else { & $tr "StartMonitoring" }
        $cancelButton.Text = if ($uiState.CancelRequested) { & $tr "Canceling" } else { & $tr "Cancel" }
        $portsHintLabel.Text = & $tr "PortsHint"
        $suggestGroup.Text = & $tr "SuggestGroup"
        $startLabel.Text = & $tr "Start"
        $offsetLabel.Text = & $tr "Difference"
        $countLabel.Text = & $tr "Suggestions"
        $suggestButton.Text = & $tr "FindPairs"
        $usePairButton.Text = & $tr "UsePair"
        $suggestHintLabel.Text = & $tr "SuggestHint"
        $diagnosisLabel.Text = & $tr "PortStatus"
        $suggestionsLabel.Text = & $tr "SuggestedPairs"
        $copyPairButton.Text = & $tr "CopyPair"
        $exportButton.Text = & $tr "ExportReport"
        $statusTitleLabel.Text = & $tr "NextAction"
        $historyGroup.Text = & $tr "History"
        $clearHistoryButton.Text = & $tr "ClearHistory"
        $resourcesHintLabel.Text = & $tr "ResourcesHint"
        $captureBeforeButton.Text = & $tr "CaptureBefore"
        $compareAfterButton.Text = & $tr "CompareAfter"
        $comparisonHintLabel.Text = & $tr "ComparisonHint"

        $diagnosisGrid.Columns[0].HeaderText = & $tr "Port"
        $diagnosisGrid.Columns[1].HeaderText = & $tr "TCP"
        $diagnosisGrid.Columns[2].HeaderText = & $tr "UDP"
        $diagnosisGrid.Columns[3].HeaderText = & $tr "Processes"
        $diagnosisGrid.Columns[4].HeaderText = & $tr "Diagnosis"
        $suggestionsGrid.Columns[0].HeaderText = & $tr "Port1"
        $suggestionsGrid.Columns[1].HeaderText = & $tr "Port2"
        $suggestionsGrid.Columns[2].HeaderText = & $tr "Difference"
        $suggestionsGrid.Columns[3].HeaderText = & $tr "State"
        $resourcesGrid.Columns[0].HeaderText = & $tr "Port"
        $resourcesGrid.Columns[1].HeaderText = & $tr "Owner"
        $resourcesGrid.Columns[2].HeaderText = & $tr "TcpReserved"
        $resourcesGrid.Columns[3].HeaderText = & $tr "UdpReserved"
        $resourcesGrid.Columns[4].HeaderText = & $tr "Firewall"
        $resourcesGrid.Columns[5].HeaderText = & $tr "PortProxy"
        $comparisonGrid.Columns[0].HeaderText = & $tr "Port"
        $comparisonGrid.Columns[1].HeaderText = & $tr "Before"
        $comparisonGrid.Columns[2].HeaderText = & $tr "After"
        $comparisonGrid.Columns[3].HeaderText = & $tr "Change"
        for ($rowIndex = 0; $rowIndex -lt $suggestionsGrid.Rows.Count; $rowIndex++) {
            $suggestionsGrid.Rows[$rowIndex].Cells[3].Value = if ($rowIndex -eq 0) { & $tr "Recommended" } else { & $tr "FreeNow" }
        }
        & $renderWindowsResources
        & $renderComparison
        if ($uiState.ComparisonRows.Count -gt 0) {
            $comparisonStatusLabel.Text = & $tr "ComparisonComplete"
        }
        elseif ($null -ne $uiState.BeforeCapturedAt) {
            $comparisonStatusLabel.Text = (& $tr "BeforeCaptured") -f $uiState.BeforeCapturedAt.ToString("HH:mm:ss")
        }
    }.GetNewClosure()

    $refreshDiagnostics = {
        try {
            $form.UseWaitCursor = $true
            $currentPorts = @([int]$port1Input.Value, [int]$port2Input.Value)
            $snapshot = @(
                foreach ($port in $currentPorts) {
                    Get-PortSnapshot -Port $port -ExpectedProcess $ProcessoEsperado
                }
            )

            foreach ($item in $snapshot) {
                $item | Add-Member -NotePropertyName TransportDiagnosis -NotePropertyValue $item.Diagnostico -Force
                if ($heartbeatResults.ContainsKey([int]$item.Porta)) {
                    $heartbeat = $heartbeatResults[[int]$item.Porta]
                    if (((Get-Date) - $heartbeat.Momento).TotalMinutes -le 5) {
                        $item.Diagnostico = $heartbeat.Diagnostico
                    }
                }
            }

            $diagnosisGrid.Rows.Clear()
            foreach ($item in $snapshot) {
                $localizedDiagnosis = Convert-DiagnosisText -Text $item.Diagnostico -Language $uiState.Language
                $rowIndex = $diagnosisGrid.Rows.Add([object[]]@(
                    $item.Porta,
                    (Convert-PortStateText -Text $item.TCP -Language $uiState.Language),
                    (Convert-PortStateText -Text $item.UDP -Language $uiState.Language),
                    ((Convert-ProcessDisplayText -Text $item.Processos -Language $uiState.Language) -replace ' \| ', [Environment]::NewLine),
                    $localizedDiagnosis
                ))
                $row = $diagnosisGrid.Rows[$rowIndex]

                if ($item.Diagnostico -like "CAMADA APP:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(224, 245, 232)
                }
                elseif ($item.Diagnostico -like "CANAL REAL:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(224, 245, 232)
                }
                elseif ($item.Diagnostico -like "TCP GENERICO:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(224, 245, 232)
                }
                elseif ($item.Diagnostico -like "CONFLITO:*" -or $item.Diagnostico -like "FALHA*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 224, 224)
                }
                elseif ($item.Diagnostico -like "CANCELADO:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 244, 204)
                }
                elseif ($item.Diagnostico -like "INCONCLUSIVO:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 244, 204)
                }
                elseif ($item.Diagnostico -like "ATENCAO:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 244, 204)
                }
                elseif ($item.Diagnostico -like "SOCKET:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(226, 239, 252)
                }
                elseif ($item.Diagnostico -like "LIVRE:*" -or $item.Diagnostico -like "DISPONIVEL:*") {
                    $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(226, 239, 252)
                }
            }

            $targetProcesses = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "*$ProcessoEsperado*" })
            $serverProcesses = @($targetProcesses | Where-Object { $_.ProcessName -like "*AppLuncher*" })
            $applicationResponses = @($snapshot | Where-Object { $_.Diagnostico -like "CAMADA APP:*" })
            $tcpProbeResponses = @($snapshot | Where-Object { $_.Diagnostico -like "TCP GENERICO:*" })
            $canceledResults = @($snapshot | Where-Object { $_.Diagnostico -like "CANCELADO:*" })
            $socketConnections = @($snapshot | Where-Object { $_.TransportDiagnosis -like "SOCKET:*" -or $_.TransportDiagnosis -like "CANAL REAL:*" })
            $conflicts = @($snapshot | Where-Object { $_.TransportDiagnosis -like "CONFLITO:*" })
            $transportFailures = @($snapshot | Where-Object { $_.TransportDiagnosis -like "FALHA:*" })
            $serviceFailures = @($snapshot | Where-Object { $_.Diagnostico -like "FALHA*" })
            $availablePorts = @($snapshot | Where-Object { $_.TransportDiagnosis -like "LIVRE:*" -or $_.Diagnostico -like "DISPONIVEL:*" })
            $established = @($snapshot | Where-Object { $_.TCP -like "*Established*" })

            if ($conflicts.Count -gt 0 -or $transportFailures.Count -gt 0) {
                Set-StatusCardState $transportCard (& $tr "TransportFailure") (& $tr "TransportFailureDetail") "Error"
            }
            elseif ($established.Count -eq 2) {
                Set-StatusCardState $transportCard (& $tr "Connected") (& $tr "TwoTcpChannels") "Info"
            }
            elseif ($availablePorts.Count -eq 2) {
                Set-StatusCardState $transportCard (& $tr "Available") (& $tr "BothPortsFree") "Info"
            }
            elseif ($availablePorts.Count -gt 0) {
                Set-StatusCardState $transportCard (& $tr "Warning") (& $tr "OnePortFree") "Warning"
            }
            elseif ($targetProcesses.Count -eq 0) {
                Set-StatusCardState $transportCard (& $tr "NotVerified") (& $tr "NoTarget") "Neutral"
            }
            else {
                Set-StatusCardState $transportCard (& $tr "Warning") (& $tr "NotAllChannels") "Warning"
            }

            $currentHeartbeats = @(
                foreach ($port in $currentPorts) {
                    if ($heartbeatResults.ContainsKey($port)) {
                        $heartbeatResults[$port]
                    }
                }
            )
            $applicableHeartbeats = @($currentHeartbeats | Where-Object {
                -not ($_.PSObject.Properties.Name -contains "Aplicavel") -or $_.Aplicavel
            })
            $skippedHeartbeats = @($currentHeartbeats | Where-Object {
                $_.PSObject.Properties.Name -contains "Aplicavel" -and -not $_.Aplicavel
            })
            $successfulHeartbeats = @($applicableHeartbeats | Where-Object {
                ($_.PSObject.Properties.Name -contains "Resultado" -and $_.Resultado -in @("Success", "Observed")) -or
                (-not ($_.PSObject.Properties.Name -contains "Resultado") -and $_.Respondeu)
            })
            $observedHeartbeats = @($applicableHeartbeats | Where-Object {
                $_.PSObject.Properties.Name -contains "Resultado" -and $_.Resultado -eq "Observed"
            })
            $validatedHeartbeats = @($applicableHeartbeats | Where-Object {
                $_.PSObject.Properties.Name -contains "Resultado" -and $_.Resultado -eq "Success"
            })
            $inconclusiveHeartbeats = @($applicableHeartbeats | Where-Object {
                ($_.PSObject.Properties.Name -contains "Resultado" -and $_.Resultado -eq "Inconclusive") -or
                $_.Diagnostico -like "INCONCLUSIVO:*"
            })
            $canceledHeartbeats = @($currentHeartbeats | Where-Object {
                $_.PSObject.Properties.Name -contains "Cancelado" -and $_.Cancelado
            })
            $tcpHeartbeats = @($currentHeartbeats | Where-Object {
                $_.PSObject.Properties.Name -contains "Tipo" -and $_.Tipo -eq "Tcp"
            })
            $selectedMode = & $getSelectedMode
            if ($canceledHeartbeats.Count -gt 0) {
                Set-StatusCardState $serviceCard (& $tr "CanceledState") (& $tr "VerificationCanceled") "Warning"
            }
            elseif ($selectedMode -eq "Availability") {
                Set-StatusCardState $serviceCard (& $tr "NotExecuted") (& $tr "AvailabilityOnly") "Neutral"
            }
            elseif ($observedHeartbeats.Count -eq 2) {
                Set-StatusCardState $serviceCard (& $tr "ObservedOperating") (& $tr "RealChannelsObserved") "Success"
            }
            elseif ($tcpHeartbeats.Count -gt 0 -and $skippedHeartbeats.Count -eq 0 -and $validatedHeartbeats.Count -eq 2) {
                Set-StatusCardState $serviceCard (& $tr "TcpAccepted") (& $tr "BothTcpAccepted") "Success"
            }
            elseif ($tcpHeartbeats.Count -gt 0 -and $skippedHeartbeats.Count -eq 0) {
                Set-StatusCardState $serviceCard (& $tr "TransportFailure") (& $tr "TcpFailure") "Error"
            }
            elseif ($skippedHeartbeats.Count -gt 0 -and $applicableHeartbeats.Count -eq 0) {
                Set-StatusCardState $serviceCard (& $tr "AwaitingConfiguration") (& $tr "FreePortsNeedConfig") "Info"
            }
            elseif ($skippedHeartbeats.Count -gt 0 -and $successfulHeartbeats.Count -eq $applicableHeartbeats.Count) {
                Set-StatusCardState $serviceCard (& $tr "Partial") (& $tr "OnePortAwaiting") "Warning"
            }
            elseif ($inconclusiveHeartbeats.Count -gt 0) {
                Set-StatusCardState $serviceCard (& $tr "InconclusiveState") (& $tr "ActiveTestInconclusive") "Warning"
            }
            elseif ($skippedHeartbeats.Count -gt 0) {
                Set-StatusCardState $serviceCard (& $tr "TransportFailure") (& $tr "IncompleteResponse") "Error"
            }
            elseif ($validatedHeartbeats.Count -eq 2) {
                $maxLatency = ($validatedHeartbeats | Measure-Object -Property LatenciaMs -Maximum).Maximum
                Set-StatusCardState $serviceCard (& $tr "Responding") ((& $tr "ResponseLatency") -f $maxLatency) "Success"
            }
            elseif ($currentHeartbeats.Count -gt 0) {
                Set-StatusCardState $serviceCard (& $tr "TransportFailure") (& $tr "IncompleteResponse") "Error"
            }
            else {
                Set-StatusCardState $serviceCard (& $tr "NotVerified") (& $tr "UseVerify") "Neutral"
            }

            if ($uiState.Functional -eq "Funcionou") {
                Set-StatusCardState $functionalCard (& $tr "Working") (& $tr "ConfirmedByUser") "Success"
            }
            elseif ($uiState.Functional -eq "Falhou") {
                Set-StatusCardState $functionalCard (& $tr "FunctionalFailure") (& $tr "RealOperationFailure") "Error"
            }
            else {
                Set-StatusCardState $functionalCard (& $tr "NotVerified") (& $tr "ExecuteRealOperation") "Neutral"
            }

            if ($canceledResults.Count -gt 0) {
                $statusLabel.Text = & $tr "VerificationCanceled"
            }
            elseif ($conflicts.Count -gt 0 -or $transportFailures.Count -gt 0 -or $serviceFailures.Count -gt 0) {
                $statusLabel.Text = & $tr "FailureOrConflict"
            }
            elseif ($serverProcesses.Count -gt 1) {
                $statusLabel.Text = & $tr "MultipleServers"
            }
            elseif ($availablePorts.Count -gt 0) {
                $statusLabel.Text = & $tr "FreePortAction"
            }
            elseif ($tcpProbeResponses.Count -eq 2) {
                $statusLabel.Text = & $tr "TcpBothAction"
            }
            elseif ($observedHeartbeats.Count -eq 2) {
                $statusLabel.Text = & $tr "PassiveSafeAction"
            }
            elseif ($applicationResponses.Count -eq 2) {
                if ($uiState.Functional -eq "NaoVerificado") {
                    $statusLabel.Text = & $tr "ServiceRespondedAction"
                }
                elseif ($uiState.Functional -eq "Falhou") {
                    $statusLabel.Text = & $tr "InfrastructureFunctionalFailure"
                }
                else {
                    $statusLabel.Text = & $tr "ThreeValidated"
                }
            }
            elseif ($socketConnections.Count -eq 2) {
                $statusLabel.Text = if ($selectedMode -eq "Service") { & $tr "ActiveAdvancedAction" } else { & $tr "PassiveSafeAction" }
            }
            elseif ($targetProcesses.Count -eq 0) {
                $statusLabel.Text = & $tr "OpenApplication"
            }
            else {
                $statusLabel.Text = & $tr "DiagnosisComplete"
            }

            $uiState.LastSnapshot = $snapshot
            $historySignature = (Get-SnapshotSignature -Snapshot $snapshot) + "|" + $uiState.Functional
            if (-not $uiState.SuppressHistory -and
                ($uiState.ForceHistoryEntry -or $historySignature -ne $uiState.LastHistorySignature)) {
                $portWord = & $tr "Port"
                $summary = ($snapshot | ForEach-Object { "$portWord $($_.Porta): $(Convert-DiagnosisText -Text $_.Diagnostico -Language $uiState.Language)" }) -join " | "
                $historyList.Items.Insert(0, "$(Get-Date -Format 'HH:mm:ss') - $summary")
                while ($historyList.Items.Count -gt 100) {
                    $historyList.Items.RemoveAt($historyList.Items.Count - 1)
                }
                $uiState.LastHistorySignature = $historySignature
                $uiState.ForceHistoryEntry = $false
            }
        }
        catch {
            $statusLabel.Text = (& $tr "DiagnosisError") -f $_.Exception.Message
            [System.Windows.Forms.MessageBox]::Show(
                $_.Exception.Message,
                (& $tr "AppTitle"),
                "OK",
                "Error"
            ) | Out-Null
        }
        finally {
            $form.UseWaitCursor = $false
        }
    }.GetNewClosure()

    $findSuggestions = {
        try {
            $form.UseWaitCursor = $true
            $suggestButton.Enabled = $false
            $statusLabel.Text = & $tr "SearchingPairs"
            [System.Windows.Forms.Application]::DoEvents()

            $pairs = @(Find-PortPairs `
                -StartPort ([int]$startInput.Value) `
                -EndPort 48000 `
                -Offset ([int]$offsetInput.Value) `
                -Count ([int]$countInput.Value))

            $suggestionsGrid.Rows.Clear()
            if ($pairs.Count -gt 0) {
                $pairs[0].Estado = & $tr "Recommended"
            }
            for ($pairIndex = 0; $pairIndex -lt $pairs.Count; $pairIndex++) {
                $pair = $pairs[$pairIndex]
                $pairState = if ($pairIndex -eq 0) { & $tr "Recommended" } else { & $tr "FreeNow" }
                $null = $suggestionsGrid.Rows.Add([object[]]@(
                    $pair.Porta1,
                    $pair.Porta2,
                    $pair.Diferenca,
                    $pairState
                ))
            }

            if ($pairs.Count -gt 0) {
                $suggestionsGrid.Rows[0].Selected = $true
                $usePairButton.Enabled = $true
                $copyPairButton.Enabled = $true
                $tabs.SelectedTab = $technicalTab
                $statusLabel.Text = (& $tr "PairsFound") -f $pairs.Count
            }
            else {
                $usePairButton.Enabled = $false
                $copyPairButton.Enabled = $false
                $statusLabel.Text = & $tr "NoPairs"
            }
        }
        catch {
            $statusLabel.Text = (& $tr "SearchError") -f $_.Exception.Message
            [System.Windows.Forms.MessageBox]::Show(
                $_.Exception.Message,
                (& $tr "AppTitle"),
                "OK",
                "Error"
            ) | Out-Null
        }
        finally {
            $suggestButton.Enabled = $true
            $form.UseWaitCursor = $false
        }
    }.GetNewClosure()

    $loadSelectedPair = {
        if ($null -eq $suggestionsGrid.CurrentRow) {
            return
        }

        $selectedFirst = [int]$suggestionsGrid.CurrentRow.Cells[0].Value
        $selectedSecond = [int]$suggestionsGrid.CurrentRow.Cells[1].Value
        $statusLabel.Text = & $tr "RevalidatingPair"
        $form.UseWaitCursor = $true
        [System.Windows.Forms.Application]::DoEvents()
        $stillFree = (Test-CandidatePort -Port $selectedFirst) -and (Test-CandidatePort -Port $selectedSecond)
        $form.UseWaitCursor = $false
        if (-not $stillFree) {
            $suggestionsGrid.CurrentRow.Cells[3].Value = & $tr "PairNoLongerFree"
            $suggestionsGrid.CurrentRow.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 224, 224)
            $statusLabel.Text = & $tr "PairNoLongerFree"
            return
        }

        $port1Input.Value = [decimal]$selectedFirst
        $port2Input.Value = [decimal]$selectedSecond
        $tabs.SelectedTab = $overviewTab
        $statusLabel.Text = & $tr "PairRevalidatedLoaded"
        & $refreshDiagnostics
    }.GetNewClosure()

    $runVerification = {
        $monitorWasEnabled = $timer.Enabled
        try {
            $uiState.CancelRequested = $false
            $uiState.SuppressHistory = $true
            $uiState.ForceHistoryEntry = $false
            $selectedMode = & $getSelectedMode
            $uiState.Mode = $selectedMode
            if ($monitorWasEnabled) {
                $timer.Stop()
            }
            $form.UseWaitCursor = $true
            $diagnoseButton.Enabled = $false
            $monitorButton.Enabled = $false
            $modeCombo.Enabled = $false
            $port1Input.Enabled = $false
            $port2Input.Enabled = $false
            $suggestButton.Enabled = $false
            $captureBeforeButton.Enabled = $false
            $compareAfterButton.Enabled = $false
            $cancelButton.Enabled = $true
            $cancelButton.Text = & $tr "Cancel"
            $statusLabel.Text = & $tr "Analyzing"
            [System.Windows.Forms.Application]::DoEvents()

            $testedPorts = @([int]$port1Input.Value, [int]$port2Input.Value)
            if ($selectedMode -eq "Availability") {
                foreach ($port in $testedPorts) {
                    $heartbeatResults.Remove($port)
                }
            }
            & $refreshDiagnostics

            if ($selectedMode -in @("Full", "Availability") -and -not $uiState.CancelRequested) {
                & $refreshWindowsResources
            }

            if ($selectedMode -ne "Availability" -and -not $uiState.CancelRequested) {
                $form.UseWaitCursor = $true
                $statusLabel.Text = if ($selectedMode -eq "Tcp") {
                    (& $tr "TestingTcpPort") -f $testedPorts[0]
                }
                elseif ($selectedMode -eq "Service") {
                    & $tr "TestingService"
                }
                else {
                    & $tr "Analyzing"
                }
                [System.Windows.Forms.Application]::DoEvents()

                $cancellationCheck = { return [bool]$uiState.CancelRequested }.GetNewClosure()
                foreach ($port in $testedPorts) {
                    if ($uiState.CancelRequested) {
                        break
                    }
                    $portSnapshot = @($uiState.LastSnapshot | Where-Object { [int]$_.Porta -eq $port } | Select-Object -First 1)
                    if ($portSnapshot.Count -gt 0 -and $portSnapshot[0].TransportDiagnosis -like "LIVRE:*") {
                        $heartbeatResults[$port] = [PSCustomObject]@{
                            Porta       = $port
                            Conectou    = $false
                            Respondeu   = $false
                            Aplicavel   = $false
                            Cancelado   = $false
                            Tipo        = if ($selectedMode -eq "Tcp") { "Tcp" } else { "Service" }
                            Resultado   = "Skipped"
                            LatenciaMs  = $null
                            Resposta    = ""
                            Diagnostico = "DISPONIVEL: porta livre; teste de servico nao aplicavel"
                            Momento     = Get-Date
                        }
                    }
                    elseif ($selectedMode -eq "Full") {
                        $realChannelEstablished = $portSnapshot.Count -gt 0 -and
                            $portSnapshot[0].PSObject.Properties.Name -contains "CanalRealEstabelecido" -and
                            [bool]$portSnapshot[0].CanalRealEstabelecido
                        if ($realChannelEstablished) {
                            $heartbeatResults[$port] = [PSCustomObject]@{
                                Porta       = $port
                                Conectou    = $true
                                Respondeu   = $true
                                Aplicavel   = $true
                                Cancelado   = $false
                                Tipo        = "Passive"
                                Resultado   = "Observed"
                                LatenciaMs  = $null
                                Resposta    = ""
                                Diagnostico = "CANAL REAL: conexao esperada estabelecida; teste ativo nao necessario"
                                Momento     = Get-Date
                            }
                            $uiState.ProbeFailureStreak[$port] = 0
                        }
                        else {
                            $heartbeatResults[$port] = [PSCustomObject]@{
                                Porta       = $port
                                Conectou    = $false
                                Respondeu   = $false
                                Aplicavel   = $true
                                Cancelado   = $false
                                Tipo        = "Passive"
                                Resultado   = "Inconclusive"
                                LatenciaMs  = $null
                                Resposta    = ""
                                Diagnostico = "INCONCLUSIVO: canal real nao foi observado conectado"
                                Momento     = Get-Date
                            }
                        }
                    }
                    elseif ($selectedMode -eq "Tcp") {
                        $statusLabel.Text = (& $tr "TestingTcpPort") -f $port
                        [System.Windows.Forms.Application]::DoEvents()
                        $heartbeatResults[$port] = Invoke-GenericTcpProbe -Port $port -TimeoutSeconds 2 -CancellationCheck $cancellationCheck
                    }
                    else {
                        $statusLabel.Text = (& $tr "TestingPort") -f $port
                        [System.Windows.Forms.Application]::DoEvents()
                        $probeResult = Invoke-ApplicationHeartbeatWithTimeout -Port $port -TimeoutSeconds 6 -CancellationCheck $cancellationCheck
                        $realChannelEstablished = $portSnapshot.Count -gt 0 -and
                            $portSnapshot[0].PSObject.Properties.Name -contains "CanalRealEstabelecido" -and
                            [bool]$portSnapshot[0].CanalRealEstabelecido

                        if ($probeResult.Diagnostico -like "FALHA*" -and $realChannelEstablished) {
                            $probeResult.Resultado = "Inconclusive"
                            $probeResult.Diagnostico = "CANAL REAL: conexao esperada estabelecida; teste ativo inconclusivo"
                            $uiState.ProbeFailureStreak[$port] = 0
                        }
                        elseif ($realChannelEstablished -and
                            $probeResult.PSObject.Properties.Name -contains "Resultado" -and
                            $probeResult.Resultado -eq "Inconclusive") {
                            $probeResult.Diagnostico = "CANAL REAL: conexao esperada estabelecida; teste ativo inconclusivo"
                            $uiState.ProbeFailureStreak[$port] = 0
                        }
                        elseif ($probeResult.Diagnostico -like "FALHA*") {
                            $previousFailures = if ($uiState.ProbeFailureStreak.ContainsKey($port)) { [int]$uiState.ProbeFailureStreak[$port] } else { 0 }
                            $failureCount = [Math]::Min(3, $previousFailures + 1)
                            $uiState.ProbeFailureStreak[$port] = $failureCount
                            if ($failureCount -lt 3) {
                                $probeResult.Resultado = "Inconclusive"
                                $probeResult.Diagnostico = "INCONCLUSIVO: falha isolada do teste ativo ($failureCount/3); repetir para confirmar"
                            }
                        }
                        else {
                            $uiState.ProbeFailureStreak[$port] = 0
                        }
                        $heartbeatResults[$port] = $probeResult
                    }
                }
            }

            $uiState.SuppressHistory = $false
            $uiState.ForceHistoryEntry = $true
            & $refreshDiagnostics

            if ($uiState.CancelRequested) {
                $statusLabel.Text = & $tr "VerificationCanceled"
            }
            elseif ($selectedMode -ne "Availability") {
                $verificationResults = @($testedPorts | ForEach-Object {
                    if ($heartbeatResults.ContainsKey($_)) { $heartbeatResults[$_] }
                })
                $skipped = @($verificationResults | Where-Object { $_.PSObject.Properties.Name -contains "Aplicavel" -and -not $_.Aplicavel })
                $applicable = @($verificationResults | Where-Object { -not ($_.PSObject.Properties.Name -contains "Aplicavel") -or $_.Aplicavel })
                $successful = @($applicable | Where-Object {
                    $_.PSObject.Properties.Name -contains "Resultado" -and $_.Resultado -in @("Success", "Observed")
                })
                $inconclusive = @($applicable | Where-Object {
                    ($_.PSObject.Properties.Name -contains "Resultado" -and $_.Resultado -eq "Inconclusive") -or
                    $_.Diagnostico -like "INCONCLUSIVO:*"
                })
                if ($skipped.Count -gt 0 -and $successful.Count -eq $applicable.Count) {
                    $statusLabel.Text = & $tr "AvailableResult"
                }
                elseif ($selectedMode -eq "Tcp" -and $successful.Count -eq 2) {
                    $statusLabel.Text = & $tr "TcpBothAction"
                }
                elseif ($selectedMode -eq "Tcp") {
                    $statusLabel.Text = & $tr "TcpFailure"
                }
                elseif ($selectedMode -eq "Full" -and $successful.Count -eq 2) {
                    $statusLabel.Text = & $tr "PassiveSafeAction"
                }
                elseif ($inconclusive.Count -gt 0) {
                    $statusLabel.Text = & $tr "ActiveAdvancedAction"
                }
                elseif ($successful.Count -eq 2) {
                    $statusLabel.Text = & $tr "ServiceBoth"
                }
                else {
                    $statusLabel.Text = & $tr "ServiceFailureOne"
                }
            }
        }
        catch {
            $statusLabel.Text = (& $tr "VerificationError") -f $_.Exception.Message
        }
        finally {
            $uiState.SuppressHistory = $false
            $uiState.ForceHistoryEntry = $false
            $diagnoseButton.Enabled = $true
            $monitorButton.Enabled = $true
            $modeCombo.Enabled = $true
            $port1Input.Enabled = $true
            $port2Input.Enabled = $true
            $suggestButton.Enabled = $true
            $captureBeforeButton.Enabled = $true
            $compareAfterButton.Enabled = $true
            $cancelButton.Enabled = $false
            $cancelButton.Text = & $tr "Cancel"
            $form.UseWaitCursor = $false
            if ($monitorWasEnabled) {
                $timer.Start()
            }
            $uiState.CancelRequested = $false
        }
    }.GetNewClosure()

    $markFunctionalOk = {
        $uiState.Functional = "Funcionou"
        Set-StatusCardState $functionalCard (& $tr "Working") (& $tr "ConfirmedByUser") "Success"
        $statusLabel.Text = & $tr "FunctionalConfirmed"
        & $refreshDiagnostics
    }.GetNewClosure()

    $markFunctionalFailure = {
        $uiState.Functional = "Falhou"
        Set-StatusCardState $functionalCard (& $tr "FunctionalFailure") (& $tr "RealOperationFailure") "Error"
        $statusLabel.Text = & $tr "FunctionalFailureRecorded"
        & $refreshDiagnostics
    }.GetNewClosure()

    $captureBefore = {
        try {
            $form.UseWaitCursor = $true
            $uiState.BeforeSnapshot = @(
                foreach ($port in @([int]$port1Input.Value, [int]$port2Input.Value)) {
                    Get-PortSnapshot -Port $port -ExpectedProcess $ProcessoEsperado
                }
            )
            $uiState.BeforeCapturedAt = Get-Date
            $uiState.ComparisonRows = @()
            & $renderComparison
            $comparisonStatusLabel.Text = (& $tr "BeforeCaptured") -f $uiState.BeforeCapturedAt.ToString("HH:mm:ss")
            $statusLabel.Text = $comparisonStatusLabel.Text
        }
        catch {
            $comparisonStatusLabel.Text = (& $tr "DiagnosisError") -f $_.Exception.Message
        }
        finally {
            $form.UseWaitCursor = $false
        }
    }.GetNewClosure()

    $compareAfter = {
        if ($uiState.BeforeSnapshot.Count -eq 0) {
            $comparisonStatusLabel.Text = & $tr "NoBefore"
            $statusLabel.Text = & $tr "NoBefore"
            return
        }

        try {
            $form.UseWaitCursor = $true
            $afterSnapshot = @(
                foreach ($port in @([int]$port1Input.Value, [int]$port2Input.Value)) {
                    Get-PortSnapshot -Port $port -ExpectedProcess $ProcessoEsperado
                }
            )
            $comparisonRows = @()
            foreach ($afterItem in $afterSnapshot) {
                $beforeItem = @($uiState.BeforeSnapshot | Where-Object { [int]$_.Porta -eq [int]$afterItem.Porta } | Select-Object -First 1)
                $beforeValue = if ($beforeItem.Count -gt 0) { $beforeItem[0] } else { $null }
                $beforeSignature = if ($null -eq $beforeValue) { "" } else { Get-SnapshotSignature -Snapshot @($beforeValue) }
                $afterSignature = Get-SnapshotSignature -Snapshot @($afterItem)
                $comparisonRows += [PSCustomObject]@{
                    Porta   = [int]$afterItem.Porta
                    Before = $beforeValue
                    After  = $afterItem
                    Changed = ($beforeSignature -ne $afterSignature)
                }
            }
            $uiState.ComparisonRows = @($comparisonRows)
            & $renderComparison
            $comparisonStatusLabel.Text = & $tr "ComparisonComplete"
            $statusLabel.Text = & $tr "ComparisonComplete"
        }
        catch {
            $comparisonStatusLabel.Text = (& $tr "DiagnosisError") -f $_.Exception.Message
        }
        finally {
            $form.UseWaitCursor = $false
        }
    }.GetNewClosure()

    $copySelectedPair = {
        if ($null -eq $suggestionsGrid.CurrentRow) {
            return
        }

        try {
            $first = [int]$suggestionsGrid.CurrentRow.Cells[0].Value
            $second = [int]$suggestionsGrid.CurrentRow.Cells[1].Value
            [System.Windows.Forms.Clipboard]::SetText("Porta 1: $first`r`nPorta 2: $second")
            $statusLabel.Text = (& $tr "PairCopied") -f $first, $second
        }
        catch {
            $statusLabel.Text = (& $tr "CopyError") -f $_.Exception.Message
        }
    }.GetNewClosure()

    $exportReport = {
        $dialog = [System.Windows.Forms.SaveFileDialog]::new()
        $dialog.Title = & $tr "ExportDialog"
        $dialog.Filter = & $tr "TextFile"
        $dialog.FileName = "diagnostico-canais-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"

        try {
            if ($dialog.ShowDialog($form) -ne [System.Windows.Forms.DialogResult]::OK) {
                return
            }

            $reportLines = @(
                (& $tr "ReportHeading")
                "$(& $tr 'GeneratedAt'): $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
                ""
                (& $tr "Summary")
                "$(& $tr 'Transport'): $($transportCard.State.Text) - $($transportCard.Detail.Text)"
                "$(& $tr 'LocalService'): $($serviceCard.State.Text) - $($serviceCard.Detail.Text)"
                "$(& $tr 'FunctionalTest'): $($functionalCard.State.Text) - $($functionalCard.Detail.Text)"
                "$(& $tr 'DiagnosticMode'): $($modeCombo.SelectedItem)"
                ""
                "$(& $tr 'AnalyzedPorts'): $([int]$port1Input.Value) / $([int]$port2Input.Value)"
                ""
                (& $tr "Details")
            )

            foreach ($item in @($uiState.LastSnapshot)) {
                $reportLines += "$(& $tr 'Port') $($item.Porta) | TCP: $(Convert-PortStateText -Text $item.TCP -Language $uiState.Language) | UDP: $(Convert-PortStateText -Text $item.UDP -Language $uiState.Language)"
                $reportLines += "$(& $tr 'Processes'): $(Convert-ProcessDisplayText -Text $item.Processos -Language $uiState.Language)"
                $reportLines += "$(& $tr 'Diagnosis'): $(Convert-DiagnosisText -Text $item.Diagnostico -Language $uiState.Language)"
                $reportLines += ""
            }

            if ($resourcesGrid.Rows.Count -gt 0) {
                $reportLines += & $tr "WindowsResourcesHeading"
                foreach ($row in $resourcesGrid.Rows) {
                    $reportLines += "$(& $tr 'Port') $($row.Cells[0].Value) | $(& $tr 'TcpReserved'): $($row.Cells[2].Value) | $(& $tr 'UdpReserved'): $($row.Cells[3].Value) | $(& $tr 'Firewall'): $($row.Cells[4].Value) | $(& $tr 'PortProxy'): $($row.Cells[5].Value)"
                }
                $reportLines += ""
            }

            if ($comparisonGrid.Rows.Count -gt 0) {
                $reportLines += & $tr "ComparisonHeading"
                foreach ($row in $comparisonGrid.Rows) {
                    $reportLines += "$(& $tr 'Port') $($row.Cells[0].Value) | $(& $tr 'Before'): $($row.Cells[1].Value) | $(& $tr 'After'): $($row.Cells[2].Value) | $(& $tr 'Change'): $($row.Cells[3].Value)"
                }
                $reportLines += ""
            }

            if ($suggestionsGrid.Rows.Count -gt 0) {
                $reportLines += & $tr "SuggestedPairsHeading"
                foreach ($row in $suggestionsGrid.Rows) {
                    $reportLines += "$($row.Cells[0].Value) / $($row.Cells[1].Value) | $($row.Cells[3].Value)"
                }
                $reportLines += ""
            }

            $reportLines += & $tr "SessionHistory"
            foreach ($entry in $historyList.Items) {
                $reportLines += [string]$entry
            }

            [System.IO.File]::WriteAllLines(
                $dialog.FileName,
                [string[]]$reportLines,
                [System.Text.Encoding]::UTF8
            )
            $statusLabel.Text = & $tr "ReportSuccess"
        }
        catch {
            $statusLabel.Text = (& $tr "ReportError") -f $_.Exception.Message
        }
        finally {
            $dialog.Dispose()
        }
    }.GetNewClosure()

    $diagnoseButton.Add_Click($runVerification)
    $cancelButton.Add_Click({
        $uiState.CancelRequested = $true
        $cancelButton.Enabled = $false
        $cancelButton.Text = & $tr "Canceling"
        $statusLabel.Text = & $tr "VerificationCanceled"
    }.GetNewClosure())
    $suggestButton.Add_Click($findSuggestions)
    $usePairButton.Add_Click($loadSelectedPair)
    $copyPairButton.Add_Click($copySelectedPair)
    $exportButton.Add_Click($exportReport)
    $functionalOkButton.Add_Click($markFunctionalOk)
    $functionalFailButton.Add_Click($markFunctionalFailure)
    $captureBeforeButton.Add_Click($captureBefore)
    $compareAfterButton.Add_Click($compareAfter)
    $clearHistoryButton.Add_Click({
        $historyList.Items.Clear()
        $uiState.LastHistorySignature = ""
        $uiState.ForceHistoryEntry = $false
        $statusLabel.Text = & $tr "HistoryCleared"
    }.GetNewClosure())
    $languageCombo.Add_SelectedIndexChanged({
        $uiState.Language = if ($languageCombo.SelectedIndex -eq 1) { "en-US" } else { "pt-BR" }
        & $applyLanguage
        $historyList.Items.Clear()
        $uiState.LastHistorySignature = ""
        & $refreshDiagnostics
    }.GetNewClosure())
    $modeCombo.Add_SelectedIndexChanged({
        if ($uiState.ApplyingLanguage) {
            return
        }
        $uiState.Mode = & $getSelectedMode
        foreach ($port in @([int]$port1Input.Value, [int]$port2Input.Value)) {
            $heartbeatResults.Remove($port)
            $uiState.ProbeFailureStreak.Remove($port)
        }
        & $refreshDiagnostics
    }.GetNewClosure())
    $suggestionsGrid.Add_CellDoubleClick($loadSelectedPair)
    $suggestionsGrid.Add_SelectionChanged({
        $hasSelection = $null -ne $suggestionsGrid.CurrentRow
        $usePairButton.Enabled = $hasSelection
        $copyPairButton.Enabled = $hasSelection
    }.GetNewClosure())

    $timer.Add_Tick({
        & $refreshDiagnostics
    }.GetNewClosure())

    $monitorButton.Add_Click({
        if ($timer.Enabled) {
            $timer.Stop()
            $monitorButton.Text = & $tr "StartMonitoring"
            $monitorButton.BackColor = [System.Drawing.SystemColors]::Control
            $statusLabel.Text = & $tr "MonitoringStopped"
        }
        else {
            & $refreshDiagnostics
            $timer.Start()
            $monitorButton.Text = & $tr "StopMonitoring"
            $monitorButton.BackColor = [System.Drawing.Color]::FromArgb(255, 224, 224)
            $statusLabel.Text = & $tr "MonitoringActive"
        }
    }.GetNewClosure())

    $form.Add_FormClosing({
        $timer.Stop()
        $timer.Dispose()
        try {
            Save-LocalChannelSettings `
                -Port1 ([int]$port1Input.Value) `
                -Port2 ([int]$port2Input.Value) `
                -StartPort ([int]$startInput.Value) `
                -Offset ([int]$offsetInput.Value) `
                -Language $uiState.Language `
                -Mode (& $getSelectedMode)
        }
        catch {
        }
        try {
            [NetMQ.NetMQConfig]::Cleanup($false)
        }
        catch {
        }
    }.GetNewClosure())

    & $applyLanguage
    & $refreshDiagnostics
    $null = $form.ShowDialog()
}

Assert-NetworkCmdlets

if ($Acao -notin @("Interface", "HeartbeatProbe")) {
    Write-Host "Diagnostico de Canais Locais"
    Write-Host "============================="
}

switch ($Acao) {
    "Interface" {
        Show-LocalChannelInterface
    }

    "Diagnosticar" {
        Show-TargetProcesses -ProcessNameFilter $ProcessoEsperado
        Write-Host ""
        Write-Host "Analisando as portas configuradas: $($Portas -join ', ')"
        $null = Show-PortDiagnosis -Ports $Portas -ExpectedProcess $ProcessoEsperado
        Write-Host ""
        Write-Host "Se o aplicativo estiver aberto e ambas continuarem livres, ele nao assumiu a configuracao."
        Write-Host "Se aparecer outro processo, existe conflito local no Windows."
    }

    "Heartbeat" {
        Write-Host "Executando teste ativo auxiliar nas portas: $($Portas -join ', ')"
        $results = foreach ($port in $Portas) {
            Invoke-ApplicationHeartbeatWithTimeout -Port $port -TimeoutSeconds 6
        }
        $results | Format-Table Porta, Conectou, Respondeu, LatenciaMs, Diagnostico -AutoSize -Wrap
        Write-Host "O teste e auxiliar. Respostas desconhecidas sao inconclusivas e nao comprovam uma operacao funcional."
    }

    "HeartbeatProbe" {
        Invoke-ApplicationHeartbeat -Port $PortaTeste | ConvertTo-Json -Compress
    }

    "Sugerir" {
        Write-Host "Procurando pares entre $Inicio e $Fim, com diferenca de $Diferenca..."
        $pairs = @(Find-PortPairs -StartPort $Inicio -EndPort $Fim -Offset $Diferenca -Count $Quantidade)

        if ($pairs.Count -eq 0) {
            Write-Host "Nenhum par foi encontrado no intervalo informado."
        }
        else {
            $pairs | Format-Table Porta1, Porta2, Diferenca, Estado -AutoSize
            Write-Host "A disponibilidade e momentanea. Valide o par novamente depois de reiniciar o aplicativo."
        }
    }

    "Monitorar" {
        Watch-PortPair `
            -Ports $Portas `
            -ExpectedProcess $ProcessoEsperado `
            -DurationSeconds $Segundos `
            -RefreshSeconds $IntervaloSegundos
    }
}
