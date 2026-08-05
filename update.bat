@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 📦 필요한 패키지(requirements.txt)를 설치 및 업데이트하는 중입니다...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo ✅ 필요한 모든 패키지 설치가 완료되었습니다.
pause