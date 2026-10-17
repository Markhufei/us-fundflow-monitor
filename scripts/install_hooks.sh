#!/usr/bin/env bash
# 安装 L2 pre-commit hook（幂等：重复执行安全，覆盖旧版）
# hooks 不随 git clone 存活 -> 新机器/新 clone 后执行一次本脚本即可
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cp "$ROOT/scripts/hooks/pre-commit" "$ROOT/.git/hooks/pre-commit"
chmod +x "$ROOT/.git/hooks/pre-commit"
echo "✅ pre-commit hook 已安装: $ROOT/.git/hooks/pre-commit（断点扫描 + 全量 pytest）"
