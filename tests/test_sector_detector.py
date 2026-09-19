"""版块轮动检测器测试（Phase 4，2026-09-01 方法学重做后）。

用合成快照（DB 播种，去抖需历史序列）+ 事件库验证:
  - 去抖确认: momentum_cross 需连续 N 天为正 + 新鲜交叉；历史不足不发信号
  - rs_cross: RS 上穿 100 + 动量连续 N 天为正（不要求新鲜）
  - from_sector 取当日 RS-Mom 最低的其他版块
  - severity 由 confidence + probability 联合判定（低样本高概率不误发 HIGH）
  - message 含概率 + Wilson CI + 样本量 + 低置信度标注
"""
import pytest

from src.analyzers import sector_detector
from src.storage import store

TH = {
    "vol_confirm_zscore": 1.5,
    "momentum_confirm_days": 2,
    "momentum_tol_abs": 0.5,
    "sustained_amplitude": 0.02,
    "prob_min_sample": 10,
    "prob_high_sample": 30,
}


def _snap(symbol, sector, rs_ratio, rs_momentum, vol_zscore=None, date="2026-08-10"):
    return {"symbol": symbol, "sector": sector, "rs_ratio": rs_ratio,
            "rs_momentum": rs_momentum, "vol_zscore": vol_zscore, "date": date}


def _seed_series(db, symbol, sector, rows):
    """播种该 symbol 的快照序列 rows: [(date, rs_ratio, rs_momentum, vol_zscore), ...]"""
    for date, rs, mom, vz in rows:
        store.store_sector_snapshot({
            "date": date, "symbol": symbol, "sector": sector,
            "close": 100.0, "volume": 1_000_000,
            "rs_ratio": rs, "rs_momentum": mom, "vol_zscore": vz,
        }, db)


def test_detect_rotation_momentum_cross_up(tmp_path):
    """去抖确认（连续 2 天为正 + 新鲜）+ 量能确认 -> momentum_cross 轮动告警。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-08", 98, -1.0, 0.5),
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.8),
    ])
    _seed_series(db, "XLK", "科技", [
        ("2026-08-08", 102, -0.2, 0.5),
        ("2026-08-09", 101, -0.4, 0.5),
        ("2026-08-10", 101, -0.5, 0.5),
    ])
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 101, -0.4, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]

    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["to_sector"] == "能源"
    assert a["to_symbol"] == "XLE"
    assert a["from_sector"] == "科技"   # 当日 RS-Mom 最低的其他版块（-0.5）
    assert a["trigger_type"] == "momentum_cross"


def test_detect_rotation_rs_cross_up(tmp_path):
    """RS-Ratio 上穿 100 + 动量连续 2 天为正（不要求新鲜）-> rs_cross。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLF", "金融", [
        ("2026-08-08", 99, 0.5, 0.5),
        ("2026-08-09", 99, 0.6, 0.5),
        ("2026-08-10", 101, 0.8, 0.5),
    ])
    prev = [_snap("XLF", "金融", 99, 0.6, 0.5, "2026-08-09")]
    cur = [_snap("XLF", "金融", 101, 0.8, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert len(alerts) == 1
    assert alerts[0]["to_sector"] == "金融"
    assert alerts[0]["trigger_type"] == "rs_cross"
    assert alerts[0]["from_sector"] == "其他"   # 无其他版块可配


def test_detect_rotation_stale_streak_no_refire(tmp_path):
    """长正段中段不重复告警（窗口前一日已为正 -> 非新鲜交叉）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLK", "科技", [
        ("2026-08-06", 100, 0.5, 2.0),
        ("2026-08-07", 100, 0.6, 2.0),
        ("2026-08-08", 100, 0.7, 2.0),
    ])
    prev = [_snap("XLK", "科技", 100, 0.6, 2.0, "2026-08-07")]
    cur = [_snap("XLK", "科技", 100, 0.7, 2.0, "2026-08-08")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert alerts == []


def test_detect_rotation_no_volume_confirm_no_momentum_event(tmp_path):
    """确认日量能不足 -> momentum_cross 不触发；rs 未上穿 -> 无告警。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-08", 99, -1.0, 0.5),
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.0),   # vz=1.0 < 1.5
    ])
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.0, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert alerts == []


