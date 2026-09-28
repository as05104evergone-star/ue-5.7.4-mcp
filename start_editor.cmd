@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ===================================================================
REM  ComboMCP / 启动 Unreal Editor
REM
REM  插件化之后，本脚本**不再给编辑器加任何特殊参数**：
REM    * ComboMCP.uplugin 的 "Plugins" 依赖会自动启用 PythonScriptPlugin
REM    * Content/Python 由引擎自动挂进 sys.path
REM  所以直接双击 .uproject 也完全可以，本脚本只是顺手做了引擎探测。
REM
REM  要注意的是：插件只在**启动时**加载，所以刚装上插件后必须重启一次
REM  编辑器，它才会出现在 Edit -> Plugins 列表里。
REM
REM  本脚本不修改项目里的任何文件。
REM ===================================================================

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."

REM ---- 自动发现 .uproject：不写死项目名，插件拷到别的项目也能直接用
set "PROJECT="
for %%F in ("%PROJECT_ROOT%\*.uproject") do (
    if not defined PROJECT set "PROJECT=%%~fF"
)

if not defined PROJECT (
    echo [错误] 在 %PROJECT_ROOT% 下找不到 .uproject
    pause
    exit /b 1
)

REM ---- 探测引擎安装（按 UnrealEditor.exe 是否存在判断，与通道设置无关）
set "ENGINE="
for %%D in (
    "E:\UE_5.7"
    "D:\UE_5.7"
    "C:\Program Files\Epic Games\UE_5.7"
    "E:\UE_5.6\UE_5.6"
    "D:\UE_5.6\UE_5.6"
    "C:\Program Files\Epic Games\UE_5.6"
) do (
    if not defined ENGINE (
        if exist "%%~D\Engine\Binaries\Win64\UnrealEditor.exe" set "ENGINE=%%~D"
    )
)

if not defined ENGINE (
    echo [错误] 没有找到 Unreal Engine 安装。
    echo        请编辑本脚本，把引擎路径加进上面的候选列表，
    echo        或者直接双击项目文件启动。
    pause
    exit /b 1
)

set "EDITOR=%ENGINE%\Engine\Binaries\Win64\UnrealEditor.exe"

echo.
echo ============================================================
echo  ComboMCP  启动编辑器
echo ============================================================
echo  引擎 : %ENGINE%
echo  项目 : %PROJECT%
echo  参数 : （无——插件会自动启用 Python，无需 -EnablePlugins）
echo.
echo  启动后可在 Edit -^> Plugins 里看到 "Combo Blueprint MCP"。
echo  插件菜单在 Tools -^> ComboMCP。
echo.
echo  首次加载大项目可能需要几分钟，请等编辑器完全就绪。
echo.

start "" "%EDITOR%" "%PROJECT%"

echo  已发出启动命令。
timeout /t 3 >nul
exit /b 0
