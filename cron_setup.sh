#!/bin/bash
# 安装 cron 任务：每日 06:00 daily，06:15 sector，06:30 compare2d，周一 07:00 weekly
# cron 不加载 shell 配置，crontab 行运行时 source .env 拿 TWELVEDATA_API_KEY
# 前置: 项目根目录需有 .env 文件（含 TWELVEDATA_API_KEY=xxx，权限 600，已 gitignore）

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_PATH" ]; then
    echo "⚠️  venv 不存在: $PYTHON_PATH"
    echo "   先运行: python3 -m venv $PROJECT_DIR/.venv && $PROJECT_DIR/.venv/bin/pip install -r $PROJECT_DIR/requirements.txt"
    exit 1
fi

ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "⚠️  .env 不存在: $ENV_FILE"
    echo "   cron 运行时需从此文件加载 TWELVEDATA_API_KEY。创建命令:"
    echo "   printf 'TWELVEDATA_API_KEY=你的key\n' > $ENV_FILE && chmod 600 $ENV_FILE"
    exit 1
fi

PIPELINE_SCRIPT="$PROJECT_DIR/src/pipeline.py"
CONFIG_PATH="$PROJECT_DIR/config/watchlist.yaml"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# cron 任务定义：set -a 让 source 的变量自动 export，PYTHONPATH=. 让 from src 导入生效
CRON_DAILY="0 6 * * * cd $PROJECT_DIR && set -a && . ./.env && set +a && PYTHONPATH=. $PYTHON_PATH $PIPELINE_SCRIPT --config $CONFIG_PATH --mode daily >> $LOG_DIR/cron_daily.log 2>&1"
CRON_COMPARE2D="30 6 * * * cd $PROJECT_DIR && set -a && . ./.env && set +a && PYTHONPATH=. $PYTHON_PATH $PIPELINE_SCRIPT --config $CONFIG_PATH --mode compare2d >> $LOG_DIR/cron_compare2d.log 2>&1"
CRON_WEEKLY="0 7 * * 1 cd $PROJECT_DIR && set -a && . ./.env && set +a && PYTHONPATH=. $PYTHON_PATH $PIPELINE_SCRIPT --config $CONFIG_PATH --mode weekly >> $LOG_DIR/cron_weekly.log 2>&1"
CRON_SECTOR="15 6 * * * cd $PROJECT_DIR && set -a && . ./.env && set +a && PYTHONPATH=. $PYTHON_PATH $PIPELINE_SCRIPT --config $CONFIG_PATH --mode sector >> $LOG_DIR/cron_sector.log 2>&1"

echo "=== 设置美股资金流监控 cron 任务 ==="
echo "项目目录: $PROJECT_DIR"
echo "Python: $PYTHON_PATH"
echo ".env: $ENV_FILE"
echo ""

# 一次性安装：移除旧 fundflow 任务（去重 + 清理旧格式），再写入 3 个新任务
# 单次 crontab - 调用，避免逐个添加时的读写竞态（macOS crontab 逐次写入会丢失前面的任务）
EXISTING=$(crontab -l 2>/dev/null | grep -v "us-fundflow-monitor" || true)
printf '%s\n' "$EXISTING" "$CRON_DAILY" "$CRON_SECTOR" "$CRON_COMPARE2D" "$CRON_WEEKLY" | crontab -
echo "已安装 4 个 cron 任务: daily(06:00) / sector(06:15) / compare2d(06:30) / weekly(周一 07:00)"

echo ""
echo "当前 crontab（fundflow 相关）:"
crontab -l 2>/dev/null | grep -F "$PROJECT_DIR" || echo "(无)"
