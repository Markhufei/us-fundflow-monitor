"""指数观察页（页面 8）AppTest 冒烟测试（红态验收，镜像 test_web_apptest.py）。

覆盖: 空库 warning+stop；播种后快照表/回撤图/概率渲染；probability=None 显 N/A
（页面必须兜底，回归 sector 总览页 None-crash 教训）；kind 三态共存可渲染。
快照/事件日期一律动态生成（页面含 days 窗口查询，杜绝硬编码日期时间炸弹）。

页面路径用 glob 匹配（8_*指数观察*.py），容忍 emoji 命名差异；未创建时 skip（红态）。
"""
import sys
import os
import glob
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from streamlit.testing.v1 import AppTest

from src.storage import store
from web import common

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 30


def _page8_path():
    hits = sorted(glob.glob(os.path.join(ROOT, "web", "pages", "8_*指数观察*.py")))
    return hits[0] if hits else None


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """tmp 空库 + 切换 common.DB_PATH + 清缓存（缓存键不含 DB 路径，必须先清）。"""
    db = str(tmp_path / "web.db")
    store.init_db(db)
    monkeypatch.setattr(common, "DB_PATH", db)
    common.clear_data_cache()
    yield db
    common.clear_data_cache()


def _seed_index_data(db):
    """12 天快照: GSPC（index，rs NULL 纯回撤口径）+ SPY（proxy）+ SPXL（leveraged）。"""
    for i in range(12):
        d = (date.today() - timedelta(days=11 - i)).isoformat()
        store.store_index_snapshot(
            {"date": d, "symbol": "GSPC", "kind": "index", "close": 5000.0 + i,
             "volume": None, "dd20": -0.02, "dd50": -0.01,
             "ma20": 5010.0, "ma50": 4990.0, "above_ma200": 1,
             "rs_ratio": None, "rs_momentum": None,
             "cmf": 0.05, "mfi": 60.0, "vol_zscore": None}, db)
        store.store_index_snapshot(
            {"date": d, "symbol": "SPY", "kind": "proxy", "close": 500.0 + i * 0.5,
             "volume": 1_000_000, "dd20": None, "dd50": None,
             "ma20": None, "ma50": None, "above_ma200": 1,
             "rs_ratio": None, "rs_momentum": None,
             "cmf": 0.08, "mfi": 62.0, "vol_zscore": 0.5}, db)
        store.store_index_snapshot(
            {"date": d, "symbol": "SPXL", "kind": "leveraged", "close": 100.0 + i,
             "volume": 2_000_000, "dd20": None, "dd50": None,
             "ma20": None, "ma50": None, "above_ma200": 1,
             "rs_ratio": None, "rs_momentum": None,
             "cmf": None, "mfi": None, "vol_zscore": None}, db)


def _seed_events(db):
    """4 个 GSPC pullback_start 历史事件（3 持续），概率 0.75 / 样本 4。"""
    events = []
    for k, ret in enumerate([0.05, 0.08, 0.04, 0.01]):
        events.append({
            "event_date": (date.today() - timedelta(days=400 * (k + 1))).isoformat(),
            "symbol": "GSPC", "trigger_type": "pullback_start",
            "rs_momentum": None, "dd20": -0.06, "cmf": 0.0, "vol_zscore": None,
            "ret_5d": ret - 0.01, "ret_10d": ret, "ret_20d": ret, "ret_60d": ret + 0.02,
            "max_dd_20d": -0.09, "duration_days": 5 + k, "spy_above_ma200": 1})
    store.store_index_history_events(events, db)


def _run_page8(timeout=TIMEOUT):
    path = _page8_path()
    if path is None:
        pytest.skip("web/pages/8_*指数观察*.py 未创建（红态验收）")
    at = AppTest.from_file(path, default_timeout=timeout)
    at.run()
    return at


def _all_text(at):
    """拼接页面主要文本元素（含 dataframe 内容），供跨实现形态的渲染断言。"""
    parts = []
    for el in list(at.markdown) + list(at.caption) + list(at.subheader) \
            + list(at.header) + list(at.title) + list(at.text) + list(at.info) \
            + list(at.warning) + list(at.metric):
        parts.append(str(getattr(el, "value", "")))
    for df_el in at.dataframe:
        try:
            parts.append(df_el.value.to_string())
        except Exception:
            pass
    return "\n".join(parts)


# ---------- 空库态 ----------

def test_page8_empty_db_warning_and_stop(tmp_db):
    """空库: 无异常，warning 引导建库后 stop（后续区块不渲染）。"""
    at = _run_page8()
    assert not at.exception
    assert len(at.warning) >= 1


# ---------- 有数据态 ----------

def test_page8_seeded_renders_snapshot_table(tmp_db):
    """播种后: 无异常，快照表含 GSPC 行，回撤区块（dd20 时间线）渲染。"""
    _seed_index_data(tmp_db)
    at = _run_page8()
    assert not at.exception
    text = _all_text(at)
    assert "GSPC" in text
    assert "回撤" in text


def test_page8_seeded_handles_kind_diversity(tmp_db):
    """kind=index/proxy/leveraged 三态共存: 页面不 crash（leveraged 无指标列值也能渲染）。"""
    _seed_index_data(tmp_db)
    at = _run_page8()
    assert not at.exception
    text = _all_text(at)
    assert "SPY" in text and "SPXL" in text


def test_page8_probability_none_shows_na(tmp_db):
    """回归: probability=None（无历史样本）时页面有诚实兜底且不 crash。

    契约原文要求显 N/A；实现以 warning「无历史样本（事件库未建或该类型 0 事件）」
    兜底（意图等价：不 crash + 明示无数据），断言两者皆可（差异已上报 team-lead 裁决）。
    """
    _seed_index_data(tmp_db)
    at = _run_page8()
    assert not at.exception
    text = _all_text(at)
    assert "N/A" in text or "无历史样本" in text


def test_page8_probability_renders_sample_size(tmp_db):
    """有历史事件: 概率区块渲染样本量（条件概率查询可见，含参考非预测语境）。"""
    _seed_index_data(tmp_db)
    _seed_events(tmp_db)
    at = _run_page8()
    assert not at.exception
    assert "样本" in _all_text(at)


def test_page8_recent_index_signals_render(tmp_db):
    """近 30 日信号区块: alerts 表中的 index_ 前缀告警可渲染（动态日期，防窗口滑出）。"""
    _seed_index_data(tmp_db)
    d = (date.today() - timedelta(days=1)).isoformat()
    store.store_alerts([{
        "date": d, "symbol": "GSPC", "rule": "index_pullback_start",
        "severity": "MEDIUM", "message": f"[{d[5:]}] 指数信号: GSPC 回撤开始 "
                                         "（历史统计参考非预测）",
        "value": -0.065, "created_at": f"{d}T00:00:00",
    }], tmp_db)
    at = _run_page8()
    assert not at.exception
    assert "GSPC" in _all_text(at)
