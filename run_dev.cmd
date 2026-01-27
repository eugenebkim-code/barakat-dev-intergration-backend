@echo off

echo [DEV] loading .env.dev ...

set ENV=dev

for /f "usebackq tokens=1,* delims==" %%a in (".env.dev") do (
    if not "%%a"=="" set %%a=%%b
)

echo [DEV] ENV loaded
echo SPREADSHEET_ID=%SPREADSHEET_ID%

python main.py

pause