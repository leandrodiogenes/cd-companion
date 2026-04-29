@echo off
cd /d "%~dp0.."

echo Verificando PyInstaller...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Instalando PyInstaller...
    pip install pyinstaller
)

echo.
echo Compilando...
pyinstaller --onefile --console --name "CD_Companion" ^
  --icon "launcher.ico" ^
  --add-data "server;server" ^
  --add-data "overlay;overlay" ^
  --add-data "shared;shared" ^
  --add-data "lang;lang" ^
  launcher.py

echo.
if exist dist\CD_Companion.exe (
    echo [OK] dist\CD_Companion.exe gerado com sucesso.
    if exist dist\lang (
        echo [OK] Found existing language folder, not overwriting
    ) else (
        echo [WARN] language folder does not exist, copying from parent...
        xcopy /e /i /y lang dist\lang
    )
) else (
    echo [ERRO] Compilacao falhou. Veja o log acima.
)

pause
