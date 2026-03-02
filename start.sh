#!/bin/bash

echo "🎤 智能语音助手 - 启动脚本"
echo "================================"

# 检查 Node.js 是否安装
if ! command -v node &> /dev/null; then
    echo "❌ Node.js 未安装，请先安装 Node.js 18+"
    exit 1
fi

# 检查 npm 是否安装
if ! command -v npm &> /dev/null; then
    echo "❌ npm 未安装，请先安装 npm"
    exit 1
fi

# 进入项目目录
cd "$(dirname "$0")"

# 检查是否已安装依赖
if [ ! -d "node_modules" ] || [ ! -d "backend/node_modules" ] || [ ! -d "frontend/node_modules" ]; then
    echo "📦 安装依赖中..."
    npm run install:all
fi

# 检查环境变量文件
if [ ! -f "backend/.env" ]; then
    echo "⚙️  创建环境变量文件 backend/.env..."

    cat > backend/.env << 'EOF'
# 本地开发环境变量（请自行填写）
# Python 后端必需：
DASHSCOPE_API_KEY=

# 可选：端口覆盖
PORT=3002
EOF

    echo "请编辑 backend/.env 文件，填入你的 API 密钥（如 DASHSCOPE_API_KEY）"
    read -p "按回车键继续..."
fi

# 创建日志目录
mkdir -p backend/logs

echo "🚀 启动开发服务器..."
echo "前端地址: http://localhost:5173"
echo "后端地址: http://localhost:3002"
echo "按 Ctrl+C 停止服务器"
echo ""

# 启动开发服务器
npm run dev