"""周六验证：美股周五收盘数据是否已自动入库（M2 周六采集修复的持续守护）。

launchd 每周六 07:00 触发（此时 06:00/06:15/06:30 三个采集任务已完成）。
检查 daily_snapshot 与 sector_snapshot 的 MAX(date) 是否 = 昨天（周五），
terminal-notifier 通知结果，日志 logs/friday_check.log。

手动运行: PYTHONPATH=. .venv/bin/python scripts/verify_friday_collect.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "fundflow.db"
LOG = ROOT / "logs" / "friday_check.log"


def _notify(title: str, message: str, log_path: Path):
    tn = shutil.which("terminal-notifier")
    try:
        if tn:
            subprocess.run([tn, "-title", title, "-message", message,
                            "-execute", f"open -a Console \"{log_path.resolve()}\""],
                           timeout=15, check=False)
        else:
            subprocess.run(["osascript", "-e",
                            f'display notification "{message}" with title "{title}"'],
                           timeout=15, check=False)
    except Exception:
        pass


def main():
    expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB)
    daily_max = conn.execute("SELECT MAX(date) FROM daily_snapshot").fetchone()[0] or ""
    sector_max = conn.execute("SELECT MAX(date) FROM sector_snapshot").fetchone()[0] or ""
    conn.close()

    ok_daily = daily_max >= expected
    ok_sector = sector_max >= expected
    ts = datetime.now().isoformat(timespec="seconds")

    if ok_daily and ok_sector:
        msg = f"周五 {expected} 数据已自动入库（daily={daily_max} sector={sector_max}）"
        print(f"[{ts}] PASS {msg}")
    else:
        msg = (f"周五 {expected} 数据未入库！daily MAX={daily_max or '空'} "
               f"sector MAX={sector_max or '空'}，周六采集可能失败")
        print(f"[{ts}] FAIL {msg}")
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {'PASS' if ok_daily and ok_sector else 'FAIL'} {msg}\n")
    _notify("美股周五数据检查" + ("✅" if ok_daily and ok_sector else "⚠️"),
            msg, LOG)
    sys.exit(0 if ok_daily and ok_sector else 1)


if __name__ == "__main__":
    main()
