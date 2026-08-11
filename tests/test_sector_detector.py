"""版块轮动检测器测试（Phase 4）。

用合成快照 + mock 历史事件库验证:
  - 转强/转弱识别（momentum_cross / rs_cross）
  - 配对逻辑（from 取最弱）
  - 概率查询 + 严重度分级
  - message 格式 + 低置信度标注
  - 无信号时返回空
"""
import pytest

from src.analyzers import sector_detector
from src.storage import store


def _snap(symbol, sector, rs_ratio, rs_momentum, vol_zscore=None, date="2026-08-10"):
    return {"symbol": symbol, "sector": sector, "rs_ratio": rs_ratio,
            "rs_momentum": rs_momentum, "vol_zscore": vol_zscore, "date": date}


def test_detect_rotation_momentum_cross_up(tmp_path):
    """RS-Momentum 上穿 0 + 量能确认 -> 转强版块触发轮动。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    # 前一日: XLE 弱（mom 负），XLK 强
    prev = [_snap("XLE", "能源", rs_ratio=98, rs_momentum=-1.0, vol_zscore=0.5, date="2026-08-09"),
            _snap("XLK", "科技", rs_ratio=102, rs_momentum=1.0, vol_zscore=0.5, date="2026-08-09")]
    # 当日: XLE 转强（mom 上穿 0 + 量能确认），XLK 转弱（mom 下穿 0）
    cur = [_snap("XLE", "能源", rs_ratio=99, rs_momentum=1.2, vol_zscore=1.8, date="2026-08-10"),
           _snap("XLK", "科技", rs_ratio=101, rs_momentum=-0.5, vol_zscore=0.5, date="2026-08-10")]

    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert len(alerts) == 1
    assert alerts[0]["to_sector"] == "能源"
    assert alerts[0]["from_sector"] == "科技"  # 最弱（mom=-0.5）
    assert alerts[0]["to_symbol"] == "XLE"


def test_detect_rotation_rs_cross_up(tmp_path):
    """RS-Ratio 上穿 100 + 动量正 -> 转强（无量能确认也触发 rs_cross）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    prev = [_snap("XLF", "金融", rs_ratio=99, rs_momentum=0.5, vol_zscore=0.5, date="2026-08-09")]
    cur = [_snap("XLF", "金融", rs_ratio=101, rs_momentum=0.8, vol_zscore=0.5, date="2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert len(alerts) == 1
    assert alerts[0]["to_sector"] == "金融"


def test_detect_rotation_no_signal(tmp_path):
    """无明显转强信号时返回空。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    prev = [_snap("XLK", "科技", 100, 1.0, 0.5, "2026-08-09")]
    cur = [_snap("XLK", "科技", 100, 1.0, 0.5, "2026-08-10")]  # 无变化
    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert alerts == []


def test_detect_rotation_no_volume_confirm_no_momentum_event(tmp_path):
    """mom 上穿 0 但量能不足 -> 不算 momentum_cross（但可能 rs_cross）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    prev = [_snap("XLE", "能源", 99, -1.0, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 0.5, 1.0, "2026-08-10")]  # mom 上穿0 但 vz=1.0<1.5
    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    # mom 上穿0 但量能不足 -> momentum_cross 不触发
    # rs_ratio 99->99 未上穿100 -> rs_cross 不触发
    assert alerts == []


def test_detect_rotation_with_history_probability(tmp_path):
    """有历史事件库时，告警含概率 + 持续天数。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    # 插入 4 个相似历史事件，3 个持续
    store.store_history_events([
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.2,
         "duration_days": 15, "ret_10d": 0.05},
        {"event_date": "2020-03-01", "symbol": "XLE", "rs_momentum": 1.3,
         "duration_days": 20, "ret_10d": 0.08},
        {"event_date": "2020-06-01", "symbol": "XLE", "rs_momentum": 1.1,
         "duration_days": 12, "ret_10d": 0.04},
        {"event_date": "2020-09-01", "symbol": "XLE", "rs_momentum": 1.2,
         "duration_days": 3, "ret_10d": 0.01},
    ], db)
    prev = [_snap("XLE", "能源", 98, -1.0, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 102, 1.0, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert len(alerts) == 1
    a = alerts[0]
    assert a["sample_size"] == 4
    assert a["probability"] == pytest.approx(0.75, rel=1e-4)
    assert a["confidence"] == "低"  # 4 < 10
    assert "样本4" in a["message"]
    assert "⚠️低置信度" in a["message"]


def test_detect_rotation_severity_high_when_strong_signal(tmp_path):
    """有 from + 概率 > 0.6 -> HIGH 严重度。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    # 造 30 个事件，全部持续 -> prob=1.0
    store.store_history_events([
        {"event_date": f"2020-01-{i+1:02d}", "symbol": "XLE", "rs_momentum": 1.2,
         "duration_days": 15, "ret_10d": 0.05}
        for i in range(30)
    ], db)
    prev = [_snap("XLE", "能源", 98, -1.0, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 102, 1.0, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert alerts[0]["severity"] == "HIGH"
    assert alerts[0]["confidence"] == "高"  # 30 >= 30


def test_detect_rotation_message_format(tmp_path):
    """message 含 [MM-DD] + from->to + 概率 + 持续 + 样本。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    store.store_history_events([
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.2,
         "duration_days": 18, "ret_10d": 0.082},
    ], db)
    prev = [_snap("XLE", "能源", 98, -1.0, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 102, 1.0, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    msg = alerts[0]["message"]
    assert "[08-10]" in msg
    assert "科技->能源" in msg
    assert "持续概率" in msg
    assert "样本1" in msg


def test_detect_rotation_empty_snapshots(tmp_path):
    """空快照返回空。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    alerts = sector_detector.detect_rotation([], [], db, thresholds={})
    assert alerts == []


def test_detect_rotation_missing_prev_skips(tmp_path):
    """某版块无前一日快照时跳过（不报错）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, [], db, thresholds={
        "vol_confirm_zscore": 1.5, "sustained_days": 10, "sustained_amplitude": 0.03,
        "prob_min_sample": 10, "prob_high_sample": 30,
    })
    assert alerts == []  # 无 prev 无法判断上穿
