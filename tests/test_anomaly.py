"""异动检测器测试：验证 alert 含交易时间(MM-DD) + 交易详细。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analyzers import anomaly_detector


def test_volume_spike_with_detail():
    """volume_spike alert 含日期 + 当日量/均值/倍数明细。"""
    snapshot = {
        "date": "2026-07-24", "symbol": "SOXX",
        "vol_zscore": 2.5, "volume": 18701163, "vol_mean": 6500000,
    }
    alerts = anomaly_detector.detect(snapshot, {})
    assert len(alerts) == 1
    a = alerts[0]
    assert a["rule"] == "volume_spike"
    assert a["severity"] == "HIGH"
    assert "[07-24]" in a["message"]
    assert "Z=2.50" in a["message"]
    assert a["detail"] is not None
    assert "18,701,163" in a["detail"]
    assert "6,500,000" in a["detail"]
    assert "2.9x" in a["detail"]  # 18701163/6500000=2.877


def test_insider_sell_with_detail():
    """insider_sell alert 含日期+笔数 + 逐笔明细。"""
    snapshot = {
        "date": "2026-07-24", "symbol": "MU",
        "insider_net": -92084930,
        "insider_transactions": [
            {"date": "2026-07-20", "code": "D", "tcode": "S", "shares": 50000, "price": 865, "amount": 43250000, "insider": "MEHROTRA SANJAY", "title": "CEO"},
            {"date": "2026-07-21", "code": "D", "tcode": "S", "shares": 30000, "price": 970, "amount": 29100000, "insider": "ARNZEN APRIL", "title": "EVP"},
        ],
    }
    alerts = anomaly_detector.detect(snapshot, {})
    assert len(alerts) == 1
    a = alerts[0]
    assert a["rule"] == "insider_sell"
    assert "[07-24]" in a["message"]
    assert "$92,084,930" in a["message"]
    assert "(2笔" in a["message"]  # (2笔, top ...)
    assert "MEHROTRA" in a["message"]  # top seller 写入 message
    assert a["detail"] is not None
    assert "07-20" in a["detail"]
    assert "07-21" in a["detail"]
    assert "卖" in a["detail"]
    assert "50,000股" in a["detail"]
    assert "@$865.00" in a["detail"]
    assert "MEHROTRA" in a["detail"]  # detail 分组头含内部人姓名
    assert "CEO" in a["detail"]  # detail 含职位


def test_price_move_with_close_vwap():
    """price_move alert 含收盘价 + VWAP。"""
    snapshot = {
        "date": "2026-07-24", "symbol": "MU",
        "close": 900.0, "vwap": 1000.0,  # 偏离 -10% > 5% 阈值
    }
    alerts = anomaly_detector.detect(snapshot, {})
    assert len(alerts) == 1
    a = alerts[0]
    assert a["rule"] == "price_move"
    assert "[07-24]" in a["message"]
    assert "$900.00" in a["message"]
    assert "$1,000.00" in a["message"]


def test_no_anomaly():
    """无异动时返回空列表。"""
    snapshot = {"date": "2026-07-24", "symbol": "MU",
                "vol_zscore": 0.5, "close": 100, "vwap": 100}
    alerts = anomaly_detector.detect(snapshot, {})
    assert alerts == []


def test_volume_spike_no_mean_no_detail():
    """vol_mean 缺失时 detail 为 None（不报错，降级）。"""
    snapshot = {"date": "2026-07-24", "symbol": "SOXX",
                "vol_zscore": 2.5, "volume": 18701163}
    alerts = anomaly_detector.detect(snapshot, {})
    assert len(alerts) == 1
    assert alerts[0]["detail"] is None


if __name__ == "__main__":
    print("运行异动检测器测试...")
    test_volume_spike_with_detail()
    print("✓ test_volume_spike_with_detail")
    test_insider_sell_with_detail()
    print("✓ test_insider_sell_with_detail")
    test_price_move_with_close_vwap()
    print("✓ test_price_move_with_close_vwap")
    test_no_anomaly()
    print("✓ test_no_anomaly")
    test_volume_spike_no_mean_no_detail()
    print("✓ test_volume_spike_no_mean_no_detail")
    print("\n所有异动检测器测试通过！")
