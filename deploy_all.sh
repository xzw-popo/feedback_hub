#!/usr/bin/env bash
# deploy_all.sh — 源码直推部署（路径 A）
# 无需本地 Docker，CloudBuild 云端构建
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo "=========================================="
echo "  Feedback Hub 一键部署（路径 A：源码直推）"
echo "=========================================="

# ---------- 前端 ----------
echo ""
echo "📦 [1/3] 构建前端..."
cd dashboard
npm run build
echo "✅ 前端构建完成"

echo ""
echo "🚀 [2/3] 部署前端到 CloudBase 静态托管..."
tcb hosting deploy dist -e feedback7-d3gz69ofw321c4da5
echo "✅ 前端部署完成"

# ---------- 后端 ----------
cd "$PROJECT_ROOT"

echo ""
echo "☁️  [3/3] 部署后端到 CloudRun（云端构建）..."
echo "" | tcb cloudrun deploy -s feedback-api --source . --port 9000 --force
echo "✅ 后端部署已提交"

echo ""
echo "=========================================="
echo "  🎉 部署完成！"
echo "  前端: https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com/"
echo "  后端: https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/"
echo "  ⏳ CloudRun 新版本通常 2-5 分钟生效"
echo "=========================================="
