@echo off
title 推送到 Gitee
echo ===================================================
echo   正在推送到 Gitee: luzzzzzz/smart-exam-grader
echo ===================================================
echo.
echo [说明] 如果是首次推送，系统会提示您输入 Gitee 账号和密码。
echo [提示] 登录成功后，Windows 会自动记住凭据。
echo.

cd /d "%~dp0"
git push -u origin main

if %errorlevel% equ 0 goto SUCCESS
goto FAILED

:SUCCESS
echo.
echo ===================================================
echo   [恭喜] 代码已成功推送到 Gitee！
echo   仓库地址：https://gitee.com/luzzzzzz/smart-exam-grader
echo ===================================================
goto END

:FAILED
echo.
echo ===================================================
echo   [提示] 推送未完成。
echo   如果提示 Authentication failed，请检查账号密码。
echo ===================================================
goto END

:END
echo.
pause
