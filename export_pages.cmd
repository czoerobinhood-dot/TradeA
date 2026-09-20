@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Project virtual environment not found: "%PYTHON%"
    exit /b 1
)

"%PYTHON%" -m ashare_screener export-pages --source "%ROOT%reports\latest.html" --output "%ROOT%site" %*
if errorlevel 1 exit /b %ERRORLEVEL%

"%PYTHON%" -m ashare_screener verify-pages --source "%ROOT%reports\latest.html" --output "%ROOT%site"
exit /b %ERRORLEVEL%
