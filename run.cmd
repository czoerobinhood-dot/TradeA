@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Project virtual environment not found: "%PYTHON%"
    echo Create it with: python -m venv .venv
    echo Then install with: .venv\Scripts\python.exe -m pip install -e ".[test]"
    exit /b 1
)

"%PYTHON%" -m ashare_screener serve --config "%ROOT%config.example.json" --output "%ROOT%reports" --port 8765 --refresh-interval 0 %*
exit /b %ERRORLEVEL%
