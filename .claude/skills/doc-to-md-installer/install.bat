@echo off
chcp 65001 >nul 2>nul
setlocal EnableExtensions EnableDelayedExpansion
title doc-to-md 安裝程式 v1.4.6

echo.
echo ============================================================
echo   doc-to-md 安裝程式 v1.4.6
echo ============================================================
echo.

set "INSTALL_DIR=%USERPROFILE%\.doc-to-md"
set "SCRIPT_DIR=%~dp0"
set "SKILL_SRC=%SCRIPT_DIR%skill"
set "EXTRACT_DIR="
set "VENV_PY=%INSTALL_DIR%\venv\Scripts\python.exe"

:: If the package only contains skill.zip, extract it first.
if not exist "%SKILL_SRC%\scripts\requirements.txt" (
    if not exist "%SCRIPT_DIR%skill.zip" (
        echo 找不到 skill.zip，請確認安裝包已完整解壓縮。
        pause
        exit /b 1
    )

    set "EXTRACT_DIR=%TEMP%\doc-to-md-skill-%RANDOM%%RANDOM%"
    set "DOC_TO_MD_SKILL_ZIP=%SCRIPT_DIR%skill.zip"
    set "DOC_TO_MD_EXTRACT_DIR=!EXTRACT_DIR!"

    echo [準備] 解壓縮技能檔...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:DOC_TO_MD_SKILL_ZIP -DestinationPath $env:DOC_TO_MD_EXTRACT_DIR -Force" >nul
    if errorlevel 1 (
        echo 解壓縮 skill.zip 失敗，請先將整個安裝包解壓縮後再執行 install.bat。
        pause
        exit /b 1
    )
    set "SKILL_SRC=!EXTRACT_DIR!\skill"
)

if not exist "%SKILL_SRC%\scripts\requirements.txt" (
    echo 找不到 %SKILL_SRC%\scripts\requirements.txt
    echo 請確認安裝包內容完整，或重新下載安裝包。
    pause
    exit /b 1
)

:: Step 1: Find a real Python 3.8+. Avoid the Microsoft Store WindowsApps alias.
echo [Step 1/3] 檢查 Python 版本...
set "PY_CMD="
set "PY_VER="

call :try_python py -3.13
call :try_python py -3.12
call :try_python py -3.11
call :try_python py -3.10
call :try_python py -3.9
call :try_python py -3.8
call :try_python py -3
call :try_python python
call :try_python python3

if "%PY_CMD%"=="" (
    echo.
    echo 找不到可用的 Python 3.8 以上版本。
    echo.
    echo 請先安裝 Python：
    echo 1. 前往 https://www.python.org/downloads/
    echo 2. 下載安裝 Python 3.12 或更新版本
    echo 3. Windows 安裝時務必勾選 Add Python to PATH
    echo 4. 安裝完成後，關閉此視窗重新執行 install.bat
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

echo    找到 Python %PY_VER%：%PY_CMD%

:: Step 2: Create or repair venv.
echo.
echo [Step 2/3] 建立虛擬環境...

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo    偵測到舊的虛擬環境已失效，重新建立...
        rmdir /S /Q "%INSTALL_DIR%\venv" >nul 2>nul
    ) else (
        echo    虛擬環境已存在，跳過建立
    )
)

if not exist "%VENV_PY%" (
    %PY_CMD% -m venv "%INSTALL_DIR%\venv"
    if errorlevel 1 (
        echo    建立虛擬環境失敗，請截圖回報老師。
        pause
        exit /b 1
    )
    echo    虛擬環境建立在 %INSTALL_DIR%\venv
)

:: Step 3: Install dependencies and copy scripts.
echo.
echo [Step 3/3] 安裝 Python 套件（可能需要 1-2 分鐘）...

"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r "%SKILL_SRC%\scripts\requirements.txt" --quiet

if errorlevel 1 (
    echo    安裝失敗，請截圖錯誤訊息回報老師。
    pause
    exit /b 1
)
echo    所有套件安裝完成

copy /Y "%SKILL_SRC%\scripts\doc_to_md.py" "%INSTALL_DIR%\" >nul
if errorlevel 1 (
    echo    複製 doc_to_md.py 失敗。
    echo    來源：%SKILL_SRC%\scripts\doc_to_md.py
    echo    目的：%INSTALL_DIR%\
    pause
    exit /b 1
)
copy /Y "%SKILL_SRC%\scripts\requirements.txt" "%INSTALL_DIR%\" >nul
if errorlevel 1 (
    echo    複製 requirements.txt 失敗。
    echo    來源：%SKILL_SRC%\scripts\requirements.txt
    echo    目的：%INSTALL_DIR%\
    pause
    exit /b 1
)

if not exist "%INSTALL_DIR%\doc_to_md.py" (
    echo    找不到已安裝的 doc_to_md.py，請截圖回報老師。
    pause
    exit /b 1
)

(
echo @echo off
echo "%VENV_PY%" "%INSTALL_DIR%\doc_to_md.py" %%*
) > "%INSTALL_DIR%\doc-to-md.bat"

echo.
echo 驗證安裝...
"%VENV_PY%" -c "import sys; print('Python OK:', sys.version.split()[0])"
if errorlevel 1 (
    echo    Python 執行失敗，請截圖回報老師。
    pause
    exit /b 1
)

"%VENV_PY%" -c "import fitz, ebooklib, bs4, chardet, opencc, lxml; print('套件匯入 OK')"
if errorlevel 1 (
    echo.
    echo    套件匯入失敗。上方會顯示真正錯誤原因，請截圖回報老師。
    echo    常見原因：Python 版本/架構不相容，或 Windows 缺少必要執行環境。
    pause
    exit /b 1
)

"%VENV_PY%" "%INSTALL_DIR%\doc_to_md.py" --help

if errorlevel 1 (
    echo.
    echo    doc_to_md.py 驗證失敗。上方會顯示真正錯誤原因，請截圖回報老師。
    pause
    exit /b 1
)

if defined EXTRACT_DIR rmdir /S /Q "%EXTRACT_DIR%" >nul 2>nul

echo    驗證通過！
echo.
echo ============================================================
echo   安裝完成！
echo ============================================================
echo.
echo 接下來請在 Claude Desktop 加入技能：
echo 1. 打開 Claude Desktop
echo 2. 點左上方 Customize - Skills - + 號
echo 3. 選 Create Skill - Upload a skill
echo 4. 上傳安裝包裡的 skill.zip
echo 5. 確認 doc-to-md 出現在 Skills 列表中
echo.
echo 手動使用：
echo "%INSTALL_DIR%\doc-to-md.bat" --auto C:\Users\你的名字\Desktop\mybook.pdf -o C:\Users\你的名字\Desktop\
echo.
pause
exit /b 0

:try_python
if defined PY_CMD exit /b 0
for /f "usebackq tokens=1,2 delims=|" %%v in (`%* -c "import sys; exe=sys.executable; ok=sys.version_info >= (3, 8) and 'WindowsApps' not in exe; print(f'{sys.version_info.major}.{sys.version_info.minor}|{exe}' if ok else '')" 2^>nul`) do (
    set "PY_CMD=%*"
    set "PY_VER=%%v"
)
exit /b 0
