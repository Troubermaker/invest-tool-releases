#!/bin/bash
# ============================================================
# update.sh —— 一键更新到最新代码并启动
# 用法：chmod +x update.sh && ./update.sh
# 运行前请先关闭旧的 python main.py 进程
# ============================================================

set -e  # 任一步失败即中断

cd "$(dirname "$0")"  # 切到脚本所在目录

echo ""
echo "[1/4] === 拉取最新代码 ==="
git pull

echo ""
echo "[2/4] === 安装 / 更新前端依赖 ==="
cd frontend
npm install

echo ""
echo "[3/4] === 打包前端 ==="
npm run build
cd ..

echo ""
echo "[4/4] === 启动桌面应用 ==="
echo ""
python main.py
