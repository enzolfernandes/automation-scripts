@echo off
setlocal EnableDelayedExpansion
title TCP/ICMP Diagnostic Tool - Enterprise Edition
color 0A

:: =========================================================================
:: TCP NETWORK TESTING TOOL - ENTERPRISE EDITION
:: Objective        : Dual Probe + IEC 61850 Check + PDF Generation + Heuristics
:: Developed by     : Enzo Fernandes
:: GitHub           : https://github.com/enzolfernandes
:: =========================================================================

:MENU
cls
echo ===================================================
echo     INDUSTRIAL DIAGNOSTIC SUITE
echo ===================================================
echo.
set /p TARGET_IP="1. Enter the Target IP (e.g., 192.168.1.23): "
set /p TARGET_PORT="2. Enter the Primary TCP Port (e.g., 1001): "

:: Default Port 102 for IEC 61850 MMS
set "TARGET_PORT2=102"
set /p TARGET_PORT2="3. Enter Secondary Port (e.g., 102 for MMS, 0 to skip) [102]: "
set /p RUN_TIME="4. Execution time in seconds (e.g., 3600): "
echo.

:: =========================================================================
:: INPUT VALIDATION (#2 - IP format, numeric ports and duration; #13 - IPv6 warning)
:: =========================================================================

:: Detect IPv6 (contains colon)
echo %TARGET_IP% | findstr /c:":" >nul
if not errorlevel 1 (
    echo.
    echo [ERRO] IPv6 nao e suportado. Informe um endereco IPv4 (ex: 192.168.1.23^).
    echo.
    pause
    goto MENU
)

:: Validate IPv4 format (four numeric octets separated by dots)
:: Uses PowerShell to avoid the \r\n trailing character issue with findstr $ anchor
powershell -Command "if ('%TARGET_IP%' -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERRO] Formato de IP invalido. Use o formato IPv4 (ex: 192.168.1.23^).
    echo.
    pause
    goto MENU
)

:: Validate primary port (must be a positive integer)
set /a _P1CHK=%TARGET_PORT% 2>nul
if "%_P1CHK%"=="0" (
    echo.
    echo [ERRO] Porta primaria invalida. Informe um numero de porta entre 1 e 65535.
    echo.
    pause
    goto MENU
)

:: Validate duration (must be greater than zero)
set /a _DURCK=%RUN_TIME% 2>nul
if "%_DURCK%"=="0" (
    echo.
    echo [ERRO] Duracao invalida. Informe um valor em segundos maior que zero.
    echo.
    pause
    goto MENU
)

:: =========================================================================
:: OUTPUT FOLDER AND FILE PATHS
:: =========================================================================

:: Captures the safe timestamp (DD-MM-YYYY_HH-MM-SS)
for /f "delims=" %%a in ('powershell -Command "Get-Date -format 'dd-MM-yyyy_HH-mm-ss'"') do set TIMESTAMP=%%a

:: Creates a specific folder in the same directory as this script
set "OUT_DIR=%~dp0Diagnostic_%TARGET_IP%_%TIMESTAMP%"
mkdir "%OUT_DIR%"
if errorlevel 1 (
    echo.
    echo [ERRO] Nao foi possivel criar a pasta de saida: %OUT_DIR%
    echo Verifique permissoes ou comprimento do caminho.
    echo.
    pause
    goto MENU
)

:: Output paths for CSV, HTML, and PDF inside the new folder
set "OUT_FILE=%OUT_DIR%\Data_%TIMESTAMP%.csv"
set "HTML_FILE=%OUT_DIR%\Visual_Report.html"
set "PDF_FILE=%OUT_DIR%\Final_Report.pdf"

:: =========================================================================
:: PRE-RUN SUMMARY
:: =========================================================================

cls
echo ===================================================
echo          STARTING HEURISTIC DIAGNOSTIC TEST
echo ===================================================
echo.
echo Target      : %TARGET_IP%
echo Port 1      : %TARGET_PORT% (Primary Control)
echo Port 2      : %TARGET_PORT2% (IEC 61850 MMS)
echo Duration    : %RUN_TIME% seconds
echo Folder      : %OUT_DIR%
echo.
echo - The log is being silently saved in CSV format.
echo - TO STOP EARLY AND GENERATE THE PDF REPORT, PRESS 'Q'.
echo.

:: =========================================================================
:: ENGINE INVOCATION
:: NetDiag.Engine.ps1 resides alongside this .bat file.
:: -ExecutionPolicy RemoteSigned is sufficient for locally stored scripts. (#11)
:: =========================================================================

set "PS_ENGINE=%~dp0NetDiag.Engine.ps1"

if not exist "%PS_ENGINE%" (
    echo.
    echo [ERRO] Engine nao encontrado: %PS_ENGINE%
    echo Certifique-se de que NetDiag.Engine.ps1 esta na mesma pasta que este .bat
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%PS_ENGINE%" ^
    -Ip "%TARGET_IP%" ^
    -Port1 %TARGET_PORT% ^
    -Port2 %TARGET_PORT2% ^
    -Duration %RUN_TIME% ^
    -OutFile "%OUT_FILE%" ^
    -HtmlFile "%HTML_FILE%" ^
    -PdfFile "%PDF_FILE%"

:: =========================================================================
:: POST-RUN
:: =========================================================================

echo.
color 0B
echo Testing concluded. Diagnostic files have been saved to your folder:
echo %OUT_DIR%
echo.
echo Press any key to exit...
pause >nul
