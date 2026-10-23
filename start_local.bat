@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo  A股量化空间 · 本地服务
echo  地址: http://127.0.0.1:8010
echo  竞价窗口: http://127.0.0.1:8010/auction_dynamic.html
echo ================================================
python backend/server.py --port 8010
pause
