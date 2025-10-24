@echo off
echo 🎤 智能语音助手 - 启动脚本
echo ================================

REM 检查 Node.js 是否安装
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Node.js 未安装，请先安装 Node.js 18+
    pause
    exit /b 1
)

REM 检查 npm 是否安装
npm --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ npm 未安装，请先安装 npm
    pause
    exit /b 1
)

REM 进入项目目录
cd /d "%~dp0"

REM 检查是否已安装依赖
if not exist "node_modules" (
    echo 📦 安装依赖中...
    npm run install:all
)

if not exist "backend\node_modules" (
    echo 📦 安装后端依赖中...
    cd backend && npm install && cd ..
)

if not exist "frontend\node_modules" (
    echo 📦 安装前端依赖中...
    cd frontend && npm install && cd ..
)

REM 检查环境变量文件
if not exist "backend\.env" (
    echo ⚙️  创建环境变量文件...
    copy "backend\.env.example" "backend\.env"
    echo 请编辑 backend\.env 文件，填入你的 API 密钥
    echo 特别是 OPENAI_API_KEY，这是必需的
    pause
)

REM 创建日志目录
if not exist "backend\logs" mkdir backend\logs

echo 🚀 启动开发服务器...
echo 前端地址: http://localhost:5173
echo 后端地址: http://localhost:3001
echo 按 Ctrl+C 停止服务器
echo.

REM 启动开发服务器
npm run dev