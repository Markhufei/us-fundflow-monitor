"""腾讯指数源测试（2026-10-18 主源改造）。

覆盖: 列序按位映射（date,open,close,high,low,volume 非惯例序）、向后翻页聚合、
count 上限、空响应重试、持续失败降级 yfinance、data_source 落库与幂等迁移。
全 mock 无网络（monkeypatch delenv TWELVEDATA_API_KEY）。
"""
import sqlite3
from unittest.mock import patch

import pytest

from src.collectors import index_price
from src.storage import store

CONFIG_DB = ":memory:"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)


# ---------- 列序按位映射 ----------

def test_column_order_mapping():
    """腾讯原始数组列序 date,open,close,high,low,volume -> 字段按位正确（非 OHLC 惯例序）。"""
    raw = [["2026-10-15", "100.50", "102.30", "103.00", "99.80", "123456"]]
    with patch.object(index_price, "_tencent_fetch_window", return_value=raw):
        rows = index_price.fetch_tencent_index("GSPC", start="2026-10-01", end="2026-10-16")
    r = rows[0]
    assert r["date"] == "2026-10-15"
    assert r["open"] == 100.50    # 第 2 位
    assert r["close"] == 102.30   # 第 3 位（腾讯是 close 在 high/low 前）
    assert r["high"] == 103.00    # 第 4 位
    assert r["low"] == 99.80      # 第 5 位
    assert r["volume"] == 123456  # 第 6 位


def test_tencent_output_adds_symbol_and_vwap():
    """_tencent_rows_to_output 补 symbol/vwap，与 yfinance 通道同构。"""
    rows = [{"date": "2026-10-15", "open": 1.0, "close": 2.0, "high": 3.0, "low": 1.0, "volume": 10}]
    out = index_price._tencent_rows_to_output(rows, "GSPC")
    assert out[0]["symbol"] == "GSPC"
    assert out[0]["vwap"] == pytest.approx((3.0 + 1.0 + 2.0) / 3)


# ---------- 向后翻页聚合 ----------

def _batch(dates, base=100.0):
    return [[d, str(base), str(base + 1), str(base + 2), str(base - 1), "1000"] for d in dates]


def test_backward_pagination_concatenates_and_sorts():
    """两窗翻页: 聚合升序、无重复、越过 start 终止。"""
    calls = []

    def fake_window(code, start, end):
        calls.append((code, start, end))
        if end >= "2026-06-01":
            return _batch(["2026-06-02", "2026-06-01"])   # 近窗（最近 2 根）
        return _batch(["2026-05-29", "2026-05-28"])       # 前窗

    with patch.object(index_price, "_tencent_fetch_window", side_effect=fake_window), \
         patch.object(index_price.time, "sleep", lambda s: None):
        rows = index_price.fetch_tencent_index("DJI", start="2026-05-01", end="2026-06-05")

    dates = [r["date"] for r in rows]
    assert dates == ["2026-05-28", "2026-05-29", "2026-06-01", "2026-06-02"]  # 升序无重复
    # 第二窗的 end 应为第一窗最早日期的前一天
    assert calls[1][2] == "2026-05-31"


def test_pagination_dedup_overlapping_boundary():
    """翻页边界重叠日期不重复入库。"""

    def fake_window(code, start, end):
        if end >= "2026-06-01":
            return _batch(["2026-06-01", "2026-05-29"])
        return _batch(["2026-05-29"])   # 与前窗重叠

    with patch.object(index_price, "_tencent_fetch_window", side_effect=fake_window), \
         patch.object(index_price.time, "sleep", lambda s: None):
        rows = index_price.fetch_tencent_index("DJI", start="2026-05-01", end="2026-06-05")
    assert [r["date"] for r in rows] == ["2026-05-29", "2026-06-01"]


def test_unknown_symbol_skips_tencent():
    """非 TENCENT_MAP 符号（ETF 等）不走腾讯，返回空。"""
    with patch.object(index_price, "_tencent_fetch_window") as m:
        assert index_price.fetch_tencent_index("QQQ") == []
        m.assert_not_called()


# ---------- 限流重试与失败降级 ----------

