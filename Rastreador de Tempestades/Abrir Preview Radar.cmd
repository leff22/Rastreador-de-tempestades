@echo off
setlocal

set "ROOT=%~dp0"
set "PORT=8765"

where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado no PATH.
  echo Abra o preview manualmente com:
  echo   python -m http.server %PORT%
  echo na pasta:
  echo   %ROOT%
  pause
  exit /b 1
)

echo Abrindo preview em http://127.0.0.1:%PORT%/preview/index.html
start "Servidor Preview Radar" python "%ROOT%scripts\radar_tracker_cli.py" serve-preview --port %PORT%

exit /b 0
