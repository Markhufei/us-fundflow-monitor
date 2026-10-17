"""pipeline._run_index 指数扫描流程测试（mock 采集与通知，真实 tmp DB）。

镜像 test_pipeline_sector_mock.py 策略: monkeypatch collector 模块属性 +
tmp_path SQLite + 动态日期种子 + delenv TWELVEDATA_API_KEY 保离线。

对齐的实现语义（与 lead 契约的偏差点已上报 team-lead）:
  - index_watch 为扁平 list（{symbol, name, flow_proxy, rs_framework, leveraged_*}）
  - ok=False 仅当 SPY 代理失败（镜像 sector 基准失败语义）；指数本体失败进
    skipped 继续跑——lead 契约原文"指数拉取失败→ok=False 零写入"与实现有出入，
    见 test_index_failure_contract_divergence（保留契约断言供 lead 裁决）
  - 检测需 DB 历史 >= 60 天（特征窗口门槛）-> 告警场景播种 60 行
  - 告警复用 alerts 表（rule=index_<trigger_type>），UNIQUE 幂等仅新插入通知

信号场景: 播种 d-2 dd20=-0.04 / d-1=-0.06，mock 采集今日 dd20=-0.07 ->
"连续 2 天 <= -0.05 且前日新鲜" 的 pullback_start 确认日。
"""
import sys
import os
import sqlite3
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src import pipeline
from src.storage import store

index_price = pytest.importorskip(
    "src.collectors.index_price",
    reason="src/collectors/index_price.py 未落地（红态验收，impl-index 完成后自动启用）",
)

TH = {
    "pullback_dd": -0.05,
    "pullback_lookback": 15,
    "cmf_withdrawal": -0.10,
    "rebound_min_ret": 0.015,
    "confirm_days": 2,
    "vol_confirm": 1.5,
    "sustained_amplitude": 0.02,
    "momentum_tol_abs": 0.5,
    "prob_min_sample": 10,
    "prob_high_sample": 30,
}
CONFIG = {
    "index_watch": [
        {"symbol": "GSPC", "name": "标普500", "flow_proxy": "SPY", "rs_framework": False,
         "leveraged_long": "SPXL", "leveraged_short": "SPXU"},
        {"symbol": "IXIC", "name": "纳斯达克综合", "flow_proxy": "QQQ", "rs_framework": True,
         "leveraged_long": "TQQQ", "leveraged_short": "SQQQ"},
    ],
    "index_thresholds": TH,
    "index_periods": {"rs_period": 30, "index_hist_days": 250},
}
INDEX_SYMBOLS = {"GSPC", "IXIC"}


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _flat_hist(symbol, days=30, base=500.0, volume=1_000_000):
    """平坦量价历史（proxy/杠杆 ETF；index 传 volume=None 走守卫放行路径）。"""
    rows = []
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        rows.append({"date": d, "symbol": symbol, "close": base,
                     "high": base * 1.01, "low": base * 0.99,
                     "volume": volume, "vwap": None})
    return rows


def _pullback_hist(symbol):
    """48 日合成序列: 44 日横盘 + 4 日连跌，末日 dd20 = 93/100-1 = -0.07（fresh 确认日）。"""
    closes = [100.0] * 44 + [99.0, 96.0, 94.0, 93.0]
    rows = []
    for i, c in enumerate(closes):
        d = (date.today() - timedelta(days=len(closes) - 1 - i)).isoformat()
        rows.append({"date": d, "symbol": symbol, "close": c,
                     "high": c * 1.01, "low": c * 0.99,
                     "volume": None, "vwap": None})   # 指数本体无量 -> None
    return rows


def _fake_collect(fail=()):
    """按 symbol 打桩: 失败名单返回 []；指数走合成序列（volume=None）；ETF 走平坦序列。

    同时兼容 index_price.collect_index(symbol, days=...) 与
    price_volume.collect(symbol, days=..., fallback_symbol=...)（模块属性打桩均生效）。
    """
    def fake(symbol, days=60, fallback_symbol=None, **kw):
        if symbol in fail:
            return []
        if symbol in INDEX_SYMBOLS:
            return _pullback_hist(symbol) if symbol == "IXIC" else _flat_hist(symbol, volume=None)
        return _flat_hist(symbol)
    return fake


def _stub_collectors(monkeypatch, fail=()):
    fake = _fake_collect(fail)
    monkeypatch.setattr(index_price, "collect_index", fake)
    import src.collectors.price_volume as pv
    monkeypatch.setattr(pv, "collect", fake)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)


@pytest.fixture
def notified(monkeypatch):
    calls = []
    monkeypatch.setattr("src.alerts.notify.send", lambda alert, config: calls.append(alert))
    return calls


def _unpack(res):
    """兼容旧 bool / 新 (ok, skipped) 两种返回。"""
    if isinstance(res, tuple):
        return res[0], res[1]
    return bool(res), None


def _snap_count(db):
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM index_snapshot").fetchone()[0]
    conn.close()
    return n


def _neutral_row(symbol, days_ago, dd20=-0.01, close=98.0):
    """中性 index_snapshot 行（close<ma20/ma50，cmf=0，无任何触发条件）。"""
    return {"date": _d(days_ago), "symbol": symbol, "kind": "index",
            "close": close, "volume": None, "ret_1d": 0.0,
            "dd20": dd20, "dd50": -0.01, "ma20": 101.0, "ma50": 99.0,
            "above_ma200": 0, "rs_ratio": 100.0, "rs_momentum": -0.5,
            "cmf": 0.0, "mfi": 50.0, "vol_zscore": None}


