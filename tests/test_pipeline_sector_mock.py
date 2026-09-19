"""pipeline._run_sector 版块扫描流程测试（mock 采集与通知，真实 tmp DB）。

覆盖: SPY 失败→False、部分版块失败时成功版块照常入库、个股照常入库、
首次运行无前日快照→无告警不通知。
签名兼容: _run_sector 旧返回 bool / 新返回 (ok, skipped)（review-data 落地后自动覆盖断言）。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src import pipeline
from src.storage import store

CONFIG = {
    "sector_etfs": {
        "benchmark": "SPY",
        "etfs": [{"symbol": "XLE", "sector": "能源"},
                 {"symbol": "XLK", "sector": "科技"}],
    },
    "sector_stocks": [{"symbol": "MU", "name": "美光科技", "sector_symbol": "XLK"}],
    "sector_thresholds": {},
    "sector_periods": {"sector_hist_days": 15},
}


def _hist(symbol, days=15, base=100.0):
    """构造升序 OHLCV 历史（15 天，够 rs_ratio_period=10 出 RS 值）。"""
    return [{"date": f"2026-08-{d + 1:02d}", "symbol": symbol,
             "close": base + d, "high": base + d + 1.0, "low": base + d - 1.0,
             "volume": 1000 + d} for d in range(days)]


def _unpack(res):
    """兼容旧 bool / 新 (ok, skipped) 两种返回。"""
    if isinstance(res, tuple):
        return res[0], res[1]
    return bool(res), None


def _set_collect(monkeypatch, fn):
    """打桩 price_volume.collect（pipeline 内部延迟导入，模块属性打桩生效）。"""
    import src.collectors.price_volume as pv
    monkeypatch.setattr(pv, "collect", fn)


@pytest.fixture
def notified(monkeypatch):
    """mock notify.send，返回调用记录（pipeline 延迟导入 notify 模块属性）。"""
    calls = []
    monkeypatch.setattr("src.alerts.notify.send", lambda alert, config: calls.append(alert))
    return calls


def test_spy_failure_returns_false_and_stores_nothing(tmp_path, notified, monkeypatch):
    """SPY 拉不到数据 → 扫描失败（False），不存任何快照、不发通知。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _set_collect(monkeypatch, lambda symbol, days=60, fallback_symbol=None: [])

    res = pipeline._run_sector(CONFIG, db)
    ok, skipped = _unpack(res)
    assert ok is False
    assert store.load_sector_history("SPY", days=5, db_path=db) == []
    assert store.load_sector_history("XLE", days=5, db_path=db) == []
    assert notified == []


def test_partial_failure_stores_succeeded_symbols(tmp_path, notified, monkeypatch):
    """XLE 失败被跳过，SPY/XLK/MU 照常入库；新签名下 skipped 含 XLE。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)

    def fake_collect(symbol, days=60, fallback_symbol=None):
        if symbol == "XLE":
            return []  # 限流/无数据 → 跳过
        return _hist(symbol, days=15)

    _set_collect(monkeypatch, fake_collect)
    res = pipeline._run_sector(CONFIG, db)
    ok, skipped = _unpack(res)
    assert ok is True
    # 成功版块照常入库（含 RS 值）
    xlk = store.load_sector_history("XLK", days=5, db_path=db)
    assert xlk and xlk[-1]["rs_ratio"] is not None
    # 失败版块无数据
    assert store.load_sector_history("XLE", days=5, db_path=db) == []
    # 个股照常入库
    mu = store.load_stock_history("MU", days=5, db_path=db)
    assert mu and mu[-1]["rs_ratio"] is not None
    # 新签名下 skipped 报告被跳过标的
    if skipped is not None:
        assert "XLE" in skipped


def test_first_run_no_prev_snapshots_no_alert(tmp_path, notified, monkeypatch):
    """全量成功但无前日快照（首次运行）→ 不产生告警、不发通知。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _set_collect(monkeypatch, lambda symbol, days=60, fallback_symbol=None: _hist(symbol, days=15))

    res = pipeline._run_sector(CONFIG, db)
    ok, _ = _unpack(res)
    assert ok is True
    assert notified == []
