@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Project virtual environment not found: "%PYTHON%"
    exit /b 1
)

pushd "%ROOT%"
"%PYTHON%" "scripts\audit_sohu_sources.py" %*
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
