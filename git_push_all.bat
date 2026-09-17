@echo off
title 一键同步 GitHub / Gitee
echo ===================================================
echo   SmartExamGrader 代码一键同步脚本 (GitHub / Gitee)
echo ===================================================
echo.

cd /d "%~dp0"

where git >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 系统未检测到 git 命令。
    pause
    exit /b 1
)

echo >>> 正在推送代码至远程仓库...

git remote | findstr /i "gitee" >nul
if %errorlevel% equ 0 (
    echo [*] 正在推送到国内 Gitee 仓库...
    git push gitee main
)

git remote | findstr /i "github" >nul
if %errorlevel% equ 0 (
    echo [*] 正在推送到国际 GitHub 仓库...
    git push github main
)

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
