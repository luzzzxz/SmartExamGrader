@echo off
title 推送到 GitHub
echo ===================================================
echo   正在推送到 GitHub: luzzzxz/SmartExamGrader
echo ===================================================
echo.
echo [说明] 首次推送 GitHub，系统会弹出网页让您点击【Authorize】授权登录。
echo [提示] 授权成功后，Windows 会自动永久保存登录凭证。
echo.

cd /d "%~dp0"
git push -u -f github main

if %errorlevel% equ 0 goto SUCCESS
goto FAILED

:SUCCESS
echo.
echo ===================================================
echo   [恭喜] 代码已成功推送到 GitHub！
echo   仓库地址：https://github.com/luzzzxz/SmartExamGrader
echo ===================================================
goto END

:FAILED
echo.
echo ===================================================
echo   [提示] 推送未完成。
echo   请确认浏览器中已完成 GitHub 授权，或检查国际网络连接。
echo ===================================================
goto END

:END
echo.
pause
