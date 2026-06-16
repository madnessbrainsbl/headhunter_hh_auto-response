@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist "hh_local_config.json" (
    echo Missing hh_local_config.json
    echo Copy hh_local_config.example.json to hh_local_config.json and fill HH_CLIENT_ID, HH_CLIENT_SECRET, HH_RESUME_ID.
    echo.
    pause
    exit /b 1
)

python test.py %*
set EXIT_CODE=%ERRORLEVEL%
echo.
pause
exit /b %EXIT_CODE%
