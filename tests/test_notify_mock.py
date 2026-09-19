"""通知模块 fallback 分支测试（全 mock，不真发系统通知）。

覆盖: 非 Darwin 早退、terminal-notifier 缺失→osascript 降级、
terminal-notifier 存在→优先使用且不调 osascript、tn 执行异常→降级 osascript、
severity 低于门槛只写日志不发系统通知。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

from src.alerts import notify

ALERT = {"date": "2026-09-01", "symbol": "MU", "rule": "volume_spike",
         "severity": "HIGH", "message": "成交量异常放大"}


def _darwin():
    fake = MagicMock()
    fake.sysname = "Darwin"
    return fake


def test_non_darwin_returns_without_notification(tmp_path):
    """非 macOS 系统直接返回，不调用任何通知命令。"""
    linux = MagicMock()
    linux.sysname = "Linux"
    with patch("src.alerts.notify.os.uname", return_value=linux):
        with patch("src.alerts.notify.subprocess.run") as mr:
            notify._send_macos_notification(ALERT, str(tmp_path / "l.log"))
    mr.assert_not_called()


def test_no_terminal_notifier_falls_back_to_osascript(tmp_path):
    """terminal-notifier 不存在 → 降级 osascript。"""
    with patch("src.alerts.notify.os.uname", return_value=_darwin()):
        with patch("src.alerts.notify.shutil.which", return_value=None):
            with patch("src.alerts.notify.subprocess.run") as mr:
                notify._send_macos_notification(ALERT, str(tmp_path / "l.log"))
    assert mr.call_count == 1
    cmd = mr.call_args.args[0]
    assert cmd[0] == "osascript"
    assert "display notification" in cmd[2]


def test_terminal_notifier_preferred(tmp_path):
    """terminal-notifier 存在 → 使用它（带 -execute 打开日志），不调 osascript。"""
    with patch("src.alerts.notify.os.uname", return_value=_darwin()):
        with patch("src.alerts.notify.shutil.which",
                   return_value="/opt/homebrew/bin/terminal-notifier"):
            with patch("src.alerts.notify.subprocess.run") as mr:
                notify._send_macos_notification(ALERT, str(tmp_path / "l.log"))
    assert mr.call_count == 1
    cmd = mr.call_args.args[0]
    assert cmd[0].endswith("terminal-notifier")
    assert "-execute" in cmd
    assert "osascript" not in cmd


def test_terminal_notifier_failure_falls_back_to_osascript(tmp_path):
    """terminal-notifier 执行异常 → 静默降级 osascript。"""
    with patch("src.alerts.notify.os.uname", return_value=_darwin()):
        with patch("src.alerts.notify.shutil.which",
                   return_value="/opt/homebrew/bin/terminal-notifier"):
            with patch("src.alerts.notify.subprocess.run",
                       side_effect=[Exception("boom"), MagicMock()]) as mr:
                notify._send_macos_notification(ALERT, str(tmp_path / "l.log"))
    assert mr.call_count == 2
    assert mr.call_args_list[1].args[0][0] == "osascript"


def test_low_severity_writes_log_only(tmp_path):
    """severity 低于门槛：只写日志文件，不发系统通知。"""
    log_file = tmp_path / "a.log"
    with patch("src.alerts.notify._send_macos_notification") as ms:
        notify.send({**ALERT, "severity": "LOW"},
                    {"alerts": {"min_severity": "MEDIUM", "log_file": str(log_file)}})
    ms.assert_not_called()
    assert log_file.exists()
    assert "MU" in log_file.read_text()


def test_high_severity_triggers_macos_notification(tmp_path):
    """severity 达到门槛：写日志 + 触发系统通知路径。"""
    log_file = tmp_path / "a.log"
    with patch("src.alerts.notify._send_macos_notification") as ms:
        notify.send({**ALERT, "severity": "HIGH"},
                    {"alerts": {"min_severity": "MEDIUM", "log_file": str(log_file)}})
    ms.assert_called_once()
    assert log_file.exists()
