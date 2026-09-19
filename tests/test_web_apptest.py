"""Web 页面 AppTest 冒烟测试（空库 + 有数据两态，全 mock-free、tmp DB 隔离）。

重点回归:
- 总览页对 probability=None 轮动事件不再 crash（显 "N/A"）
- RRG 页对 RS 点不足的标的输出 dropped 提示（不再静默消失）
- 三个数据页空库态可渲染（warning/info 引导，无异常）
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from streamlit.testing.v1 import AppTest

from src.storage import store
from web import common

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "web", "app.py")
PAGE1 = os.path.join(ROOT, "web", "pages", "1_🔄_RRG轮动.py")
PAGE2 = os.path.join(ROOT, "web", "pages", "2_📡_轮动信号.py")
PAGE4 = os.path.join(ROOT, "web", "pages", "4_🔍_版块详情.py")
PAGE7 = os.path.join(ROOT, "web", "pages", "7_🎯_个股监控.py")
TIMEOUT = 30


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """tmp 空库 + 切换 common.DB_PATH + 清缓存（缓存键不含 DB 路径，必须先清）。"""
    db = str(tmp_path / "web.db")
    store.init_db(db)
    monkeypatch.setattr(common, "DB_PATH", db)
    common.clear_data_cache()
    yield db
    common.clear_data_cache()


def _seed_sector_data(db):
    """12 天快照: SPY 基准 + XLK（RS 有效）+ UFO（RS 缺失→RRG 图应 dropped）。"""
    for i in range(12):
        d = f"2026-08-{i + 1:02d}"
        store.store_sector_snapshot(
            {"date": d, "symbol": "SPY", "close": 500.0 + i}, db)
        store.store_sector_snapshot(
            {"date": d, "symbol": "XLK", "sector": "科技", "close": 200.0 + i,
             "rs_ratio": 101.0 + i * 0.1, "rs_momentum": 0.5 + i * 0.1,
             "mfi": 60.0, "cmf": 0.1, "vol_zscore": 1.0}, db)
        store.store_sector_snapshot(
            {"date": d, "symbol": "UFO", "sector": "太空", "close": 20.0 + i,
             "rs_ratio": None, "rs_momentum": None}, db)


def _seed_none_probability_event(db):
    """probability/avg_duration/avg_amplitude 全 None 的轮动事件（无历史相似样本场景）。"""
    # 动态日期: 硬编码日期会滑出信号页 days=30 查询窗口（时间炸弹）
    event_date = (date.today() - timedelta(days=1)).isoformat()
    store.store_rotation_events([{
        "date": event_date, "from_sector": "科技", "to_sector": "太空",
        "to_symbol": "UFO", "probability": None, "avg_duration": None,
        "avg_amplitude": None, "sample_size": 0, "confidence": "低",
        "severity": "MEDIUM", "message": "[08-12] 版块轮动: 科技->太空 持续概率N/A",
        "created_at": "2026-09-01T00:00:00",
    }], db)


def _seed_stock_data(db):
    """个股: MU 12 天 RS 有效 + SKHY 无数据（个股 RRG 应 dropped）。"""
    for i in range(12):
        d = f"2026-08-{i + 1:02d}"
        store.store_stock_snapshot(
            {"date": d, "symbol": "MU", "name": "美光科技", "close": 100.0 + i,
             "rs_ratio": 102.0 + i * 0.1, "rs_momentum": 1.0,
             "mfi": 55.0, "cmf": 0.05, "vol_zscore": 0.5}, db)


# ---------- 空库态 ----------

def test_app_empty_db_renders_guidance(tmp_db):
    """总览页空库：无异常，显示引导 warning。"""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("暂无 RS 历史数据" in w.value for w in at.warning)


def test_page2_empty_db_renders_guidance(tmp_db):
    """轮动信号页空库：无异常，显示无信号提示。"""
    at = AppTest.from_file(PAGE2, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("无轮动信号" in i.value for i in at.info)


def test_page4_empty_db_renders_guidance(tmp_db):
    """版块详情页空库：无异常，显示无快照提示。"""
    at = AppTest.from_file(PAGE4, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("无 XLK 历史快照" in i.value for i in at.info)


# ---------- 有数据态 ----------

def test_app_none_probability_event_no_crash(tmp_db):
    """回归: probability=None 轮动事件渲染为 N/A，总览页不 crash（原 TypeError）。"""
    _seed_sector_data(tmp_db)
    _seed_none_probability_event(tmp_db)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("N/A" in md.value for md in at.markdown)


def test_app_seeded_renders_metrics_and_dropped(tmp_db):
    """总览页有数据：指标卡 + 轨迹图渲染，UFO（RS 缺失）出现在 dropped 提示。"""
    _seed_sector_data(tmp_db)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("未显示" in c.value and "UFO" in c.value for c in at.caption)


def test_page1_seeded_shows_dropped_symbol(tmp_db):
    """RRG 页有数据：跑通全页（无异常）+ UFO dropped 提示（不再静默消失）。"""
    _seed_sector_data(tmp_db)
    at = AppTest.from_file(PAGE1, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("UFO" in c.value for c in at.caption)
    assert any("读图" in c.value for c in at.caption)  # 页面渲染到轨迹图之后的说明区


def test_page2_none_probability_shows_na(tmp_db):
    """轮动信号页: None 概率显 N/A（表列与详情标题），不 crash。"""
    _seed_none_probability_event(tmp_db)
    at = AppTest.from_file(PAGE2, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("概率%" in df.columns and (df["概率%"] == "N/A").any()
               for df in (d.value for d in at.dataframe))
    assert any("N/A" in e.label for e in at.expander)


def test_page4_seeded_renders_charts(tmp_db):
    """版块详情页有数据：跑通全页（无异常）+ 数据截至 caption。"""
    _seed_sector_data(tmp_db)
    at = AppTest.from_file(PAGE4, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("数据截至" in c.value for c in at.caption)


def test_page7_seeded_shows_dropped_stock(tmp_db):
    """个股监控页: MU 有数据渲染，SKHY（无数据）出现在 dropped 提示。"""
    _seed_sector_data(tmp_db)
    _seed_stock_data(tmp_db)
    at = AppTest.from_file(PAGE7, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    assert any("SKHY" in c.value for c in at.caption)
    assert any("截至" in sh.value for sh in at.subheader)
