@echo off
setlocal
set "PYTHONUTF8=1"

where py >nul 2>nul
if not errorlevel 1 (
  set "PY_CMD=py -3"
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=python"
  ) else (
    echo Python 3.11+ is required. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
  )
)

set "BASE_DIR=%~dp0"

if "%~1"=="login" goto do_login
if "%~1"=="--login" goto do_login
if "%~1"=="check" goto do_check
if "%~1"=="--check" goto do_check
if "%~1"=="acp" goto do_acp
if "%~1"=="--acp" goto do_acp
if "%~1"=="config" goto do_config
if "%~1"=="--config" goto do_config
if "%~1"=="help" goto do_help
if "%~1"=="--help" goto do_help
if "%~1"=="-h" goto do_help
goto do_run

:do_login
shift
%PY_CMD% "%BASE_DIR%app\acp_terminal.py" --login %*
exit /b %errorlevel%

:do_check
shift
%PY_CMD% "%BASE_DIR%app\diagnostics.py" %*
exit /b %errorlevel%

:do_acp
shift
%PY_CMD% "%BASE_DIR%app\acp_terminal.py" %*
exit /b %errorlevel%

:do_config
echo Config file: %BASE_DIR%app\config.json
type "%BASE_DIR%app\config.json"
exit /b 0

:do_help
echo Usage: agym [command ^| agy flags]
echo.
echo Service commands:
echo   agym login ^| --login       Browser OAuth login
echo   agym check ^| --check       Check proxy, runtimes, and profile
echo   agym acp   ^| --acp [args]  Direct text ACP client
echo   agym config ^| --config     Show config.json and proxy ports
echo   agym help  ^| --help        Show this help
echo.
echo Auto-approve:
echo   -y, -d, --yolo, yolo        Aliases for --dangerously-skip-permissions
echo.
echo agy CLI:
echo   agym                       Start interactive CLI in current directory
echo   agym -y                    Start with auto-approval of permissions
echo   agym -p "..."              Single prompt
echo   agym -c                    Continue last conversation
exit /b 0

:do_run
set "ARGS="
:arg_loop
if "%~1"=="" goto run_python
set "ARG=%~1"
if "%ARG%"=="-y" set "ARG=--dangerously-skip-permissions"
if "%ARG%"=="-d" set "ARG=--dangerously-skip-permissions"
if "%ARG%"=="--danger" set "ARG=--dangerously-skip-permissions"
if "%ARG%"=="--yolo" set "ARG=--dangerously-skip-permissions"
if "%ARG%"=="yolo" set "ARG=--dangerously-skip-permissions"
if "%ARG%"=="--skip-permissions" set "ARG=--dangerously-skip-permissions"

set "ARGS=%ARGS% %ARG%"
shift
goto arg_loop

:run_python
%PY_CMD% "%BASE_DIR%app\agy_native.py" %ARGS%
exit /b %errorlevel%
