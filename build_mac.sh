#!/bin/bash
set -e

echo "=== Book-Distiller macOS Build ==="

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo "错误: 未找到 .venv，请先运行: python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source .venv/bin/activate
python -m PyInstaller build.spec --noconfirm

echo ""
echo "构建完成！输出: dist/Book-Distiller.app"
