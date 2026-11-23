@echo off
cd /d "%~dp0"
echo Starting the Congestion Route app...
echo If the browser does not open, open the address printed below.
echo Close this window or press Ctrl+C to quit.
echo.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 app.py --open
  goto done
)
where python >nul 2>nul
if not errorlevel 1 (
  python app.py --open
  goto done
)
echo.
echo Python 3.10 or newer is required.
echo Install it from https://www.python.org/downloads/ and check "Add python.exe to PATH".
:done
echo.
pause
