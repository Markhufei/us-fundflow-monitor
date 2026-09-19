"""price_volume 采集链路单元测试（全 mock，不依赖网络）。

覆盖降级链与边界: TwelveData 失败→yfinance 降级、TwelveData 成功不再调 yfinance、
YFRateLimit 重试 3 次（间隔 sleep 不真执行）、非限流异常立即返回空、
fallback_symbol 稀疏补位（yfinance 与 twelvedata 两条路径）。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import pandas as pd

from src.collectors import price_volume, twelvedata


def _df(rows=3):
    """构造 yfinance history() 形状的 DataFrame（High/Low/Close/Volume，日期索引）。"""
    idx = pd.date_range("2026-08-01", periods=rows, freq="D")
    return pd.DataFrame(
        {"High": [101.0] * rows, "Low": [99.0] * rows,
         "Close": [100.0] * rows, "Volume": [1000] * rows},
        index=idx,
    )


class FakeYFRateLimitError(Exception):
    """异常类名含 "RateLimit"，触发 price_volume 的重试分支。"""


def test_twelvedata_failure_falls_back_to_yfinance():
    """Twelve Data 返回空（限流/失败）时降级 yfinance 拿到数据。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "k"}):
        with patch("src.collectors.twelvedata.collect_price_volume", return_value=[]):
            with patch("src.collectors.price_volume.yf.Ticker") as mt:
                mt.return_value.history.return_value = _df(rows=3)
                data = price_volume.collect("MU", days=5)
    assert len(data) == 3
    assert data[-1]["close"] == 100.0
    assert data[-1]["symbol"] == "MU"


def test_twelvedata_success_skips_yfinance():
    """Twelve Data 成功返回时不调用 yfinance。"""
    td_data = [{"date": "2026-08-01", "symbol": "MU", "close": 101.0,
                "high": 102.0, "low": 100.0, "volume": 100, "vwap": 101.0}]
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "k"}):
        with patch("src.collectors.twelvedata.collect_price_volume", return_value=td_data):
            with patch("src.collectors.price_volume.yf.Ticker") as mt:
                data = price_volume.collect("MU", days=5)
    mt.assert_not_called()
    assert data[0]["close"] == 101.0


def test_rate_limit_retries_three_times_with_sleep():
    """yfinance RateLimit 异常重试 3 次、间隔 sleep（mock 不真等 30s），最终返回空。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": ""}):
        with patch("src.collectors.price_volume.yf.Ticker") as mt:
            mt.return_value.history.side_effect = FakeYFRateLimitError("rate limited")
            with patch("src.collectors.price_volume.time.sleep") as ms:
                data = price_volume.collect("MU", days=5)
    assert mt.return_value.history.call_count == 3  # 3 次尝试
    assert ms.call_count == 2  # 前两次重试前各 sleep(30)，第 3 次失败直接返回
    assert data == []


def test_non_ratelimit_exception_no_retry():
    """非限流异常不重试，立即返回空。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": ""}):
        with patch("src.collectors.price_volume.yf.Ticker") as mt:
            mt.return_value.history.side_effect = ValueError("boom")
            with patch("src.collectors.price_volume.time.sleep"):
                data = price_volume.collect("MU", days=5)
    assert mt.return_value.history.call_count == 1
    assert data == []


def test_fallback_symbol_used_when_sparse():
    """主标的行数 < days/2 时拉 fallback 标的补位，symbol 改写为主标的。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": ""}):
        with patch("src.collectors.price_volume.yf.Ticker") as mt:
            # 第 1 次调用（SKHY）只回 1 行，第 2 次调用（MU fallback）回 8 行
            mt.return_value.history.side_effect = [_df(rows=1), _df(rows=8)]
            with patch("src.collectors.price_volume.time.sleep"):
                data = price_volume.collect("SKHY", days=10, fallback_symbol="MU")
    assert len(data) == 8
    assert all(d["symbol"] == "SKHY" for d in data)  # 补位数据仍归属主标的
    assert mt.return_value.history.call_count == 2


def test_twelvedata_fallback_symbol_used_when_sparse():
    """Twelve Data 路径：主标的稀疏时拉 fallback 补位并改写 symbol。"""
    one_row = {"status": "ok", "values": [
        {"datetime": "2026-08-01", "open": "100", "high": "101", "low": "99",
         "close": "100", "volume": "1000"}]}
    two_rows = {"status": "ok", "values": [
        {"datetime": "2026-08-01", "open": "100", "high": "101", "low": "99",
         "close": "100", "volume": "1000"},
        {"datetime": "2026-08-02", "open": "100", "high": "101", "low": "99",
         "close": "100", "volume": "1000"}]}
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "k"}):
        with patch("src.collectors.twelvedata._get", side_effect=[one_row, two_rows]):
            data = twelvedata.collect_price_volume("SKHY", days=10, fallback_symbol="MU")
    assert len(data) == 2
    assert all(d["symbol"] == "SKHY" for d in data)
