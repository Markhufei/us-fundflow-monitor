"""Twelve Data 采集器单元测试（mock requests，不依赖真实 API key）。

验证解析逻辑：字段映射、升序、volume 字符串转换、VWAP 典型价、降级。
真实 API 待 TWELVEDATA_API_KEY 配置后端到端验证（demo 已确认 AAPL 可用）。
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.collectors import twelvedata


def test_has_key_false_without_env():
    """无 TWELVEDATA_API_KEY 时 has_key 返回 False。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": ""}):
        assert twelvedata.has_key() is False


def test_collect_no_key():
    """无 key 时返回空列表。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": ""}):
        data = twelvedata.collect_price_volume("MU", days=5)
        assert data == []


def test_collect_parse():
    """mock Twelve Data 返回，验证解析（字段、升序、volume 转换、vwap 典型价）。"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    # Twelve Data 降序（最新在前）
    mock_resp.json = MagicMock(return_value={
        "status": "ok",
        "values": [
            {"datetime": "2026-07-21", "open": "101", "high": "103", "low": "100", "close": "102", "volume": "1100"},
            {"datetime": "2026-07-20", "open": "100", "high": "102", "low": "99", "close": "101", "volume": "1000"},
        ]
    })
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "test_key"}):
        with patch("src.collectors.twelvedata.requests.get", return_value=mock_resp):
            data = twelvedata.collect_price_volume("MU", days=5)
    assert len(data) == 2
    # 升序：旧 -> 新
    assert data[0]["date"] == "2026-07-20"
    assert data[1]["date"] == "2026-07-21"
    # 字段 + volume 字符串转 int
    assert data[0]["symbol"] == "MU"
    assert data[0]["close"] == 101
    assert data[0]["high"] == 102
    assert data[0]["low"] == 99
    assert data[0]["volume"] == 1000
    # vwap 典型价 (102+99+101)/3
    assert data[0]["vwap"] == (102 + 99 + 101) / 3


def test_collect_volume_float_string():
    """volume 含小数的字符串转 int。"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "status": "ok",
        "values": [
            {"datetime": "2026-07-21", "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000.5"},
        ]
    })
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "test_key"}):
        with patch("src.collectors.twelvedata.requests.get", return_value=mock_resp):
            data = twelvedata.collect_price_volume("MU", days=5)
    assert data[0]["volume"] == 1000  # int(float("1000.5"))


def test_collect_request_failure():
    """请求异常时返回空列表（不抛异常）。"""
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "test_key"}):
        with patch("src.collectors.twelvedata.requests.get", side_effect=Exception("network error")):
            data = twelvedata.collect_price_volume("MU", days=5)
    assert data == []


def test_collect_api_error_status():
    """API 返回 error status 时返回空。"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "status": "error",
        "code": 401,
        "message": "invalid api key"
    })
    with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "test_key"}):
        with patch("src.collectors.twelvedata.requests.get", return_value=mock_resp):
            data = twelvedata.collect_price_volume("MU", days=5)
    assert data == []


if __name__ == "__main__":
    print("运行 Twelve Data 单元测试...")
    test_has_key_false_without_env()
    print("✓ has_key 无 key 返回 False")
    test_collect_no_key()
    print("✓ 无 key 返回空列表")
    test_collect_parse()
    print("✓ 量价解析（字段/升序/volume/vwap）")
    test_collect_volume_float_string()
    print("✓ volume 字符串转 int")
    test_collect_request_failure()
    print("✓ 请求异常返回空")
    test_collect_api_error_status()
    print("✓ API error status 返回空")
    print("\n所有 Twelve Data 单元测试通过！")
