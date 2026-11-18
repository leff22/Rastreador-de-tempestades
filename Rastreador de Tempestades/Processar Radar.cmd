@echo off
setlocal

set "ROOT=%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado no PATH.
  pause
  exit /b 1
)

echo Processando sequencia completa e regenerando GIF...
python "%ROOT%scripts\radar_tracker_cli.py" process --preview-gif
if errorlevel 1 (
  echo Falha ao processar a sequencia.
  pause
  exit /b 1
)

echo Pipeline concluido.
exit /b 0
