@echo off
chcp 65001 >nul
title 推送代码到 Gitee
echo ===================================================
echo   正在推送到 Gitee: luzzzzzz/smart-exam-grader
echo ===================================================
echo.
echo [说明] 如果是首次推送，系统会提示您输入 Gitee 账号和密码（或弹出授权窗口）。
echo [提示] 登录成功后，Windows 会自动记住凭据，以后无需重复输入。
echo.

cd /d "%~dp0"
git push -u origin main

echo.
if %errorlevel% equ 0 (
    echo ===================================================
    echo   [恭喜] 代码已成功推送到 Gitee！
    echo   仓库地址：https://gitee.com/luzzzzzz/smart-exam-grader
    echo ===================================================
) else (
    echo ===================================================
    echo   [提示] 推送未完成。如果提示 Authentication failed，
    echo   请确认输入的 Gitee 账号/密码或私人令牌 (Personal Access Token)。
    echo ===================================================
)
echo.
pause
