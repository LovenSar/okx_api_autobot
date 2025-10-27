@echo off
chcp 65001 >nul
echo ========================================
echo BTC自动交易机器人 - Web监控面板
echo ========================================
echo.

echo [1/3] 激活虚拟环境...
call venv\Scripts\activate.bat

echo [2/3] 检查依赖...
pip show flask >nul 2>&1
if errorlevel 1 (
    echo 正在安装Web依赖...
    pip install flask flask-cors
) else (
    echo Web依赖已安装 ✓
)

echo [3/3] 启动Web服务器...
echo.
python web_server.py

pause

