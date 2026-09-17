@echo off
chcp 65001 >nul
echo ===================================================
echo   SmartExamGrader 代码一键同步脚本 (GitHub / Gitee)
echo ===================================================
echo.

cd /d "%~dp0"

:: 检查是否存在 git
where git >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 系统未检测到 git 命令，请先安装 Git 环境。
    pause
    exit /b 1
)

:: 检查本地是否有未提交的更改
git status -s > "%temp%\git_status.tmp"
for %%R in ("%temp%\git_status.tmp") do if %%~zR gtr 0 (
    echo [提示] 检测到本地有新修改，正在执行自动提交...
    git add .
    set /p COMMIT_MSG="请输入本次更新说明（直接回车默认: update）: "
    if "%COMMIT_MSG%"=="" set COMMIT_MSG=update
    git commit -m "%COMMIT_MSG%"
)
del "%temp%\git_status.tmp" >nul 2>nul

echo.
echo >>> 正在推送代码至远程仓库...

:: 检查并推送 gitee
git remote | findstr /i "gitee" >nul
if %errorlevel% equ 0 (
    echo [1/2] 正在推送到国内 Gitee 仓库...
    git push gitee main
    if %errorlevel% equ 0 (
        echo [成功] Gitee 仓库同步完成！
    ) else (
        echo [警告] Gitee 仓库推送失败，请检查网络或权限。
    )
)

:: 检查并推送 github
git remote | findstr /i "github" >nul
if %errorlevel% equ 0 (
    echo [2/2] 正在推送到国际 GitHub 仓库...
    git push github main
    if %errorlevel% equ 0 (
        echo [成功] GitHub 仓库同步完成！
    ) else (
        echo [警告] GitHub 仓库推送失败，请检查网络或权限。
    )
)

:: 检查并推送 origin
git remote | findstr /i "origin" >nul
if %errorlevel% equ 0 (
    echo [*] 正在推送到 origin 仓库...
    git push origin main
)

echo.
echo ===================================================
echo   同步流程执行完毕！
echo ===================================================
pause
