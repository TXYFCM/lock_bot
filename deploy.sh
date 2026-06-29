#!/bin/bash
# deploy.sh — 一键部署监控仪表盘到新环境
# 用法: bash deploy.sh

set -e

echo "========================================"
echo "  开发机集群资源监控 — 部署脚本"
echo "========================================"
echo ""

# 检查 Node.js
if ! command -v node &> /dev/null; then
  echo "❌ 未安装 Node.js，请先安装: https://nodejs.org/"
  exit 1
fi

NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
echo "✓ Node.js $(node -v)"

# 定位脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查必要文件
for f in proxy.js index.html api.js adapter.js; do
  if [ ! -f "$f" ]; then
    echo "❌ 缺少文件: $f"
    exit 1
  fi
done
echo "✓ 所有必要文件就绪"

# config.json 处理
if [ ! -f "config.json" ]; then
  if [ -f "config.example.json" ]; then
    cp config.example.json config.json
    echo "✓ 已从 config.example.json 创建 config.json"
    echo ""
    echo "⚠️  请编辑 config.json 填入实际的服务器地址:"
    echo "   vim config.json"
    echo ""
  else
    echo "⚠️  config.example.json 不存在，将使用 proxy.js 内置默认值"
  fi
else
  echo "✓ config.json 已存在"
fi

echo ""
echo "========================================"
echo "  启动代理服务..."
echo "========================================"
echo ""

# 支持 PORT 环境变量覆盖
if [ -n "$PORT" ]; then
  export PROXY_PORT="$PORT"
fi

exec node proxy.js