def test_rate_limited_empty_then_success():
    """空体（限流，实测连发返回空字符串）-> _tencent_fetch_window 内部重试后成功。"""
    import io
    import json as _json
    from unittest.mock import MagicMock

    valid = _json.dumps({"code": 0, "data": {"us.INX": {"day": [
        ["2026-10-15", "100.0", "101.0", "102.0", "99.0", "50"]
    ]}}}).encode()
    # 第一次空体（限流），第二次正常 JSON
    resp_empty, resp_ok = MagicMock(), MagicMock()
    resp_empty.__enter__.return_value.read.return_value = b""
    resp_ok.__enter__.return_value = io.BytesIO(valid)

    with patch.object(index_price.urllib.request, "urlopen",
                      side_effect=[resp_empty, resp_ok]), \
         patch.object(index_price.time, "sleep", lambda s: None):
        rows = index_price.fetch_tencent_index("GSPC", start="2026-10-01", end="2026-10-16")
    assert [r["date"] for r in rows] == ["2026-10-15"]


def test_persistent_failure_returns_empty():
    """持续限流（_tencent_fetch_window 返回 None）-> 聚合 []，不抛异常。"""
    with patch.object(index_price, "_tencent_fetch_window", return_value=None):
        assert index_price.fetch_tencent_index("GSPC") == []


def test_collect_index_with_source_tencent_primary():
    """腾讯成功 -> source=tencent，rows 同构。"""
    with patch.object(index_price, "fetch_tencent_index",
                      return_value=[{"date": "2026-10-15", "open": 1, "close": 2,
                                     "high": 3, "low": 1, "volume": 10}]):
        rows, source = index_price.collect_index_with_source("GSPC", days=60)
    assert source == "tencent"
    assert rows and rows[0]["symbol"] == "GSPC"


def test_collect_index_falls_back_to_yfinance(monkeypatch):
    """腾讯空 -> 降级 yfinance（mock _fetch_history），source=yfinance。"""
    import pandas as pd

    monkeypatch.setattr(index_price, "fetch_tencent_index", lambda *a, **kw: [])
    hist = pd.DataFrame({
        "Close": [100.0], "High": [101.0], "Low": [99.0], "Volume": [5],
    }, index=pd.DatetimeIndex(["2026-10-15"], name="Date"))
    monkeypatch.setattr(index_price, "_fetch_history", lambda sym, period: hist)
    rows, source = index_price.collect_index_with_source("GSPC", days=60)
    assert source == "yfinance"
    assert rows[0]["close"] == 100.0


def test_collect_index_all_fail_returns_empty():
    """腾讯+yfinance 全失败 -> ([], "")，不抛异常。"""
    with patch.object(index_price, "fetch_tencent_index", lambda *a, **kw: []), \
         patch.object(index_price, "_fetch_history", lambda sym, period: None):
        rows, source = index_price.collect_index_with_source("DJI", days=60)
    assert rows == [] and source == ""


def test_full_history_with_source_tencent():
    """建库全史: 腾讯成功 -> (df, 'tencent')，列序 date/close/high/low/volume。"""
    with patch.object(index_price, "fetch_tencent_index",
                      return_value=[{"date": "2026-10-15", "open": 1, "close": 2,
                                     "high": 3, "low": 1, "volume": 10}]):
        df, source = index_price.fetch_full_history_with_source("IXIC")
    assert source == "tencent"
    assert list(df.columns) == ["date", "close", "high", "low", "volume"]


# ---------- data_source 落库与迁移 ----------

def test_store_snapshot_persists_data_source(tmp_path):
    """data_source 列写入并可读回。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    store.store_index_snapshot({"date": "2026-10-16", "symbol": "GSPC", "kind": "index",
                                "close": 7659.44, "data_source": "tencent"}, db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT data_source, close FROM index_snapshot WHERE symbol='GSPC'").fetchone()
    conn.close()
    assert row["data_source"] == "tencent"
    assert row["close"] == pytest.approx(7659.44)


def test_init_db_migration_adds_data_source(tmp_path):
    """旧库 index_snapshot 无 data_source 列 -> init_db 幂等迁移补列。"""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE index_snapshot (
        date TEXT NOT NULL, symbol TEXT NOT NULL, close REAL,
        PRIMARY KEY (date, symbol))""")
    conn.commit()
    conn.close()

    store.init_db(db)   # 迁移触发

    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(index_snapshot)")]
    assert "data_source" in cols
    store.init_db(db)   # 二次执行不报错（幂等）
