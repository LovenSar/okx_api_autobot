@echo off
chcp 65001 >nul
echo ========================================
echo   BTC交易机器人 - Docker启动脚本
echo ========================================
echo.

REM 检查Docker是否运行
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] Docker未运行或未安装！
    echo.
    echo 请先启动Docker Desktop
    echo 下载地址: https://www.docker.com/products/docker-desktop
    echo.
    pause
    exit /b 1
)

echo [✓] Docker运行正常
echo.

REM 检查.env文件
if not exist ".env" (
    echo [警告] 未找到.env配置文件！
    echo.
    echo 请先创建.env文件并配置API密钥
    echo 参考.env.example文件
    echo.
    pause
    exit /b 1
)

echo [✓] 配置文件已找到
echo.

REM 检查docker-compose.yml
if not exist "docker-compose.yml" (
    echo [错误] 未找到docker-compose.yml文件！
    pause
    exit /b 1
)

echo [启动] 正在启动Docker容器...
echo.

docker-compose up -d

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   启动成功！
    echo ========================================
    echo.
    echo 访问地址: http://localhost:8080
    echo.
    echo 常用命令:
    echo   查看日志: docker-compose logs -f
    echo   停止服务: docker-compose down
    echo   重启服务: docker-compose restart
    echo.
    echo 按任意键打开浏览器...
    pause >nul
    start http://localhost:8080
) else (
    echo.
    echo [错误] 启动失败，请查看错误信息
    echo.
    pause
)

