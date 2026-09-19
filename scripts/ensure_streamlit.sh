#!/bin/bash
# Streamlit 服务守护：发现 com.hsf.streamlit 未加载或端口无响应时自动拉起
# 由 com.hsf.streamlit-watchdog.plist 每 5 分钟调用，日志写 logs/watchdog.log

LOG="$HOME/us-fundflow-monitor/logs/watchdog.log"
PLIST="$HOME/Library/LaunchAgents/com.hsf.streamlit.plist"
LABEL="com.hsf.streamlit"
DOMAIN="gui/$(id -u)"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

# 1. 服务未加载进 launchd -> bootstrap（幂等）
if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootstrap "$DOMAIN" "$PLIST" >>"$LOG" 2>&1 \
        && echo "[$(stamp)] 未加载，已 bootstrap" >>"$LOG" \
        || echo "[$(stamp)] bootstrap 失败（PLIST=$PLIST，检查文件存在与 launchctl print $DOMAIN/$LABEL）" >>"$LOG"
    exit 0
fi

# 2. 已加载但 8501 无响应 -> 强制重启（launchd KeepAlive 失效的兜底）
if ! curl -s -o /dev/null --max-time 10 http://localhost:8501; then
    launchctl kickstart -k "$DOMAIN/$LABEL" >>"$LOG" 2>&1 \
        && echo "[$(stamp)] 端口无响应，已 kickstart 重启" >>"$LOG" \
        || echo "[$(stamp)] kickstart 重启失败（检查 launchctl print $DOMAIN/$LABEL 与 logs/streamlit.log）" >>"$LOG"
fi
