@echo off
setlocal DisableDelayedExpansion
REM Read the prepared selection through Python; never execute metadata as commands.
cd /d "%~dp0"
if errorlevel 1 exit /b 2
if exist "scripts\prepare_unified_workbench.py" goto rootfound
if not exist "prepare_unified_workbench.py" goto rootfound
cd /d "%~dp0.."
if errorlevel 1 exit /b 2
:rootfound
if not exist "scripts\prepare_unified_workbench.py" (
  echo Extract the complete Civil Buddy release before starting it.
  exit /b 2
)
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
if defined CIVIL_PYTHON goto explicit
where py >nul 2>nul
if errorlevel 1 goto findpython
py -3.11 -I -B -c "import sys" >nul 2>nul
if not errorlevel 1 goto pylauncher
:findpython
where python >nul 2>nul
if not errorlevel 1 goto systempython
echo Install CPython 3.11 for Windows x64, or set CIVIL_PYTHON to its full python.exe path.
echo This launcher does not install or repair dependencies.
exit /b 2
:explicit
"%CIVIL_PYTHON%" -I -B "scripts\prepare_unified_workbench.py" --launch --open %*
exit /b %errorlevel%
:pylauncher
py -3.11 -I -B "scripts\prepare_unified_workbench.py" --launch --open %*
exit /b %errorlevel%
:systempython
python -I -B "scripts\prepare_unified_workbench.py" --launch --open %*
exit /b %errorlevel%
