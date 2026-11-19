param(
    [ValidatePattern('^COM\d+$')]
    [string]$Port,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

function New-FlukeSerialPort {
    param([Parameter(Mandatory)][string]$PortName)

    $newPort = New-Object System.IO.Ports.SerialPort $PortName, 9600, 'None', 8, 'One'
    $newPort.Handshake = 'None'
    $newPort.ReadTimeout = 200
    $newPort.WriteTimeout = 1000
    return $newPort
}

function Receive-FlukeResponse {
    param(
        [Parameter(Mandatory)][System.IO.Ports.SerialPort]$Serial,
        [int]$TimeoutMs = 2500
    )

    $response = ''
    $totalTimer = [System.Diagnostics.Stopwatch]::StartNew()
    $quietTimer = [System.Diagnostics.Stopwatch]::StartNew()

    while ($totalTimer.ElapsedMilliseconds -lt $TimeoutMs) {
        Start-Sleep -Milliseconds 50
        $chunk = $Serial.ReadExisting()

        if ($chunk.Length -gt 0) {
            $response += $chunk
            $quietTimer.Restart()
        }
        elseif (($response.Length -gt 0) -and ($quietTimer.ElapsedMilliseconds -ge 200)) {
            break
        }
    }

    return $response.Trim()
}

function Get-AllComPorts {
    $ports = @()

    try {
        $ports = Get-CimInstance -ClassName Win32_PnPEntity |
            Where-Object { $_.Name -match '\(COM\d+\)' } |
            ForEach-Object { [regex]::Match($_.Name, '\((COM\d+)\)').Groups[1].Value } |
            Sort-Object -Unique
    }
    catch {
        $ports = [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object
    }

    return $ports
}

function Get-FlukeAdapterCandidates {
    try {
        $devices = Get-CimInstance -ClassName Win32_PnPEntity |
            Where-Object {
                $_.Name -match '\(COM\d+\)' -and
                $_.PNPDeviceID -match 'VID_0403[+&]PID_6001'
            }
    }
    catch {
        return @()
    }

    $candidates = foreach ($device in $devices) {
        $portMatch = [regex]::Match($device.Name, '\((COM\d+)\)')
        if ($portMatch.Success) {
            [PSCustomObject]@{
                Port        = $portMatch.Groups[1].Value
                DeviceName  = $device.Name
                PnpDeviceId = $device.PNPDeviceID
            }
        }
    }

    return @($candidates | Sort-Object { [int]($_.Port -replace 'COM', '') })
}

function Test-FlukePort {
    param([Parameter(Mandatory)][string]$PortName)

    $probe = $null

    try {
        $probe = New-FlukeSerialPort -PortName $PortName
        $probe.Open()
        $probe.DiscardInBuffer()

        # The meter requires remote mode for predictable RS-232 communication.
        $probe.Write("SYST:REM`r`n")
        Start-Sleep -Milliseconds 100
        [void](Receive-FlukeResponse -Serial $probe -TimeoutMs 150)

        $probe.DiscardInBuffer()
        $probe.Write("*IDN?`r`n")
        $identity = Receive-FlukeResponse -Serial $probe -TimeoutMs 1500

        return [PSCustomObject]@{
            Port     = $PortName
            Identity = $identity
            Error    = $null
        }
    }
    catch {
        return [PSCustomObject]@{
            Port     = $PortName
            Identity = $null
            Error    = $_.Exception.Message
        }
    }
    finally {
        if ($probe) {
            if ($probe.IsOpen) {
                try {
                    $probe.Write("SYST:LOC`r`n")
                    Start-Sleep -Milliseconds 100
                    $probe.Close()
                }
                catch {
                    # The process will release the port when it exits if the device was disconnected.
                }
            }
            $probe.Dispose()
        }
    }
}

function Resolve-FlukePort {
    param([string]$RequestedPort)

    if ($RequestedPort) {
        return [PSCustomObject]@{
            Port       = $RequestedPort
            Identity   = $null
            DeviceName = 'Manual port override'
        }
    }

    $candidates = @(Get-FlukeAdapterCandidates)

    if ($candidates.Count -eq 0) {
        $available = (Get-AllComPorts) -join ', '
        if (-not $available) { $available = 'none' }
        throw "No Fluke 884X-USB adapter was detected. Available serial ports: $available. Connect the cable or run the script with -Port COMx."
    }

    Write-Host "Detected $($candidates.Count) FTDI adapter candidate(s). Verifying meter identity..." -ForegroundColor Cyan
    $verified = @()

    foreach ($candidate in $candidates) {
        $test = Test-FlukePort -PortName $candidate.Port
        if ($test.Identity -match '^FLUKE,884[56]A,') {
            $verified += [PSCustomObject]@{
                Port       = $candidate.Port
                Identity   = $test.Identity
                DeviceName = $candidate.DeviceName
            }
        }
    }

    if ($verified.Count -eq 1) {
        return $verified[0]
    }

    if ($verified.Count -gt 1) {
        $ports = ($verified.Port -join ', ')
        throw "More than one Fluke 8845A/8846A was detected ($ports). Run the script with -Port COMx to choose one."
    }

    $candidatePorts = $candidates.Port -join ', '
    throw "The FTDI adapter candidate(s) on $candidatePorts did not identify as a Fluke 8845A/8846A. Check the meter's RS-232 setup, then run again or use -Port COMx."
}

function Send-FlukeCommand {
    param(
        [Parameter(Mandatory)][string]$Command,
        [int]$TimeoutMs = 2500
    )

    # The 8846A requires remote mode for RS-232 measurement commands.
    $script:serial.Write("SYST:REM`r`n")
    Start-Sleep -Milliseconds 100
    [void](Receive-FlukeResponse -Serial $script:serial -TimeoutMs 150)

    $script:serial.DiscardInBuffer()
    $script:serial.Write($Command.Trim() + "`r`n")
    $response = Receive-FlukeResponse -Serial $script:serial -TimeoutMs $TimeoutMs

    if ($response) {
        Write-Host "`nResponse: $response`n" -ForegroundColor Green
    }
    else {
        Write-Host "`nCommand sent. Commands without '?' normally do not return a response.`n" -ForegroundColor DarkYellow
    }

    return $response
}

function Read-FlukeErrorQueue {
    $count = 0

    do {
        $response = Send-FlukeCommand -Command 'SYST:ERR?'
        $count++
    }
    while ($response -and $response -notmatch '^\+?0,' -and $count -lt 16)
}

function Invoke-MeasurementShortcut {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Command
    )

    Write-Host "$Label changes the active measurement function to its preset configuration." -ForegroundColor DarkYellow
    [void](Send-FlukeCommand -Command $Command -TimeoutMs 5000)
}

function Show-Help {
    Write-Host @"

Connection:
  port                    Show the selected COM port and detected device.

Basic commands:
  id                      Identify the multimeter.
  read                    Take a reading using the current configuration.
  config                  Show the current measurement configuration.
  error                   Show the next queued error.
  errors                  Read all queued errors.
  status                  Read the status-byte register.
  event                   Read the standard-event register (clears that register).
  clear                   Clear the error queue and event/status registers.
  local                   Return control to the front panel.
  exit                    Return to local mode, close the COM port, and quit.

Measurement shortcuts (each changes the active measurement function):
  dcv | acv               Measure DC or AC voltage.
  dci | aci               Measure DC or AC current.
  resistance | fres       Measure 2-wire or 4-wire resistance.
  frequency | period      Measure frequency or period.
  capacitance | continuity | diode

Trigger and display:
  sample                  Show the sample count.
  trigger                 Show trigger count and source.
  display on|off|clear    Control the front-panel display.
  display text <message>  Show a message on the display.

SCPI:
  scpi <command>          Send an explicit SCPI command.
  <command>               Any other input is also sent as raw SCPI.
  Examples: *IDN?   SYST:ERR?   READ?   CONF?   TRIG:SOUR?

Avoid CAL... commands and *RST unless you have a defined procedure.
"@
}

$serial = $null

try {
    $connection = Resolve-FlukePort -RequestedPort $Port
    $Port = $connection.Port
    $serial = New-FlukeSerialPort -PortName $Port
    $serial.Open()

    if ($connection.Identity) {
        Write-Host "Detected and verified Fluke 8846A on ${Port}: $($connection.Identity)" -ForegroundColor Cyan
    }
    else {
        Write-Host "Using manually selected port $Port." -ForegroundColor Cyan
        $identity = Send-FlukeCommand -Command '*IDN?' -TimeoutMs 1500
        if ($identity -notmatch '^FLUKE,884[56]A,') {
            Write-Host 'Warning: the selected port did not identify as a Fluke 8845A/8846A.' -ForegroundColor DarkYellow
        }
    }

    Write-Host "Connected to Fluke 8846A through $Port." -ForegroundColor Cyan

    if ($VerifyOnly) {
        return
    }

    Show-Help

    while ($true) {
        $inputCommand = (Read-Host 'Fluke').Trim()

        if ($inputCommand -match '^(exit|quit)$') {
            break
        }

        switch -Regex ($inputCommand) {
            '^(help|\?)$' {
                Show-Help
                continue
            }
            '^port$' {
                Write-Host "Port: $Port" -ForegroundColor Cyan
                Write-Host "Device: $($connection.DeviceName)" -ForegroundColor Cyan
                if ($connection.Identity) {
                    Write-Host "Identity: $($connection.Identity)" -ForegroundColor Cyan
                }
                continue
            }
            '^(id|identify)$' {
                [void](Send-FlukeCommand -Command '*IDN?')
                continue
            }
            '^read$' {
                [void](Send-FlukeCommand -Command 'READ?' -TimeoutMs 5000)
                continue
            }
            '^config$' {
                [void](Send-FlukeCommand -Command 'CONF?')
                continue
            }
            '^error$' {
                [void](Send-FlukeCommand -Command 'SYST:ERR?')
                continue
            }
            '^errors$' {
                Read-FlukeErrorQueue
                continue
            }
            '^status$' {
                [void](Send-FlukeCommand -Command '*STB?')
                continue
            }
            '^event$' {
                [void](Send-FlukeCommand -Command '*ESR?')
                continue
            }
            '^clear$' {
                [void](Send-FlukeCommand -Command '*CLS')
                continue
            }
            '^(dcv|measure dc voltage)$' {
                Invoke-MeasurementShortcut -Label 'DC voltage measurement' -Command 'MEAS:VOLT:DC?'
                continue
            }
            '^(acv|measure ac voltage)$' {
                Invoke-MeasurementShortcut -Label 'AC voltage measurement' -Command 'MEAS:VOLT:AC?'
                continue
            }
            '^(dci|measure dc current)$' {
                Invoke-MeasurementShortcut -Label 'DC current measurement' -Command 'MEAS:CURR:DC?'
                continue
            }
            '^(aci|measure ac current)$' {
                Invoke-MeasurementShortcut -Label 'AC current measurement' -Command 'MEAS:CURR:AC?'
                continue
            }
            '^(resistance|ohm)$' {
                Invoke-MeasurementShortcut -Label '2-wire resistance measurement' -Command 'MEAS:RES?'
                continue
            }
            '^(fres|4w resistance)$' {
                Invoke-MeasurementShortcut -Label '4-wire resistance measurement' -Command 'MEAS:FRES?'
                continue
            }
            '^(frequency|freq)$' {
                Invoke-MeasurementShortcut -Label 'Frequency measurement' -Command 'MEAS:FREQ?'
                continue
            }
            '^period$' {
                Invoke-MeasurementShortcut -Label 'Period measurement' -Command 'MEAS:PER?'
                continue
            }
            '^(capacitance|cap)$' {
                Invoke-MeasurementShortcut -Label 'Capacitance measurement' -Command 'MEAS:CAP?'
                continue
            }
            '^(continuity|cont)$' {
                Invoke-MeasurementShortcut -Label 'Continuity measurement' -Command 'MEAS:CONT?'
                continue
            }
            '^diode$' {
                Invoke-MeasurementShortcut -Label 'Diode measurement' -Command 'MEAS:DIOD?'
                continue
            }
            '^sample$' {
                [void](Send-FlukeCommand -Command 'SAMP:COUN?')
                continue
            }
            '^trigger$' {
                [void](Send-FlukeCommand -Command 'TRIG:COUN?')
                [void](Send-FlukeCommand -Command 'TRIG:SOUR?')
                continue
            }
            '^display on$' {
                [void](Send-FlukeCommand -Command 'DISP ON')
                continue
            }
            '^display off$' {
                [void](Send-FlukeCommand -Command 'DISP OFF')
                continue
            }
            '^display clear$' {
                [void](Send-FlukeCommand -Command 'DISP:TEXT:CLE')
                continue
            }
            '^display text (.+)$' {
                $displayText = $Matches[1].Trim()
                if ($displayText.Contains('"')) {
                    Write-Host 'Display text cannot contain double quotes.' -ForegroundColor Red
                }
                else {
                    [void](Send-FlukeCommand -Command "DISP:TEXT `"$displayText`"")
                }
                continue
            }
            '^(local|loc)$' {
                $serial.Write("SYST:LOC`r`n")
                Write-Host 'Multimeter returned to local mode.' -ForegroundColor Cyan
                continue
            }
            '^scpi\s+(.+)$' {
                [void](Send-FlukeCommand -Command $Matches[1])
                continue
            }
            '^$' {
                continue
            }
            default {
                [void](Send-FlukeCommand -Command $inputCommand)
            }
        }
    }
}
catch {
    Write-Host "`nUnable to use ${Port}: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    if ($serial) {
        if ($serial.IsOpen) {
            try {
                $serial.Write("SYST:LOC`r`n")
                Start-Sleep -Milliseconds 100
                $serial.Close()
            }
            catch {
                # The process will release the port when it exits if the device was disconnected.
            }
        }
        $serial.Dispose()
    }
}
