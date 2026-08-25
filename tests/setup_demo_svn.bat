@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM   Semantic Merge - Local SVN demo repo bootstrap
REM   See tests/TESTING.md section 3.1 for details.
REM ============================================================

set "DEMO=%TEMP%\xmldev_demo_svn"
set "REPO=%DEMO%\repo"
set "WC=%DEMO%\wc"
set "TESTDIR=%~dp0"
set "DATA=%TESTDIR%data"

where svn >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 svn 命令。请安装 TortoiseSVN 时勾选 "command line client tools"，
  echo        或安装 SlikSVN 并加入 PATH。验证：svn --version
  exit /b 1
)

if not exist "%DATA%\base.xml"   ( echo [错误] 找不到 %DATA%\base.xml   & exit /b 1 )
if not exist "%DATA%\mine.xml"   ( echo [错误] 找不到 %DATA%\mine.xml   & exit /b 1 )
if not exist "%DATA%\theirs.xml" ( echo [错误] 找不到 %DATA%\theirs.xml & exit /b 1 )

echo.
echo === 清理旧演示数据 ===
if exist "%DEMO%" rmdir /s /q "%DEMO%"
mkdir "%DEMO%"

echo.
echo === 1. 创建本地 SVN 仓库 ===
svnadmin create "%REPO%"
if errorlevel 1 ( echo [错误] svnadmin 失败 & exit /b 1 )

REM Windows 下文件 URL 需要 file:/// + 正斜杠
set "REPO_FWD=%REPO:\=/%"
set "REPO_URL=file:///%REPO_FWD%"
echo Repo URL: %REPO_URL%

echo.
echo === 2. checkout 空工作副本 ===
svn checkout "%REPO_URL%" "%WC%"
if errorlevel 1 ( echo [错误] checkout 失败 & exit /b 1 )

echo.
echo === 3. 提交 base.xml 作为 r1 ===
copy /y "%DATA%\base.xml" "%WC%\items.xml" >nul
pushd "%WC%"
svn add items.xml
svn commit -m "r1: BASE version"
if errorlevel 1 ( echo [错误] r1 提交失败 & popd & exit /b 1 )

echo.
echo === 4. 用 theirs.xml 覆盖并提交 r2（模拟远程已有人更新） ===
copy /y "%DATA%\theirs.xml" "%WC%\items.xml" >nul
svn commit -m "r2: THEIRS version (remote update)"
if errorlevel 1 ( echo [错误] r2 提交失败 & popd & exit /b 1 )

echo.
echo === 5. 工作副本回退到 r1（让本地 BASE = r1） ===
svn update -r 1
if errorlevel 1 ( echo [错误] update -r 1 失败 & popd & exit /b 1 )

echo.
echo === 6. 用 mine.xml 覆盖工作副本（模拟本地未提交修改） ===
copy /y "%DATA%\mine.xml" "%WC%\items.xml" >nul
popd

echo.
echo ============================================================
echo   演示环境就绪
echo ============================================================
echo   工作副本目录: %WC%
echo   仓库目录:     %REPO%
echo.
echo   状态:
echo     SVN BASE     = base.xml  (r1)
echo     工作副本(MINE) = mine.xml  (本地未提交)
echo     远程 HEAD     = theirs.xml (r2)
echo.
echo   下一步:
echo     1. 启动 SheetMeld: 双击 start.bat 或运行 python server.py
echo     2. 在头部 + 按钮添加工作区: %WC%
echo     3. 切换到 "语义合并" tab，选 items.xml 开始
echo.
echo   清理演示数据: rmdir /s /q "%DEMO%"
echo ============================================================
