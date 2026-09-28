@echo off
chcp 65001 >nul
setlocal

REM ===================================================================
REM  ComboMCP / 一键自检
REM
REM  检查三件事，前两件**不需要编辑器在运行**：
REM    1. 插件有没有被引擎加载过（cache\plugin\loaded.json）
REM    2. MCP 工具层能不能取到数据（--selftest）
REM    3. （可选）Remote Execution 通道——只有要读"编辑器里未保存的改动"才需要
REM ===================================================================

set "SCRIPT_DIR=%~dp0"

REM ---- 找 Python 解释器（用 UE 自带的，本机没有系统 Python）
set "PY="
for %%D in (
    "E:\UE_5.7"
    "D:\UE_5.7"
    "C:\Program Files\Epic Games\UE_5.7"
    "E:\UE_5.6\UE_5.6"
    "D:\UE_5.6\UE_5.6"
    "C:\Program Files\Epic Games\UE_5.6"
) do (
    if not defined PY (
        if exist "%%~D\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" (
            set "PY=%%~D\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
        )
    )
)

if not defined PY (
    echo [错误] 找不到 UE 自带的 Python 解释器。
    echo        请编辑本脚本，把引擎路径加进上面的候选列表。
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
echo 使用解释器: %PY%
echo.

echo ============ 第 1 步：插件是否已被引擎加载 ============
set "LOADED=%SCRIPT_DIR%cache\plugin\loaded.json"
if exist "%LOADED%" (
    echo [OK] 引擎加载过本插件。最近一次记录：
    type "%LOADED%"
) else (
    echo [ -- ] 还没有加载记录。
    echo        插件只在编辑器启动时加载；装上插件后**重启一次编辑器**，
    echo        本文件就会自动生成。
)
echo.

echo ============ 第 2 步：MCP 工具层自检 ============
"%PY%" "%SCRIPT_DIR%mcpserver\server.py" --selftest status
set STATUS=%errorlevel%
echo.
if not "%STATUS%"=="0" (
    echo [错误] 工具层报告了错误，见上方输出。
    pause
    exit /b 1
)

echo ============ 第 3 步（可选）：Remote Execution 通道 ============
echo 只有需要读取"编辑器内存里尚未保存的改动"时才用得到；
echo 日常读取走 T3D 缓存，不需要它。
echo.
"%PY%" "%SCRIPT_DIR%tools\probe_remote.py"
if not "%errorlevel%"=="0" (
    echo.
    echo [ -- ] 通道未就绪——这不影响日常使用，可以忽略。
)

echo.
echo ============================================================
echo  自检完成。
echo ============================================================
pause
exit /b 0
