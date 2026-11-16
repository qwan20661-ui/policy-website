@echo off
chcp 65001 >nul
echo ===========================================================
echo   政策解读系统 - JDBC 中间层服务编译脚本
echo ===========================================================
echo.

REM 检查 Java
java -version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Java 环境，请先安装 JDK 8+
    pause
    exit /b 1
)

REM 检查达梦 JDBC 驱动
if not exist "lib\jdbc\DmJdbcDriver8.jar" (
    echo [警告] 未找到达梦 JDBC 驱动!
    echo 请将 DmJdbcDriver8.jar 放入 lib\jdbc 目录
    echo.
)

REM 检查 pom.xml
if not exist "pom.xml" (
    echo [错误] 未找到 pom.xml
    pause
    exit /b 1
)

echo [1/3] 检查 Maven...
mvn -version >nul 2>&1
if errorlevel 1 (
    echo [警告] 未检测到 Maven，将使用直接编译
    echo.
    goto :direct_compile
)

echo [2/3] 使用 Maven 编译...
call mvn clean package -DskipTests
if errorlevel 1 (
    echo [错误] Maven 编译失败，尝试直接编译...
    goto :direct_compile
)

echo.
echo [3/3] 完成!
echo.
echo 运行方式:
echo   java -jar target\jdbc-service-1.0.0.jar
echo.
pause
exit /b 0

:direct_compile
echo.
echo [直接编译] 正在编译 JDBCService.java...
mkdir target 2>nul

REM Java wildcard loads the standalone parser and Dameng JDBC driver.
javac -encoding UTF-8 -d target -cp "lib\*;lib\jdbc\*" JDBCService.java
if errorlevel 1 (
    echo.
    echo [错误] 编译失败!
    echo 请确保:
    echo   1. 已安装 JDK
    echo   2. 达梦 JDBC 驱动已放入 lib 目录
    pause
    exit /b 1
)

echo.
echo [完成] 编译成功!
echo.
echo 运行方式:
echo   java -cp "target;lib/*" com.policy.jdbcservice.JDBCService
echo.
pause
