@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not defined KEJIJU_DB_PASSWORD (
    echo 请先为当前终端设置 KEJIJU_DB_PASSWORD。
    exit /b 1
)
if defined KEJIJU_JAVA_HOME set "PATH=%KEJIJU_JAVA_HOME%\bin;%PATH%"
where javac >nul 2>nul
if errorlevel 1 (
    echo 请安装 JDK 并配置 PATH，或设置 KEJIJU_JAVA_HOME。
    exit /b 1
)
where node >nul 2>nul
if errorlevel 1 (
    echo 请将 Node.js 加入 PATH。
    exit /b 1
)
if not exist target mkdir target
javac -encoding UTF-8 -d target -cp "lib/*;lib/jdbc/*" JDBCService.java
if errorlevel 1 exit /b 1
start "JDBC Service" java -cp "target;lib/*;lib/jdbc/*" com.policy.jdbcservice.JDBCService
start "Node.js API" cmd /c "node server.js >> server-console.log 2>&1"
echo 网页端口默认 3000。也可以通过 KEJIJU_NODE_PORT 配置。
