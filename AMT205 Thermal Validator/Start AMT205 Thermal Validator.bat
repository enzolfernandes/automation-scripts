@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m amt205_validator.app
) else (
  python -m amt205_validator.app
)
endlocal
