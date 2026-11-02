@echo off
setlocal
set "PYTHONUTF8=1"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0..\app\acp_terminal.py" --login %*
goto done
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing
python "%~dp0..\app\acp_terminal.py" --login %*
goto done
:missing
echo Python 3.11+ is required. Install it from https://www.python.org/downloads/
pause
exit /b 1
:done
set "AGYCLI_EXIT=%errorlevel%"
if not "%AGYCLI_EXIT%"=="0" pause
exit /b %AGYCLI_EXIT%
