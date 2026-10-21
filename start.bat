@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Clear the "downloaded from the internet" mark (e.g. after an update) so Windows stops asking
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse | Unblock-File" >nul 2>&1
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0ecoflow_widget.pyw" %*
    exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0ecoflow_widget.pyw" %*
    exit /b 0
)
echo Python was not found. Install Python 3.9+ from python.org and run install.bat first.
echo Python не знайдено. Встановіть Python 3.9+ з python.org і спершу запустіть install.bat.
pause
exit /b 1
