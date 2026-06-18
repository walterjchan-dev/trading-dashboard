@echo off
cd /d "%~dp0"

git add main.py monitor.json requirements.txt Procfile watchlist1.txt watchlist2.txt watchlist3.txt launch.bat deploy-github.bat
if errorlevel 1 goto error

set "MESSAGE=%~1"
if not defined MESSAGE set "MESSAGE=Update trading dashboard"

git commit -m "%MESSAGE%"
if errorlevel 1 goto error

git push origin main
if errorlevel 1 goto error

echo.
echo Deployment pushed to GitHub. Railway should redeploy automatically.
pause
exit /b 0

:error
echo.
echo Deployment did not complete. Review the message above.
pause
exit /b 1
