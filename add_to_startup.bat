@echo off
chcp 65001 >nul
rem Creates a shortcut in your Startup folder so the widget launches with Windows.
rem Створює ярлик у папці автозавантаження, щоб віджет запускався разом з Windows.
cd /d "%~dp0"
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\EcoFlow Widget.lnk');" ^
  "$s.TargetPath = '%~dp0start.bat'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"
echo Added. The widget will start when you log in to Windows.
echo To undo: press Win+R, type shell:startup and delete "EcoFlow Widget".
echo.
echo Додано. Віджет запускатиметься, коли ви входите в Windows.
echo Щоб скасувати: натисніть Win+R, введіть shell:startup і видаліть "EcoFlow Widget".
pause
