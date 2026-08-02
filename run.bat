@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 메인 스크립트 실행
:TOP
python run.py
pause
goto TOP