def test_detect_rotation_insufficient_history(tmp_path):
    """历史不足 confirm_days+1 天 -> 不发信号（去抖需完整窗口）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.8),   # 仅 2 天
    ])
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert alerts == []


def test_detect_rotation_with_history_probability(tmp_path):
    """有历史事件库时，告警含概率 + Wilson CI + 低置信度标注。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-08", 98, -1.0, 0.5),
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.8),
    ])
    _seed_series(db, "XLK", "科技", [
        ("2026-08-08", 102, -0.2, 0.5),
        ("2026-08-09", 101, -0.4, 0.5),
        ("2026-08-10", 101, -0.5, 0.5),
    ])
    # 4 个同类型相似事件（1.2±0.5），3 个持续（ret_20d >= 0.02）
    store.store_history_events([
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.2,
         "trigger_type": "momentum_cross", "ret_20d": 0.05},
        {"event_date": "2020-03-01", "symbol": "XLE", "rs_momentum": 1.3,
         "trigger_type": "momentum_cross", "ret_20d": 0.08},
        {"event_date": "2020-06-01", "symbol": "XLE", "rs_momentum": 1.1,
         "trigger_type": "momentum_cross", "ret_20d": 0.04},
        {"event_date": "2020-09-01", "symbol": "XLE", "rs_momentum": 1.2,
         "trigger_type": "momentum_cross", "ret_20d": 0.01},
    ], db)
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 101, -0.4, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["sample_size"] == 4
    assert a["probability"] == pytest.approx(0.75, rel=1e-4)
    assert a["confidence"] == "低"  # 4 < 10
    assert "样本4" in a["message"]
    assert "CI95[" in a["message"]
    assert "⚠️低置信度" in a["message"]


def test_detect_rotation_severity_high_requires_high_confidence(tmp_path):
    """prob>0.6 且 高置信（>=30 样本）-> HIGH。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-08", 98, -1.0, 0.5),
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.8),
    ])
    _seed_series(db, "XLK", "科技", [
        ("2026-08-08", 102, -0.2, 0.5),
        ("2026-08-09", 101, -0.4, 0.5),
        ("2026-08-10", 101, -0.5, 0.5),
    ])
    store.store_history_events([
        {"event_date": f"2020-01-{i+1:02d}", "symbol": "XLE", "rs_momentum": 1.2,
         "trigger_type": "momentum_cross", "ret_20d": 0.05}
        for i in range(30)
    ], db)
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 101, -0.4, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert alerts[0]["severity"] == "HIGH"
    assert alerts[0]["confidence"] == "高"
    assert alerts[0]["from_sector"] == "科技"


def test_detect_rotation_low_sample_high_prob_not_severity_high(tmp_path):
    """低样本全持续（p=1.0 但置信低）-> 不误发 HIGH（旧版缺陷回归测试）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-08", 98, -1.0, 0.5),
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.8),
    ])
    _seed_series(db, "XLK", "科技", [
        ("2026-08-08", 102, -0.2, 0.5),
        ("2026-08-09", 101, -0.4, 0.5),
        ("2026-08-10", 101, -0.5, 0.5),
    ])
    store.store_history_events([
        {"event_date": f"2020-01-{i+1:02d}", "symbol": "XLE", "rs_momentum": 1.2,
         "trigger_type": "momentum_cross", "ret_20d": 0.05}
        for i in range(3)
    ], db)
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    assert alerts[0]["probability"] == 1.0
    assert alerts[0]["confidence"] == "低"
    assert alerts[0]["severity"] == "MEDIUM"


def test_detect_rotation_message_format(tmp_path):
    """message 含 [MM-DD] + from->to + 概率 + CI + 持续 + 样本。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    _seed_series(db, "XLE", "能源", [
        ("2026-08-08", 98, -1.0, 0.5),
        ("2026-08-09", 99, 0.8, 0.5),
        ("2026-08-10", 99, 1.2, 1.8),
    ])
    _seed_series(db, "XLK", "科技", [
        ("2026-08-08", 102, -0.2, 0.5),
        ("2026-08-09", 101, -0.4, 0.5),
        ("2026-08-10", 101, -0.5, 0.5),
    ])
    store.store_history_events([
        {"event_date": "2020-01-01", "symbol": "XLE", "rs_momentum": 1.2,
         "trigger_type": "momentum_cross", "ret_20d": 0.082},
    ], db)
    prev = [_snap("XLE", "能源", 99, 0.8, 0.5, "2026-08-09"),
            _snap("XLK", "科技", 101, -0.4, 0.5, "2026-08-09")]
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10"),
           _snap("XLK", "科技", 101, -0.5, 0.5, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, prev, db, TH)
    msg = alerts[0]["message"]
    assert "[08-10]" in msg
    assert "科技->能源" in msg
    assert "持续概率100%" in msg
    assert "CI95[" in msg
    assert "样本1" in msg
    assert alerts[0]["avg_amplitude"] == pytest.approx(8.2, rel=1e-3)


def test_detect_rotation_empty_snapshots(tmp_path):
    """空快照返回空。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    alerts = sector_detector.detect_rotation([], [], db, TH)
    assert alerts == []


def test_detect_rotation_missing_prev_skips(tmp_path):
    """无历史序列（仅当日快照）时跳过（不报错）。"""
    db = str(tmp_path / "t.db")
    store.init_db(db)
    cur = [_snap("XLE", "能源", 99, 1.2, 1.8, "2026-08-10")]
    alerts = sector_detector.detect_rotation(cur, [], db, TH)
    assert alerts == []  # 历史不足无法去抖确认
