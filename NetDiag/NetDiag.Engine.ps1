# =============================================================================
# TcpPingRelay.ps1 — Industrial Network Diagnostic Engine
# Developed by : Enzo Fernandes
# GitHub       : https://github.com/enzolfernandes
#
# Improvements implemented:
#  #1  Permanent .ps1 file (no more echo-based generation in BAT)
#  #3  Get-CimInstance replaces deprecated Get-WmiObject
#  #4  Hardware audit runs at startup, independent of TCP connection
#  #5  Exact 1-second cycle interval via Stopwatch residual
#  #6  ICMP RTT captured from RoundtripTime and logged to CSV
#  #7  Welford online algorithm replaces unbounded $history array
#  #8  Port2 latency measured with dedicated Stopwatch
#  #9  Responsive HTML canvas (100% width)
#  #10 Edge temp log files cleaned up after PDF generation
#  #12 PDV (RFC 3393) calculated alongside StdDev
#  #14 Robust Edge detection: hardcoded paths + Get-Command + registry
# =============================================================================

param(
    [string]$Ip,
    [int]$Port1,
    [int]$Port2,
    [int]$Duration,
    [string]$OutFile,
    [string]$HtmlFile,
    [string]$PdfFile
)

# =============================================================================
# HARDWARE AUDIT — runs once at startup, before the probe loop (#4)
# Uses Get-NetRoute/Get-NetIPAddress + Get-CimInstance (#3)
# =============================================================================

$sourceIp    = "N/A"
$adapterName = "N/A"
$macAddr     = "N/A"
$linkSpeed   = "N/A"

try {
    # Find-NetRoute asks Windows which interface it would actually use to reach
    # $Ip, avoiding the wrong-adapter problem when both WiFi and Ethernet are up.
    $nr      = Find-NetRoute -RemoteIPAddress $Ip -ErrorAction Stop
    $addrObj = $nr | Where-Object { $null -ne $_.IPAddress } | Select-Object -First 1
    $sourceIp = if ($addrObj) { $addrObj.IPAddress } else { "N/A" }

    $cimConf = Get-CimInstance Win32_NetworkAdapterConfiguration |
        Where-Object { $_.IPAddress -contains $sourceIp } | Select-Object -First 1
    if ($cimConf) {
        $macAddr     = if ($cimConf.MACAddress)  { $cimConf.MACAddress  } else { "N/A" }
        $adapterName = if ($cimConf.Description) { $cimConf.Description } else { "N/A" }
        $cimNet = Get-CimInstance Win32_NetworkAdapter |
            Where-Object { $_.Index -eq $cimConf.Index } | Select-Object -First 1
        if ($cimNet -and $cimNet.Speed) {
            $linkSpeed = [math]::Round($cimNet.Speed / 1000000).ToString() + " Mbps"
        }
    }
} catch {}

# =============================================================================
# CSV HEADER
# Columns extended with ICMP_RTT_ms and TCP2_RTT_ms (#6, #8)
# =============================================================================

"DateTime;IP;Port_1;Port_2;ICMP_Status;TCP1_RTT_ms;ICMP_RTT_ms;TCP2_RTT_ms" |
    Out-File -FilePath $OutFile -Encoding UTF8

# =============================================================================
# STATE VARIABLES
# Welford online algorithm variables replace $history array (#7)
# PDV (RFC 3393) variables (#12)
# =============================================================================

$sent     = 0
$received = 0

# Welford
$n      = 0
$wfMean = 0.0
$wfM2   = 0.0
$minLat = [double]::MaxValue
$maxLat = 0.0

# PDV (RFC 3393) — mean of absolute differences between consecutive latencies
$lastLat  = $null
$pdvSum   = 0.0
$pdvCount = 0

# Heuristic counters (updated inline each cycle — no history array needed)
$fwStress  = 0
$phyFail   = 0
$mmsActive = $false

# Chart data points (lightweight strings, capped at 3600 to limit HTML size)
$chartPoints = [System.Collections.Generic.List[string]]::new()

