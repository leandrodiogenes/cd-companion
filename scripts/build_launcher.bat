@echo off
cd /d "%~dp0.."

echo Verificando PyInstaller...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Instalando PyInstaller...
    pip install pyinstaller
)

echo.
echo Compilando a partir de CD_Companion.spec...
rem Buildar pelo spec commitado (igual a CI). NUNCA usar "pyinstaller launcher.py":
rem isso regenera o spec e remove o bundle de launcher.ico (tray sem icone) e
rem volta console=True. O spec ja define onefile, console=False e datas correto.
pyinstaller --noconfirm --clean CD_Companion.spec

echo.
if exist dist\CD_Companion.exe (
    echo [OK] dist\CD_Companion.exe gerado com sucesso.
) else (
    echo [ERRO] Compilacao falhou. Veja o log acima.
)

pause
