@echo off
chcp 65001 >nul
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo RT430 PTP Active Probe v0.1.0
echo.
python "%~dp0rt430_ptp_probe.py" ^
  --iface "Realtek PCIe GbE Family Controller" ^
  --rt-mac "F8:02:78:10:B7:AE" ^
  --dest all ^
  --domain 10 ^
  --vlan 400 ^
  --pcp 6 ^
  --count 10

echo.
echo Pressione qualquer tecla para fechar.
pause >nul
