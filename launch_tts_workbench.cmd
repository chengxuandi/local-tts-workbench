@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 goto no_uv
uv run python -m app.main --open
if errorlevel 1 goto failed
exit /b 0

:no_uv
echo ERROR: uv.exe was not found in PATH.
echo Install uv from https://docs.astral.sh/uv/ and try again.
pause
exit /b 1

:failed
echo.
echo Local TTS Workbench failed to start. See the error above.
pause
exit /b 1
