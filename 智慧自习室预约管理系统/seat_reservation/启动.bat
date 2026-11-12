@echo off
cd /d "%~dp0"

python -c "import fastapi, webview" >nul 2>&1
if errorlevel 1 goto install
goto run

:install
echo.
echo 检测到缺少运行依赖，正在安装（首次运行需要联网）...
echo.
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto retry

:run
python desktop_launcher.py
if errorlevel 1 goto logerror
goto end

:retry
echo.
echo 国内镜像安装失败，改用默认源重试...
python -m pip install -r requirements.txt
if errorlevel 1 goto failed
goto run

:failed
echo.
echo 依赖安装失败。请检查网络后手动执行：
echo     python -m pip install -r requirements.txt
echo.
pause
exit /b 1

:logerror
echo.
echo 程序异常退出，日志位置：var\logs\launcher.log
echo.
pause

:end
