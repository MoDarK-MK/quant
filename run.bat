@echo off
REM ============================================================
REM  Trading Engine - one-click runner (Windows)
REM  Double-click this file, or run from a terminal:
REM     run.bat        -> run the engine demo
REM     run.bat test   -> run the test suite
REM ============================================================
setlocal
cd /d "%~dp0"

set "PYTHON=python"
where py >nul 2>nul && set "PYTHON=py -3"

echo [1/3] Checking Python...
%PYTHON% --version >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python 3.11+ not found. Install it from https://www.python.org/downloads/
    echo        Be sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
%PYTHON% --version

echo [2/3] Installing dependencies (first run only)...
%PYTHON% -m pip install -e ".[dev]" --quiet
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    pause
    exit /b 1
)

if /i "%~1"=="test" (
    echo [3/3] Running the test suite...
    %PYTHON% -m pytest -q
    if errorlevel 1 (
        echo ERROR: tests failed.
        pause
        exit /b 1
    )
    echo All tests passed.
    pause
    exit /b 0
)

echo [3/3] Running the engine demo...
%PYTHON% -m trading_engine.demo
if errorlevel 1 (
    echo ERROR: the engine exited with an error.
    pause
    exit /b 1
)

echo.
echo Done.
pause