@echo off
chcp 65001 >nul
REM ============================================================
REM update.bat —— 一键更新到最新代码并启动
REM 用法：双击运行，或在项目根目录下 cmd 里 `update`
REM 运行前请先关闭旧的 python main.py 进程
REM ============================================================

setlocal

echo.
echo [1/4] === 拉取最新代码 ===
git pull
if errorlevel 1 (
    echo.
    echo !!! git pull 失败，请检查网络 / 冲突 / 仓库状态
    goto :fail
)

echo.
echo [2/4] === 安装 / 更新前端依赖 ===
cd frontend
call npm install
if errorlevel 1 (
    echo.
    echo !!! npm install 失败，请检查 Node 版本（要 v18+）
    goto :fail
)

echo.
echo [3/4] === 打包前端 ===
call npm run build
if errorlevel 1 (
    echo.
    echo !!! npm run build 失败，请检查报错
    goto :fail
)
cd ..

echo.
echo [4/4] === 启动桌面应用 ===
echo.
python main.py
goto :end

:fail
echo.
echo ============ 更新流程中断 ============
cd %~dp0
pause
exit /b 1

:end
echo.
echo 应用已关闭
pause
endlocal
