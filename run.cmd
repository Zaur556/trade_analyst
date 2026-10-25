@echo off
setlocal
cd /d "%~dp0"
if /I "%~1"=="watch" goto passthrough
if /I "%~1"=="alert-test" goto passthrough
if /I "%~1"=="ask" goto passthrough
if /I "%~1"=="explain" goto passthrough
if /I "%~1"=="backtest" goto passthrough
python -m commodity_agent run %*
goto end
:passthrough
python -m commodity_agent %*
:end
endlocal
