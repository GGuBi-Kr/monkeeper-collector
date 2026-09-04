@echo off
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] Git is not installed on this PC.
  echo Install Git for Windows first: https://git-scm.com/download/win
  echo Then run this file again.
  echo.
  pause
  exit /b 1
)

echo.
echo ==========================================
echo   MonKeeper collector - upload to GitHub
echo ==========================================
echo.

rem --- build the workflow folder (cannot be written by remote tools) ---
if not exist ".github\workflows" mkdir ".github\workflows"
if exist "collect-workflow.yml" (
  copy /Y "collect-workflow.yml" ".github\workflows\collect.yml" >nul
  del "collect-workflow.yml" >nul 2>nul
  echo [OK] .github\workflows\collect.yml created
) else (
  if not exist ".github\workflows\collect.yml" (
    echo [ERROR] collect-workflow.yml is missing. Cannot continue.
    pause
    exit /b 1
  )
)
echo.

echo STEP 1. Create an EMPTY PUBLIC repository on GitHub.
echo         - Do NOT add README / .gitignore / license
echo         - Name suggestion: monkeeper-collector
echo.
echo STEP 2. Paste the repository URL below.
echo         Example: https://github.com/yourname/monkeeper-collector.git
echo.
set /p REPOURL="Repository URL: "

if "%REPOURL%"=="" (
  echo [ERROR] No URL entered. Aborted.
  pause
  exit /b 1
)

echo.
echo Uploading...
echo.

if not exist ".git" git init
git add -A
git -c user.name="monkeeper" -c user.email="monkeeper@users.noreply.github.com" commit -m "init: MonKeeper collector"
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin %REPOURL%
git push -u origin main

if errorlevel 1 (
  echo.
  echo [FAILED] Push failed. Check the URL and your GitHub login.
) else (
  echo.
  echo [DONE] Upload complete.
  echo.
  echo NEXT:
  echo  1^) Settings - Actions - General - Workflow permissions
  echo     Select "Read and write permissions" then Save
  echo  2^) Actions tab - collect - Run workflow
)
echo.
pause