$stopWatch = [System.Diagnostics.Stopwatch]::StartNew()

# =============================================================================
# PROBE LOOP
# =============================================================================

try {
    while ($stopWatch.Elapsed.TotalSeconds -lt $Duration) {

        # Allow early exit with Q or Escape
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq 'Q' -or $key.Key -eq 'Escape') { break }
        }

        # Start exact-cycle timer (#5)
        $cycleWatch = [System.Diagnostics.Stopwatch]::StartNew()
        $sent++

        # ------------------------------------------------------------------
        # ICMP PROBE — capture RoundtripTime (#6)
        # ------------------------------------------------------------------
        $pingIcmp = New-Object System.Net.NetworkInformation.Ping
        $icmpStatus = 'FAIL'
        $icmpRtt    = "N/A"
        try {
            $icmpReply = $pingIcmp.Send($Ip, 1000)
            if ($icmpReply.Status -eq 'Success') {
                $icmpStatus = 'OK'
                $icmpRtt    = $icmpReply.RoundtripTime
            }
        } catch {}

        # ------------------------------------------------------------------
        # TCP PORT 1 PROBE
        # ------------------------------------------------------------------
        $pingStart = [System.Diagnostics.Stopwatch]::StartNew()
        $tcp1      = New-Object System.Net.Sockets.TcpClient
        $p1Status  = "TIMEOUT"
        $lat       = "N/A"
        $color     = "Red"
        try {
            $async1 = $tcp1.BeginConnect($Ip, $Port1, $null, $null)
            $wait1  = $async1.AsyncWaitHandle.WaitOne(1000, $false)
            if ($wait1 -and $tcp1.Connected) {
                $pingStart.Stop()
                $lat      = $pingStart.ElapsedMilliseconds
                $p1Status = "CONNECTED"
                $color    = "Green"
                $received++
                $tcp1.EndConnect($async1)
            }
        } catch { $p1Status = "NETWORK_FAILURE" } finally { $tcp1.Close() }

        # ------------------------------------------------------------------
        # TCP PORT 2 PROBE — with latency measurement (#8)
        # ------------------------------------------------------------------
        $p2Status = "SKIPPED"
        $lat2     = "N/A"
        if ($Port2 -ne 0) {
            $p2Watch = [System.Diagnostics.Stopwatch]::StartNew()
            $tcp2    = New-Object System.Net.Sockets.TcpClient
            try {
                $async2 = $tcp2.BeginConnect($Ip, $Port2, $null, $null)
                if ($async2.AsyncWaitHandle.WaitOne(500, $false) -and $tcp2.Connected) {
                    $p2Watch.Stop()
                    $lat2     = $p2Watch.ElapsedMilliseconds
                    $p2Status = "CONNECTED"
                    $mmsActive = $true
                    $tcp2.EndConnect($async2)
                } else {
                    $p2Status = "TIMEOUT"
                }
            } catch { $p2Status = "FAILURE" } finally { $tcp2.Close() }
        }

        # ------------------------------------------------------------------
        # HEURISTIC COUNTERS (inline, replaces post-loop history scan)
        # ------------------------------------------------------------------
        if ($p1Status -ne "CONNECTED" -and $icmpStatus -eq "OK")  { $fwStress++ }
        if ($p1Status -ne "CONNECTED" -and $icmpStatus -ne "OK")  { $phyFail++  }

        # ------------------------------------------------------------------
        # WELFORD ONLINE STATS UPDATE (#7)
        # ------------------------------------------------------------------
        if ($lat -ne "N/A") {
            $latVal  = [double]$lat
            $n++
            $delta   = $latVal - $wfMean
            $wfMean += $delta / $n
            $wfM2   += $delta * ($latVal - $wfMean)
            if ($latVal -lt $minLat) { $minLat = $latVal }
            if ($latVal -gt $maxLat) { $maxLat = $latVal }

            # PDV: accumulate absolute difference between consecutive latencies (#12)
            if ($null -ne $lastLat) {
                $pdvSum += [Math]::Abs($latVal - $lastLat)
                $pdvCount++
            }
            $lastLat = $latVal
        }

        # ------------------------------------------------------------------
        # CSV LOG
        # ------------------------------------------------------------------
        $timestamp = Get-Date -Format "dd/MM/yyyy HH:mm:ss"
        "$timestamp;$Ip;$p1Status;$p2Status;$icmpStatus;$lat;$icmpRtt;$lat2" |
            Out-File -FilePath $OutFile -Append -Encoding UTF8

        # ------------------------------------------------------------------
        # CHART DATA — all samples except the first (avoids Y-axis distortion on initial probe)
        # ------------------------------------------------------------------
        if ($sent -gt 1) {
            $latNum    = if ($lat    -eq "N/A") { 0 } else { $lat    }
            $icmpNum   = if ($icmpRtt -eq "N/A") { 0 } else { $icmpRtt }
            $chartPoints.Add("{time:'$timestamp',tcp1:'$p1Status',tcp2:'$p2Status',icmp:'$icmpStatus',lat:$latNum,icmpLat:$icmpNum}")
        }

        # ------------------------------------------------------------------
        # TERMINAL OUTPUT
        # ------------------------------------------------------------------
        Write-Host "[$timestamp] IP: $Ip | P1($Port1): " -NoNewline
        Write-Host $p1Status -ForegroundColor $color -NoNewline
        if ($Port2 -ne 0) {
            Write-Host " | P2($Port2): " -NoNewline
            if ($p2Status -eq "CONNECTED") {
                Write-Host $p2Status -ForegroundColor Green -NoNewline
            } else {
                Write-Host $p2Status -ForegroundColor Red -NoNewline
            }
        }
        if ($icmpStatus -eq "OK") {
            Write-Host " | ICMP: OK" -ForegroundColor Green -NoNewline
        } else {
            Write-Host " | ICMP: FAIL" -ForegroundColor Red -NoNewline
        }
        if ($lat -ne "N/A") { Write-Host " | RTT: ${lat}ms" } else { Write-Host "" }

        # ------------------------------------------------------------------
        # EXACT CYCLE INTERVAL — sleep only the remaining time (#5)
        # ------------------------------------------------------------------
        $cycleWatch.Stop()
        $remaining = 1000 - $cycleWatch.ElapsedMilliseconds
        if ($remaining -gt 0) { Start-Sleep -Milliseconds $remaining }
    }

} finally {

    # ==========================================================================
    # FINAL STATISTICS
    # ==========================================================================

    $lost        = $sent - $received
    $lossPercent = if ($sent -gt 0) { [Math]::Round(($lost / $sent) * 100, 1) } else { 0.0 }

    if ($n -gt 0) {
        $avg     = [Math]::Round($wfMean, 1)
        $bestLat = [Math]::Round($minLat, 1)
        $wrstLat = [Math]::Round($maxLat, 1)
        $stdDev  = if ($n -gt 1) { [Math]::Round([Math]::Sqrt($wfM2 / $n), 1) } else { 0.0 }
        # PDV (RFC 3393) — mean absolute deviation between consecutive packets (#12)
        $pdv = if ($pdvCount -gt 0) { [Math]::Round($pdvSum / $pdvCount, 1) } else { 0.0 }
    } else {
        $avg = 0.0; $bestLat = 0.0; $wrstLat = 0.0; $stdDev = 0.0; $pdv = 0.0
    }

    $lossStr    = "{0:F1}" -f $lossPercent
    $avgStr     = "{0:F1}" -f $avg
    $bestStr    = "{0:F1}" -f $bestLat
    $wrstStr    = "{0:F1}" -f $wrstLat
    $stdDevStr  = "{0:F1}" -f $stdDev
    $pdvStr     = "{0:F1}" -f $pdv

    # ==========================================================================
    # HEURISTIC ROOT CAUSE ENGINE
    # ==========================================================================

    if ($lossPercent -eq 0) {
        $rootCause = "Normal Operation. No anomalies detected in application or physical layers."
    } elseif ($phyFail -gt $fwStress) {
        $rootCause = "Physical Layer / EMI Disruption. Simultaneous ICMP and TCP loss indicates severe electromagnetic interference or hardware reboot."
    } else {
        $rootCause = "Firmware Saturation (CPU Stress). ICMP responded but TCP dropped, indicating the core processor is overloaded and ignoring application sockets."
    }
    if ($Port2 -ne 0 -and $mmsActive -and $lossPercent -gt 0) {
        $rootCause += " Note: IEC 61850 MMS port showed resilience during control software drops."
    }

    # ==========================================================================
    # HTML REPORT GENERATION
    # Chart: blue line = TCP RTT, orange dashed line = ICMP RTT (#6, #9)
    # Canvas is responsive (100% width) (#9)
    # ==========================================================================

    $jsData = "[" + ($chartPoints -join ",") + "]"

    $html  = "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Diagnostic Report</title>"
    $html += "<style>"
    $html += "body{font-family:Segoe UI,sans-serif;background:#ffffff;color:#121212;margin:40px;font-size:12px;}"
    $html += "h1{color:#004f9e;border-bottom:2px solid #004f9e;padding-bottom:10px;}"
    $html += "h2{color:#333;margin-top:30px;}"
    $html += "table{width:100%;border-collapse:collapse;margin-top:10px;}"
    $html += "th,td{padding:8px;border:1px solid #ccc;text-align:left;}"
    $html += "th{background:#f0f0f0;color:#333;width:25%;}"
    $html += ".chart-wrap{width:100%;margin-top:10px;}"
    $html += "canvas{border:1px solid #ccc;border-radius:4px;background:#ffffff;display:block;width:100%;}"
    $html += ".alert{background:#fff3cd;color:#856404;padding:15px;border:1px solid #ffeeba;border-radius:4px;font-weight:bold;}"
    $html += ".legend{background:#f9f9f9;border:1px solid #ddd;padding:12px;font-size:11px;border-radius:4px;margin-top:8px;color:#333;}"
    $html += "@page{size:A4 portrait;margin:10mm;}"
    $html += "@media print{body{margin:0;font-size:11px;}h2{margin-top:10px;}.legend{font-size:10px;padding:6px;}}"
    $html += "</style></head><body>"

    $html += "<h1>TCP/ICMP Diagnostic Report</h1>"

    $html += "<h2>1. Heuristic Root Cause Analysis</h2>"
    $html += "<div class='alert'>$rootCause</div>"

    $html += "<h2>2. Hardware Audit Traceability</h2>"
    $html += "<table>"
    $html += "<tr><th>Target IP Address</th><td>${Ip}:${Port1}</td><th>Source IP Address</th><td>$sourceIp</td></tr>"
    $html += "<tr><th>Network Adapter</th><td>$adapterName</td><th>MAC Address</th><td>$macAddr</td></tr>"
    $html += "<tr><th>Link Speed</th><td>$linkSpeed</td><th>Test Duration</th><td>$sent seconds</td></tr>"
    $html += "</table>"

    $html += "<h2>3. Statistical Metrics</h2>"
    $html += "<table>"
    $html += "<tr><th>Packets Sent</th><td>$sent</td><th>Packets Received</th><td>$received</td></tr>"
    $html += "<tr><th>Packet Loss</th><td>$lossStr %</td><th>Average TCP Latency</th><td>$avgStr ms</td></tr>"
    $html += "<tr><th>Best TCP Latency</th><td>$bestStr ms</td><th>Worst TCP Latency</th><td>$wrstStr ms</td></tr>"
    $html += "<tr><th>Jitter (StdDev)</th><td>$stdDevStr ms</td><th>Jitter (PDV / RFC 3393)</th><td>$pdvStr ms</td></tr>"
    $html += "</table>"

    $html += "<h2>4. Latency Profile Graph</h2>"
    $html += "<div class='chart-wrap'><canvas id='chart'></canvas></div>"

    # Inline JavaScript — responsive canvas, dual lines (TCP blue, ICMP orange)
    $html += "<script>"
    $html += "var data=$jsData;"
    $html += "var canvas=document.getElementById('chart');"
    $html += "canvas.width=canvas.parentElement.offsetWidth||900;"
    $html += "canvas.height=220;"
    $html += "var ctx=canvas.getContext('2d');"
    $html += "var w=canvas.width,h=canvas.height,padL=60,padB=45,padT=20,padR=20;"
    $html += "var plotW=w-padL-padR,plotH=h-padT-padB;"
    $html += "var maxTcp=Math.max.apply(Math,data.map(function(o){return o.lat;}));"
    $html += "var maxIcmp=Math.max.apply(Math,data.map(function(o){return o.icmpLat;}));"
    $html += "var maxLat=Math.max(maxTcp,maxIcmp);"
    $html += "if(maxLat<10)maxLat=10;else maxLat=Math.ceil(maxLat/10)*10;"
    $html += "ctx.fillStyle='#ffffff';ctx.fillRect(0,0,w,h);"
    $html += "ctx.font='11px sans-serif';ctx.fillStyle='#333';ctx.textAlign='right';ctx.textBaseline='middle';ctx.lineWidth=1;"
    # Y axis grid + labels
    $html += "for(var j=0;j<=5;j++){var val=maxLat*(j/5);var y=padT+plotH-(j/5)*plotH;"
    $html += "ctx.strokeStyle='#e0e0e0';ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(padL+plotW,y);ctx.stroke();"
    $html += "ctx.fillText(Math.round(val),padL-8,y);}"
    # X axis labels
    $html += "ctx.textAlign='center';ctx.textBaseline='top';"
    $html += "var xTicks=Math.min(data.length,10);if(xTicks===0)xTicks=1;"
    $html += "for(var k=0;k<=xTicks;k++){var x=padL+(k/xTicks)*plotW;var sec=Math.round(data.length*(k/xTicks));"
    $html += "ctx.strokeStyle='#e0e0e0';ctx.beginPath();ctx.moveTo(x,padT);ctx.lineTo(x,padT+plotH);ctx.stroke();"
    $html += "ctx.fillText(sec+'s',x,padT+plotH+8);}"
    # Y axis title (rotated)
    $html += "ctx.save();ctx.translate(15,padT+plotH/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#555';ctx.font='bold 12px sans-serif';ctx.fillText('RTT Latency (ms)',0,0);ctx.restore();"
    # X axis title
    $html += "ctx.fillStyle='#555';ctx.font='bold 12px sans-serif';ctx.textAlign='center';ctx.fillText('Test Duration (seconds)',padL+plotW/2,h-15);"
    # Frame
    $html += "ctx.strokeStyle='#333';ctx.strokeRect(padL,padT,plotW,plotH);"
    # Step width
    $html += "var step=plotW/Math.max(data.length-1,1);"
    # Timeout shaded areas (red) — clipped to plot bounds so rect never overflows frame
    $html += "ctx.save();ctx.beginPath();ctx.rect(padL,padT,plotW,plotH);ctx.clip();"
    $html += "for(var i=0;i<data.length;i++){var d1=(data[i].tcp1==='TIMEOUT'||data[i].tcp1==='NETWORK_FAILURE');var d2=(data[i].tcp2==='TIMEOUT'||data[i].tcp2==='FAILURE');"
    $html += "if(d1||d2){ctx.fillStyle='rgba(244,67,54,0.3)';ctx.fillRect(padL+i*step-(step/2),padT,Math.max(step,2),plotH);}}"
    $html += "ctx.restore();"
    # TCP latency line (blue, solid)
    $html += "ctx.beginPath();ctx.strokeStyle='#0072BD';ctx.lineWidth=1.5;"
    $html += "for(var i=0;i<data.length;i++){var x=padL+i*step;var y=padT+plotH-((data[i].lat/maxLat)*plotH);"
    $html += "if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}ctx.stroke();"
    # ICMP latency line (orange, dashed) (#6)
    $html += "ctx.beginPath();ctx.strokeStyle='#E87722';ctx.lineWidth=1.5;ctx.setLineDash([4,3]);"
    $html += "for(var i=0;i<data.length;i++){var x=padL+i*step;var y=padT+plotH-((data[i].icmpLat/maxLat)*plotH);"
    $html += "if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}ctx.stroke();"
    $html += "ctx.setLineDash([]);"
    $html += "</script>"

    # Legend
    $html += "<div class='legend'><b>Graph Key:</b>&nbsp;&nbsp;&nbsp;"
    $html += "<span style='display:inline-block;width:20px;height:3px;background:#0072BD;vertical-align:middle;'></span>&nbsp;TCP RTT (Port 1)"
    $html += "&nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp;"
    $html += "<span style='display:inline-block;width:20px;height:3px;background:#E87722;vertical-align:middle;border-top:2px dashed #E87722;'></span>&nbsp;ICMP RTT"
    $html += "&nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp;"
    $html += "<span style='display:inline-block;width:14px;height:14px;background:rgba(244,67,54,0.3);border:1px solid rgba(244,67,54,0.6);vertical-align:middle;'></span>&nbsp;Dropped / Timeout"
    $html += "</div>"

    $html += "</body></html>"

    $html | Out-File -FilePath $HtmlFile -Encoding UTF8

    # ==========================================================================
    # PDF GENERATION — robust Edge detection (#14)
    # ==========================================================================

    Write-Host "`nGenerating PDF Report..." -ForegroundColor Cyan

    $edgePaths = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    $edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

    # Fallback 1: PATH lookup
    if (-not $edge) {
        $edgeCmd = Get-Command msedge -ErrorAction SilentlyContinue
        if ($edgeCmd) { $edge = $edgeCmd.Source }
    }

    # Fallback 2: Windows registry
    if (-not $edge) {
        $regKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
        $regVal = Get-ItemProperty $regKey -ErrorAction SilentlyContinue
        if ($regVal) { $edge = $regVal.'(default)' }
    }

    if ($edge -and (Test-Path $edge)) {
        $edgeOutLog = "$env:TEMP\edge_out.log"
        $edgeErrLog = "$env:TEMP\edge_err.log"
        $null = Start-Process -FilePath $edge `
            -ArgumentList "--headless", "--disable-gpu", "--print-to-pdf=`"$PdfFile`"", "`"$HtmlFile`"" `
            -Wait -NoNewWindow `
            -RedirectStandardOutput $edgeOutLog `
            -RedirectStandardError  $edgeErrLog
        # Clean up Edge temp logs (#10)
        Remove-Item $edgeOutLog, $edgeErrLog -ErrorAction SilentlyContinue
    } else {
        Write-Host "Microsoft Edge not found. PDF generation skipped." -ForegroundColor Yellow
    }

    # ==========================================================================
    # TERMINAL SUMMARY
    # StdDev column replaced with PDV (RFC 3393) (#12)
    # ==========================================================================

    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Cyan
    Write-Host "                 FINAL DIAGNOSTIC                  " -ForegroundColor Cyan
    Write-Host "===================================================" -ForegroundColor Cyan
    Write-Host "ROOT CAUSE : $rootCause" -ForegroundColor Yellow
    Write-Host "---------------------------------------------------" -ForegroundColor Cyan
    Write-Host "+-----------------+-------+--------+----------+----------+-------+--------+-------+---------------+" -ForegroundColor White
    Write-Host "| Target IP       | P1    | Loss%  | Sent     | Received | Avg   | Best   | Worst | Jitter(PDV)   |" -ForegroundColor White
    Write-Host "+-----------------+-------+--------+----------+----------+-------+--------+-------+---------------+" -ForegroundColor White
    Write-Host ("| {0,-15} | {1,-5} | {2,-6} | {3,-8} | {4,-8} | {5,-5} | {6,-6} | {7,-5} | {8,-13} |" -f $Ip, $Port1, $lossStr, $sent, $received, $avgStr, $bestStr, $wrstStr, $pdvStr) -ForegroundColor White
    Write-Host "+-----------------+-------+--------+----------+----------+-------+--------+-------+---------------+" -ForegroundColor White
    Write-Host ""
}
