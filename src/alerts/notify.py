"""通知模块：macOS 系统通知 + 日志记录。"""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from src.schema import severity_ge


def send(alert: Dict[str, Any], config: Dict[str, Any]) -> None:
    """
    发送通知。

    Args:
        alert: Alert 对象 {date, symbol, rule, severity, message, value}
        config: 配置字典，需含 alerts.min_severity 和 alerts.log_file
    """
    alerts_config = config.get("alerts", {})
    min_severity = alerts_config.get("min_severity", "MEDIUM")
    log_file = alerts_config.get("log_file", "logs/fundflow.log")

    # 写日志（所有级别都记录）
    _write_log(alert, log_file)

    # 仅高优先级发系统通知
    if severity_ge(alert["severity"], min_severity):
        _send_macos_notification(alert, log_file)


def _write_log(alert: Dict[str, Any], log_file: str) -> None:
    """写日志到文件。"""
    Path(log_file).parent.mkdir(exist_ok=True)
    ts = datetime.now().isoformat()
    line = f"{ts} [{alert['severity']}] {alert['symbol']} {alert['rule']}: {alert['message']}"
    if alert.get("value") is not None:
        line += f" (value={alert['value']})"
    detail = alert.get("detail")
    if detail:
        line += "\n" + detail
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _send_macos_notification(alert: Dict[str, Any], log_file: str) -> None:
    """发送 macOS 系统通知（仅 macOS）。

    优先 terminal-notifier：点击通知可打开日志文件查看逐笔明细。
    fallback osascript：点击会打开脚本编辑器（macOS 限制，无法回放明细）。
    """
    if os.uname().sysname != "Darwin":
        return

    title = f"[{alert['severity']}] {alert['symbol']} - {alert['rule']}"
    message = alert["message"]

    # terminal-notifier：-execute 点击通知用 Console 打开日志，可查看逐笔明细
    tn = shutil.which("terminal-notifier")
    if tn:
        abs_log = str(Path(log_file).resolve())
        try:
            subprocess.run(
                [tn, "-title", title, "-message", message,
                 "-execute", f'open -a Console "{abs_log}"',
                 "-sound", "default"],
                capture_output=True, timeout=5,
            )
            return
        except Exception:
            pass  # 静默失败，降级 osascript

    # fallback osascript（点击打开脚本编辑器，无明细）
    script = f'display notification "{message}" with title "{title}" sound name "default"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:
        pass  # 静默失败
