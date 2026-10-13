@echo off
chcp 65001 >nul
title AI-Robot-Server
cd /d "%~dp0backend"

set PORT=8000
set URL=http://127.0.0.1:8000

rem ===== 端口已被占用：说明服务已经在跑，直接打开网页即可 =====
netstat -ano | findstr "LISTENING" | findstr /c:":%PORT% " >nul
if %errorlevel%==0 (
    echo.
    echo   [提示] 检测到服务已经在运行，无需重复启动。
    echo   正在为你打开：%URL%
    echo.
    echo   若浏览器仍显示打不开，说明那个服务其实已经退出了：
    echo   请关掉所有黑窗口，再重新双击本文件。
    echo   也可以用 %URL%/debug.html 做接口自检。
    echo.
    start "" "%URL%"
    timeout /t 4 /nobreak >nul
    exit /b
)

echo ==================================================
echo   AI 智能机器人 启动中，请稍候...
echo.
echo   网页地址： %URL%
echo   接口自检： %URL%/debug.html
echo   接口文档： %URL%/docs
echo.
echo   重要：请保持本窗口开着，关掉窗口 = 停止服务
echo ==================================================
echo.

rem ===== 后台轮询端口：后端一旦真正就绪，才自动打开浏览器 =====
rem （避免"浏览器开得比后端快"，那样会看到"无法访问此网站"）
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 240;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%PORT%);$c.Close();Start-Process '%URL%';break}catch{Start-Sleep -Milliseconds 500}}"

"..\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port %PORT%

echo.
echo 服务已停止。
pause >nul
