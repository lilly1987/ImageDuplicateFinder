@echo off
chcp 65001 >nul
cd /d "%~dp0"


REM 패키지 설치 (에러 무시)
:TOP
echo 📦 필요한 패키지를 설치하는 중입니다...
python -m pip install tkinterdnd2 pyyaml imagehash rich
echo ✅ 패키지 설치 완료
pause
goto TOP