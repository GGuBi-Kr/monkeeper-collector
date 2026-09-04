@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] Git is not installed on this PC.
  echo Install Git for Windows: https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)

echo.
echo ==========================================
echo   MonKeeper collector - upload / update
echo ==========================================
echo.

rem --- build workflow folder (remote tools cannot write .github) ---
if not exist ".github\workflows" mkdir ".github\workflows"
if exist "collect-workflow.yml" (
  copy /Y "collect-workflow.yml" ".github\workflows\collect.yml" >nul
  del "collect-workflow.yml" >nul 2>nul
  echo [OK] .github\workflows\collect.yml ready
)

rem --- first time? ask for the repo URL ---
if not exist ".git" (
  git init
  echo.
  echo Paste the repository URL ^(must end with .git^)
  echo Example: https://github.com/GGuBi-Kr/monkeeper-collector.git
  echo.
  set /p REPOURL="Repository URL: "
  if "!REPOURL!"=="" (
    echo [ERROR] No URL entered. Aborted.
    pause
    exit /b 1
  )
  git branch -M main
  git remote add origin !REPOURL!
) else (
  echo [OK] Existing repository detected. Reusing remote.
)

echo.
echo Committing local changes...
git add -A
git -c user.name="monkeeper" -c user.email="monkeeper@users.noreply.github.com" commit -m "update: MonKeeper collector" 2>nul
if errorlevel 1 echo [INFO] Nothing new to commit.

echo.
echo Pulling commits made by GitHub Actions...
git -c user.name="monkeeper" -c user.email="monkeeper@users.noreply.github.com" pull --rebase origin main
if errorlevel 1 (
  echo.
  echo [FAILED] Pull failed. Resolve conflicts, then run this file again.
  pause
  exit /b 1
)

echo.
echo Pushing...
git push -u origin main

if errorlevel 1 (
  echo.
  echo [FAILED] Push failed. Check the URL and your GitHub login.
) else (
  echo.
  echo [DONE] Done.
  echo.
  echo NEXT: Actions tab - collect - Run workflow
)
echo.
pause
