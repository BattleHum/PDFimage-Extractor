
@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON=%~dp0.venv312\Scripts\python.exe"
set "ICON=%~dp0assets\MIL-EXTRACT.ico"
set "MAIN=%~dp0main.py"

echo ========================================
echo        MIL-EXTRACT - BUILD EXE
echo ========================================
echo.

REM Check Python environment
if not exist "%PYTHON%" (
    echo ERROR: Python 3.12 environment not found:
    echo "%PYTHON%"
    echo Check the .venv312 folder.
    pause
    exit /b 1
)

REM Check application files
if not exist "%ICON%" (
    echo ERROR: Icon file not found:
    echo "%ICON%"
    pause
    exit /b 1
)

if not exist "%MAIN%" (
    echo ERROR: main.py not found.
    pause
    exit /b 1
)

REM Install PyInstaller
echo Installing PyInstaller...
"%PYTHON%" -m pip install --upgrade PyInstaller
if errorlevel 1 goto :error

REM Clean previous build
echo.
echo Cleaning previous build files...

if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0dist" rmdir /s /q "%~dp0dist"
if exist "%~dp0MIL-EXTRACT.spec" del /q "%~dp0MIL-EXTRACT.spec"

REM Build executable
echo.
echo Building MIL-EXTRACT.exe...
echo.

"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "MIL-EXTRACT" ^
    --icon "%ICON%" ^
    --add-data "%ICON%;assets" ^
    --hidden-import pymupdf ^
    --hidden-import fitz ^
    --exclude-module onnxruntime ^
    --exclude-module pymupdf.layout ^
    --exclude-module pymupdf.layout.onnx ^
    "%MAIN%"

if errorlevel 1 goto :error

echo.
echo ========================================
echo BUILD SUCCESSFUL!
echo ========================================
echo.
echo Output:
echo "%~dp0dist\MIL-EXTRACT.exe"
echo.
pause
exit /b 0

:error
echo.
echo ========================================
echo BUILD FAILED!
echo ========================================
echo Review the error messages above.
pause
exit /b 1
