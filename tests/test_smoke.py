"""冒烟测试：验证核心模块能正常运行。"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from src.analyzers import flow_indicators, anomaly_detector, compare
from src.alerts import notify
from src.schema import SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW


def test_flow_indicators_calc():
    """测试技术指标计算。"""
    # 创建测试数据
    dates = pd.date_range(end=datetime.now(), periods=30).strftime("%Y-%m-%d")
    base_close = 100.0
    df = pd.DataFrame({
        "date": dates,
        "close": base_close * (1 + np.random.normal(0, 0.02, 30)).cumprod(),
        "high": base_close * (1 + np.random.normal(0.01, 0.02, 30)).cumprod(),
        "low": base_close * (1 + np.random.normal(-0.01, 0.02, 30)).cumprod(),
        "volume": np.random.randint(500_000, 2_000_000, 30),
    })

    # 计算指标
    result = flow_indicators.calc(df)

    # 验证返回值结构
    assert isinstance(result, dict)
    assert "obv" in result
    assert "cmf" in result
    assert "mfi" in result
    assert "vwap" in result
    assert "vol_zscore" in result


def test_flow_indicators_short_history():
    """测试历史数据不足时的降级处理。"""
    dates = pd.date_range(end=datetime.now(), periods=5).strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "date": dates,
        "close": [100, 101, 102, 101, 103],
        "high": [102, 103, 104, 103, 105],
        "low": [98, 99, 100, 99, 101],
        "volume": [1000, 1100, 1200, 1100, 1300],
    })

    result = flow_indicators.calc(df, periods={"cmf_period": 20, "mfi_period": 14, "vol_avg_days": 20})

    # 即使数据不足，也应该返回结果（不报错）
    assert result["obv"] is not None


def test_anomaly_detector_detect():
    """测试异动检测。"""
    snapshot = {
        "date": "2026-07-21",
        "symbol": "MU",
        "close": 150.0,
        "volume": 5_000_000,
        "vwap": 140.0,
        "obv": 10_000_000,
        "cmf": 0.45,  # 超过阈值 0.3
        "mfi": 75.0,
        "vol_zscore": 2.5,  # 高异常
        "short_ratio": 0.35,
        "inst_change": -0.15,  # 减持超过 10%
        "insider_net": -2_000_000,  # 卖出超过 100万
    }

    thresholds = {
        "volume_spike_ratio": 1.8,
        "price_move_pct": 0.05,
        "cmf_reversal_abs": 0.3,
        "inst_reduce_pct": 0.10,
        "insider_sell_usd": 1_000_000,
        "short_ratio_high": 0.40,
        "short_ratio_delta": 0.10,
    }

    alerts = anomaly_detector.detect(snapshot, thresholds)

    # 应该检测到多个信号
    assert len(alerts) >= 3
    rules = [a["rule"] for a in alerts]
    assert "volume_spike" in rules
    assert "cmf_reversal" in rules
    assert "inst_reduce" in rules
    assert "insider_sell" in rules


def test_compare():
    """测试对比模块。"""
    today = {
        "symbol": "MU",
        "date": "2026-07-21",
        "close": 150.0,
        "volume": 5_000_000,
        "cmf": 0.4,
    }
    past = {
        "symbol": "MU",
        "date": "2026-07-14",
        "close": 100.0,
        "volume": 2_500_000,
        "cmf": 0.2,
    }

    result = compare.compare(today, past, "周对比")

    assert result["symbol"] == "MU"
    assert result["label"] == "周对比"
    assert result["price_chg_pct"] == pytest.approx(0.5)  # 50% 涨幅
    assert result["volume_chg_pct"] == pytest.approx(1.0)  # 100% 增长
    assert result["cmf_delta"] == pytest.approx(0.2)


def test_notify_write_log(tmp_path):
    """测试通知写日志（不实际发系统通知）。"""
    alert = {
        "date": "2026-07-21",
        "symbol": "MU",
        "rule": "volume_spike",
        "severity": SEVERITY_HIGH,
        "message": "成交量异常放大",
        "value": 2.5,
    }
    config = {
        "alerts": {
            "min_severity": SEVERITY_MEDIUM,
            "log_file": str(tmp_path / "test.log"),
        }
    }

    notify.send(alert, config)

    # 验证日志文件被写入
    log_file = tmp_path / "test.log"
    assert log_file.exists()
    content = log_file.read_text()
    assert "MU" in content
    assert "volume_spike" in content


def test_pipeline_import():
    """测试 pipeline 模块能正常导入。"""
    from src import pipeline
    assert pipeline is not None
    assert hasattr(pipeline, "run")
    assert hasattr(pipeline, "load_config")

