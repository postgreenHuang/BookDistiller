@echo off
title Book-Distiller Build

echo Cleaning previous build artifacts...
if exist dist\Book-Distiller rmdir /s /q dist\Book-Distiller
if exist build\Book-Distiller rmdir /s /q build\Book-Distiller

py -3.12 -m PyInstaller build.spec --noconfirm --clean

echo.
if exist dist\Book-Distiller (
    echo Build succeeded! Output: dist\Book-Distiller
) else (
    echo Build FAILED — check errors above.
)
pause
