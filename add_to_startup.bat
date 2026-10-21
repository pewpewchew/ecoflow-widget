@echo off
chcp 65001 >nul
rem Creates a shortcut in your Startup folder so the widget launches with Windows.
rem The shortcut starts Python directly (not a .bat file), so Windows never shows a security warning at startup.
rem Створює ярлик у папці автозавантаження, щоб віджет запускався разом з Windows.
rem Ярлик запускає Python напряму (а не .bat-файл), тому Windows не показує попередження безпеки під час запуску.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse | Unblock-File" >nul 2>&1

set "EF_DIR=%~dp0"
set "EF_TARGET="
set "EF_ARGS="
set "EF_STYLE=1"

for /f "delims=" %%i in ('where pyw 2^>nul') do if not defined EF_TARGET set "EF_TARGET=%%i"
if defined EF_TARGET set "EF_ARGS=-3 "%~dp0ecoflow_widget.pyw""

if not defined EF_TARGET for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined EF_TARGET set "EF_TARGET=%%i"
if defined EF_TARGET if not defined EF_ARGS set "EF_ARGS="%~dp0ecoflow_widget.pyw""

if not defined EF_TARGET (
    set "EF_TARGET=%~dp0start.bat"
    set "EF_STYLE=7"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\EcoFlow Widget.lnk'); $s.TargetPath = $env:EF_TARGET; $s.Arguments = [string]$env:EF_ARGS; $s.WorkingDirectory = $env:EF_DIR; $s.WindowStyle = [int]$env:EF_STYLE; $s.Save()"
if %errorlevel% neq 0 (
    echo Could not create the shortcut.
    echo Не вдалося створити ярлик.
    pause
    exit /b 1
)

echo Added. The widget will start when you log in to Windows.
echo To undo: press Win+R, type shell:startup and delete "EcoFlow Widget".
echo.
echo Додано. Віджет запускатиметься, коли ви входите в Windows.
echo Щоб скасувати: натисніть Win+R, введіть shell:startup і видаліть "EcoFlow Widget".
pause
