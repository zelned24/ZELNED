@echo off
title Zel.NeD — Wireless Transfer for Nintendo 3DS
cd /d "%~dp0"

echo ========================================================
echo   Iniciando Zel.NeD (Cliente PC para Nintendo 3DS)
echo ========================================================

python pc\zelned_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Ocurrio un error al iniciar la aplicacion.
    pause
)
