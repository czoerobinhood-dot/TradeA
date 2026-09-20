@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "WRANGLER=%ROOT%node_modules\.bin\wrangler.cmd"
set "PAGES_URL=https://tradea-3al.pages.dev"
set "WRANGLER_LOG_PATH=%TEMP%\tradea-wrangler-publish.log"
set "PYTEST_BASETEMP=%ROOT%tmp\pytest-publish-%RANDOM%-%RANDOM%"

if not exist "%PYTHON%" (
    echo Project virtual environment not found: "%PYTHON%"
    exit /b 1
)

if not exist "%WRANGLER%" (
    echo Wrangler is not installed. Run npm install in "%ROOT%" first.
    exit /b 1
)

cd /d "%ROOT%"
if errorlevel 1 exit /b %ERRORLEVEL%

call "%ROOT%export_pages.cmd"
if errorlevel 1 exit /b %ERRORLEVEL%

"%PYTHON%" -m pytest tests\test_pages.py tests\test_members_schema.py -p no:cacheprovider --basetemp "%PYTEST_BASETEMP%"
set "PYTEST_EXIT=%ERRORLEVEL%"
if exist "%PYTEST_BASETEMP%" rmdir /s /q "%PYTEST_BASETEMP%"
if not "%PYTEST_EXIT%"=="0" exit /b %PYTEST_EXIT%

call npm run test:worker
if errorlevel 1 exit /b %ERRORLEVEL%

call npm run pages:deploy
if errorlevel 1 exit /b %ERRORLEVEL%

"%PYTHON%" -m ashare_screener verify-pages --source "%ROOT%reports\latest.html" --output "%ROOT%site" --url "%PAGES_URL%"
exit /b %ERRORLEVEL%
