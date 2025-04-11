@echo off
setlocal EnableDelayedExpansion

title Discord Music Bot Dashboard

echo ===== Discord Music Bot Dashboard =====
echo.

:: Read API_PORT and FRONTEND_PORT from .env file if it exists
SET API_PORT=8000
SET FRONTEND_PORT=3000
SET DEBUG=0

:: Try to read from .env file
if exist ".env" (
    echo Reading configuration from .env file...
    for /f "tokens=1,* delims==" %%a in (.env) do (
        if "%%a"=="API_PORT" (
            SET API_PORT=%%b
            echo Using API port from .env: !API_PORT!
        )
        if "%%a"=="FRONTEND_PORT" (
            SET FRONTEND_PORT=%%b
            echo Using Frontend port from .env: !FRONTEND_PORT!
        )
    )
)

:: Check for debug mode
if "%1"=="-debug" (
    SET DEBUG=1
    echo DEBUG MODE ENABLED: Will show detailed diagnostics
    echo.
)

:: First, ensure no previous instances are running
echo [1/3] Cleaning up previous processes...

:: Kill existing bot and dashboard processes
IF %DEBUG%==1 echo      Terminating existing Discord bot and dashboard processes...
taskkill /FI "WINDOWTITLE eq *Discord Bot*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq *Combined Bot+API*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq *Frontend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq *node*" /F >nul 2>&1

:: Remove stale PID file if it exists
if exist bot.pid (
    echo      - Removing stale bot PID file
    del /f bot.pid >nul 2>&1
)

:: Stop any processes using our ports
IF %DEBUG%==1 echo      Checking for processes using ports %API_PORT% and %FRONTEND_PORT%...
FOR /F "tokens=5" %%P IN ('netstat -ano ^| findstr ":%API_PORT% " ^| findstr "LISTENING" 2^>nul') DO (
    echo      - Stopping process using port %API_PORT%: %%P
    taskkill /PID %%P /F >nul 2>&1
)

FOR /F "tokens=5" %%P IN ('netstat -ano ^| findstr ":%FRONTEND_PORT% " ^| findstr "LISTENING" 2^>nul') DO (
    echo      - Stopping process using port %FRONTEND_PORT%: %%P
    taskkill /PID %%P /F >nul 2>&1
)

:: Check if Python is installed
echo [2/3] Checking requirements...
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.7+ and try again.
    goto :error
)

:: Make sure psutil is installed - check first to avoid pip warnings
IF %DEBUG%==1 echo      Checking for psutil installation...
python -c "try: import psutil; print('PSUTIL_INSTALLED'); exit(0)\nexcept ImportError: exit(1)" > psutil_check.txt 2>nul
set /p PSUTIL_STATUS=<psutil_check.txt
del psutil_check.txt >nul 2>&1

if not "%PSUTIL_STATUS%"=="PSUTIL_INSTALLED" (
    echo      - Installing psutil dependency...
    pip install psutil > pip_output.txt 2>&1
    
    :: Check specifically for "Successfully installed" in the output
    findstr /C:"Successfully installed psutil" pip_output.txt >nul
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: Failed to install psutil package. Details:
        type pip_output.txt
        del pip_output.txt >nul 2>&1
        goto :error
    ) else (
        echo      - Successfully installed psutil
    )
    del pip_output.txt >nul 2>&1
) else (
    echo      - psutil already installed
)

echo [3/3] Starting Bot with integrated API...
start "Combined Bot+API" cmd /k "title Combined Bot+API && echo === BOT AND API === && python bot.py --with-api"

:: Wait for API to initialize
echo      Waiting for bot and API to initialize...
timeout /t 15 /nobreak >nul

:: Verify API is running
echo      Testing API connectivity...
curl -s -o api_response.txt -w "STATUS_CODE=%%{http_code}" http://localhost:%API_PORT%/api/debug 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Could not connect to API server.
    echo Please check the Combined Bot+API window for errors.
    echo.
    if %DEBUG%==1 (
        echo CURL ERROR: %ERRORLEVEL%
        echo Attempting to diagnose issue...
        netstat -ano | findstr ":%API_PORT% "
        echo.
        echo Checking if server process is running:
        tasklist | findstr "python"
        echo.
    )
    echo The frontend will not be started.
    echo Bot is still running. Press any key to terminate all processes...
    pause >nul
    goto :cleanup
)

:: Check API response
IF %DEBUG%==1 (
    echo API Response:
    type api_response.txt
    echo.
)
del api_response.txt >nul 2>&1

echo      API connection successful!

:: Check for Node.js
echo [Checking for Node.js...]
where node >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo WARNING: Node.js is not installed or not in PATH.
    echo The frontend dashboard will not be available.
    echo.
    echo Bot and API are still running in their windows.
    echo Press any key to terminate all processes...
    pause >nul
    goto :cleanup
)

echo [Starting Frontend...]
cd dashboard\frontend

:: Install frontend dependencies if needed
if not exist node_modules (
    echo      Installing frontend dependencies...
    call npm install
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: Failed to install frontend dependencies.
        cd ..\..
        goto :error
    )
)

:: Create .env file for the frontend based on main .env values
echo Creating frontend .env file with API_PORT=!API_PORT!
echo REACT_APP_API_URL=http://localhost:!API_PORT! > dashboard\frontend\.env
echo GENERATE_SOURCEMAP=false >> dashboard\frontend\.env

:: Start React frontend
echo Starting frontend on port !FRONTEND_PORT!...
start "Frontend" cmd /k "title Frontend && echo === FRONTEND === && SET PORT=!FRONTEND_PORT! && npm start"
cd ..\..

echo.
echo ===== ALL COMPONENTS STARTED SUCCESSFULLY =====
echo.
echo Combined Bot+API:    Running in separate window
echo Frontend Dashboard:  http://localhost:%FRONTEND_PORT%
echo.
echo Press Ctrl+C to terminate all processes when done...
echo.

:: Keep the main window open to show instructions
echo Dashboard has been started. You can close this window when you're done.
echo.
echo NOTE: Closing this window will NOT stop the services.
echo To stop all services, press any key first.
pause >nul
goto :cleanup

:error
echo.
echo ERROR: The dashboard setup encountered problems.
echo Press any key to clean up and exit...
pause >nul

:cleanup
echo.
echo ===== Shutting Down All Components =====

:: Kill processes by window titles
taskkill /FI "WINDOWTITLE eq *Combined Bot+API*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq *Frontend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq *node*" /F >nul 2>&1

:: Also kill by port
FOR /F "tokens=5" %%P IN ('netstat -ano ^| findstr ":%API_PORT% " ^| findstr "LISTENING" 2^>nul') DO (
    taskkill /PID %%P /F >nul 2>&1
)

FOR /F "tokens=5" %%P IN ('netstat -ano ^| findstr ":%FRONTEND_PORT% " ^| findstr "LISTENING" 2^>nul') DO (
    taskkill /PID %%P /F >nul 2>&1
)

:: Remove PID file if it exists
if exist bot.pid (
    echo      - Removing bot PID file
    del /f bot.pid >nul 2>&1
)

echo All components have been terminated.
echo. 