def _seed_prev_days(db, symbol="IXIC"):
    """播种 58 天中性 + d-2=-0.04 / d-1=-0.06（含今日共 61 行，过 60 天特征门槛）。"""
    for i in range(58):
        store.store_index_snapshot(_neutral_row(symbol, 58 - i + 2), db)
    store.store_index_snapshot(_neutral_row(symbol, 2, dd20=-0.04, close=95.0), db)
    store.store_index_snapshot(_neutral_row(symbol, 1, dd20=-0.06, close=94.0), db)


# ---------- 失败语义 ----------

def test_spy_proxy_failure_fatal_zero_writes(tmp_path, notified, monkeypatch):
    """SPY 代理（regime/RS 基准）拉取失败 -> ok=False，零写入，不发通知（镜像 sector 语义）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _stub_collectors(monkeypatch, fail={"SPY"})

    ok, skipped = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is False
    assert _snap_count(db) == 0
    assert notified == []


def test_index_failure_contract_divergence(tmp_path, notified, monkeypatch):
    """【契约裁决点】指数本体拉取失败 -> lead 契约要求 ok=False 零写入。

    当前实现: 指数失败仅记 skipped 继续跑（SPY 成功则 ok=True，其余标的照常入库）。
    本用例按 lead 契约原文断言；若 lead 裁决实现为准，应改断言为 ok=True +
    skipped 含指数 + 其余标的照常入库（二选一，不允许双头语义）。
    """
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _stub_collectors(monkeypatch, fail={"GSPC", "IXIC"})

    ok, skipped = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is False, (f"lead 契约: 指数拉取失败应 ok=False；实现返回 ok={ok} "
                         f"skipped={skipped}（SPY 成功时继续跑）")
    assert _snap_count(db) == 0, "契约: 失败时应零写入"
    assert notified == []


def test_partial_failure_skips_leveraged_only(tmp_path, notified, monkeypatch):
    """杠杆 ETF 单点失败 -> ok=True，skipped 含该标的；指数/代理/其余杠杆照常入库。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _stub_collectors(monkeypatch, fail={"TQQQ"})

    ok, skipped = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is True
    if skipped is not None:
        assert "TQQQ" in skipped
    assert store.load_index_history("TQQQ", days=5, db_path=db) == []
    assert store.load_index_history("SPXL", days=5, db_path=db)   # 兄弟杠杆 ETF 照常
    assert store.load_index_history("QQQ", days=5, db_path=db)    # 代理照常
    assert store.load_index_history("IXIC", days=5, db_path=db)   # 指数照常


# ---------- kind 三态与守卫 ----------

def test_leveraged_and_proxy_rows_stored_with_kind(tmp_path, notified, monkeypatch):
    """全量成功: kind=index/proxy/leveraged 三态行均入库且 kind 落库正确。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _stub_collectors(monkeypatch)

    ok, _ = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is True
    latest = {r["symbol"]: r for r in store.get_latest_index_snapshots(db)}
    assert latest["GSPC"]["kind"] == "index"
    assert latest["SPY"]["kind"] == "proxy"
    assert latest["SPXL"]["kind"] == "leveraged"
    assert latest["SQQQ"]["kind"] == "leveraged"


def test_index_volume_none_passes_partial_guard(tmp_path, notified, monkeypatch):
    """指数 volume=None（本体无量）不被盘中不完整守卫丢弃，快照正常入库。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _stub_collectors(monkeypatch)

    ok, _ = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is True
    for sym in ("GSPC", "IXIC"):
        hist = store.load_index_history(sym, days=5, db_path=db)
        assert hist, f"{sym} 快照被守卫误杀"
        assert hist[-1]["date"] == _d(0)


# ---------- 告警与通知 ----------

def test_first_run_no_alert_no_notify(tmp_path, notified, monkeypatch):
    """全量成功但无历史快照（首次运行，未过 60 天特征门槛）-> 无告警、不通知。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _stub_collectors(monkeypatch)

    ok, _ = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is True
    assert notified == []


def test_alert_notified_only_on_new_insert(tmp_path, notified, monkeypatch):
    """播种 60 天历史后今日构成 pullback_start -> 首次告警通知；重跑同日不重复通知。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_prev_days(db)
    _stub_collectors(monkeypatch)

    ok, _ = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is True
    assert notified, "今日 fresh 确认日应产生 index_pullback_start 告警并通知"
    assert any(a["rule"].startswith("index_") for a in notified)
    assert any(a["symbol"] == "IXIC" for a in notified)

    # 重跑（节假日补采/手动刷新）: 同日同规则 UNIQUE 幂等，不重复通知
    notified.clear()
    ok, _ = _unpack(pipeline._run_index(CONFIG, db))
    assert ok is True
    assert len(notified) == 0, f"重跑不应重复通知，实际 {len(notified)} 条: {notified}"


# ---------- 数据新鲜度 ----------

def test_freshness_stale_index_snapshot_notifies(tmp_path, notified):
    """index_snapshot MAX(date) 落后预期 >1 个交易日 -> data_stale 通知点名该表。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    stale_date = (date.today() - timedelta(days=10)).isoformat()
    store.store_index_snapshot({"date": stale_date, "symbol": "GSPC",
                                "kind": "index", "close": 5000.0}, db)

    pipeline._check_freshness(db, CONFIG)
    assert any("index_snapshot" in a["message"] and a["rule"] == "data_stale"
               for a in notified)


def test_freshness_empty_index_snapshot_skips(tmp_path, notified):
    """index_snapshot 空表（MAX(date)=NULL）-> 跳过不误报（防首日误报，契约显式要求）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)

    pipeline._check_freshness(db, CONFIG)
    assert not any("index_snapshot" in a["message"] for a in notified)
