@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Remove the "downloaded from the internet" mark so Windows stops warning about these files
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse | Unblock-File" >nul 2>&1
echo Installing EcoFlow widget dependencies...
echo Встановлення залежностей віджета EcoFlow...
echo.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m pip install --upgrade -r requirements.txt
) else (
    python -m pip install --upgrade -r requirements.txt
)
if %errorlevel% neq 0 (
    echo.
    echo Install failed. Make sure Python 3.9+ is installed from python.org
    echo and "Add python.exe to PATH" was ticked during setup.
    echo.
    echo Помилка встановлення. Переконайтеся, що Python 3.9+ встановлено з python.org
    echo і під час встановлення було позначено "Add python.exe to PATH".
    pause
    exit /b 1
)
echo.
echo Done. Run start.bat to launch the widget.
echo Готово. Запустіть start.bat, щоб відкрити віджет.
pause
