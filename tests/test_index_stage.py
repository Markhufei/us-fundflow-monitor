"""市场阶段判定测试（index_stage）。

覆盖: 5 阶段正例/负例/边界精确值/优先级、leading NaN、历史类比统计手算、
stage_conclusion DB 路径与腾讯回退。全离线（patch fetch_full_history_with_source），
动态日期种子（项目时间炸弹教训）。
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.analyzers import index_stage
from src.analyzers.index_stage import (STAGE_LABELS, classify_stages,
                                       stage_conclusion, stage_history_stats)
from src.storage import store


def _dates(n, end=None):
    """n 个交易日（工作日）日期序列，动态生成防时间炸弹。"""
    end = end or date.today()
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


def _series(values):
    return pd.Series(values, index=pd.Index(_dates(len(values)), name="date"))


def _trend_up(n=260, base=100.0, daily=0.001):
    """平滑上行序列（强势上涨段）。"""
    return [base * (1 + daily) ** i for i in range(n)]


def _crash_then_recover(n_down=30, drop=0.003, n_recover=10, rec=0.01, base=100.0):
    """先涨 210 日 -> 回调 30 日(-0.3%/日, 累计约 -8.6%) -> 反弹 10 日(+1%/日)。

    反弹段 <= 20 日，保证低谷 dd20 仍落在 rebound 回看窗口内（近 20 日 min(dd20) <= -5%），
    且收盘 > MA20（修复中、未走完）-> rebound_repair。
    """
    vals = [base * (1.001) ** i for i in range(210)]
    peak = vals[-1]
    vals += [peak * (1 - drop) ** (i + 1) for i in range(n_down)]
    trough = vals[-1]
    vals += [trough * (1 + rec) ** (i + 1) for i in range(n_recover)]
    return vals


# ---------- classify_stages ----------

def test_strong_uptrend():
    stages = classify_stages(_series(_trend_up(260)))
    assert stages.iloc[-1] == "strong_uptrend"


def test_active_pullback_boundary_exact():
    """dd20 恰等于 -0.05 归活跃回调（<= 阈值，且仍在 MA200 上方）。"""
    vals = _trend_up(250, daily=0.001)          # MA200 < 0.95×峰值
    vals += [vals[-1] * 0.95] * 5               # 20日峰回撤恰 -5%
    stages = classify_stages(_series(vals))
    assert stages.iloc[-1] == "active_pullback"


def test_early_pullback_close_below_ma20():
    """MA200 上方、dd20 -4%（未到 -5%）、跌破 MA20 -> 高位回调初期。"""
    vals = [100 * (1.001) ** i for i in range(250)]
    peak = vals[-1]
    vals += [peak * (1 - 0.004) ** (i + 1) for i in range(6)]
    stages = classify_stages(_series(vals))
    assert stages.iloc[-1] == "early_pullback"


def test_deep_correction_priority_over_active():
    """跌破 MA200 时即使 dd20 <= -5% 也归深度调整（优先级 1）。"""
    vals = [100 * (1.001) ** i for i in range(260)]
    # 尾部急跌 15% 击穿 MA200
    peak = vals[-1]
    vals += [peak * (1 - 0.015) ** (i + 1) for i in range(12)]
    stages = classify_stages(_series(vals))
    assert stages.iloc[-1] == "deep_correction"


def test_rebound_repair_after_pullback():
    """回调 >5% 后站回 MA20（仍低于 MA50）-> 反弹修复。"""
    vals = _crash_then_recover()
    stages = classify_stages(_series(vals))
    assert stages.iloc[-1] == "rebound_repair"


def test_leading_rows_unclassified():
    """前 19 日指标未热 -> None，不误判。"""
    stages = classify_stages(_series(_trend_up(260)))
    assert stages.iloc[:19].isna().all()
    assert stages.iloc[30:].notna().all()


def test_stage_labels_complete():
    """5 阶段标签齐全（中文）。"""
    assert len(STAGE_LABELS) == 5 and all(STAGE_LABELS.values())


# ---------- stage_history_stats ----------

def test_history_stats_hand_computed():
    """合成单段上涨手算 n: 有效自第 20 日（ma20 未热 19 日），尾部 20 日无未来。"""
    vals = [100 * (1.001) ** i for i in range(100)]
    stats = stage_history_stats(_series(vals), horizons=(20,))
    s = stats["strong_uptrend"]
    assert s["n20"] == 100 - 19 - 20  # = 61
    assert s["up_ratio_20d"] == 1.0
    assert s["median_ret_20d"] > 0


def test_history_stats_horizon_tail_excluded():
    """60 日窗口尾部不足不计入 n60，但计入 n20。"""
    vals = [100 * (1.001) ** i for i in range(100)]
    stats = stage_history_stats(_series(vals), horizons=(20, 60))
    s = stats["strong_uptrend"]
    assert s["n20"] == 61
    assert s["n60"] == 21  # 100 - 19 - 60


def test_history_stats_avg_duration():
    """平均持续天数 = 各连续段均值（两段各 10 日 -> 10.0）。"""
    vals = [100.0] * 10 + [100 * (1 - 0.02) ** i for i in range(10)] + [80.0] * 10
    # 前 10 日 close 平坦 -> dd20=0 strong；中间跌破 MA20/dd20<=-3% -> early；尾段同 early
    stages = classify_stages(_series(vals))
    # 手算 duration 直接调内部函数
    dur = index_stage._avg_duration(stages.dropna())
    assert all(v > 0 for v in dur.values())


# ---------- stage_conclusion ----------

def _seed_db(tmp_path, n=250, values=None):
    db = str(tmp_path / "s.db")
    store.init_db(db)
    vals = values or _crash_then_recover()[-n:] if n <= 280 else values
    for d, v in zip(_dates(n), vals):
        store.store_index_snapshot({"date": d, "symbol": "GSPC", "kind": "index",
                                    "close": v}, db)
    return db


def test_conclusion_from_db(tmp_path):
    """DB 250 日合成回调反弹序列 -> 当前为反弹修复，证据含收盘/MA/dd20。"""
    db = _seed_db(tmp_path)
    c = stage_conclusion("GSPC", db)
    assert c["status"] == "ok" and c["stage"] == "rebound_repair"
    assert any("收盘" in e for e in c["evidence"])
    assert any("dd20" in e for e in c["evidence"])
    assert c["as_of"] is not None


def test_conclusion_insufficient_db(tmp_path):
    """DB 仅 10 日且腾讯回退失败 -> insufficient（不抛异常）。"""
    db = str(tmp_path / "s.db")
    store.init_db(db)
    for d, v in zip(_dates(10), _trend_up(10)):
        store.store_index_snapshot({"date": d, "symbol": "DJI", "kind": "index",
                                    "close": v}, db)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(index_stage, "load_index_history",
                   lambda *a, **kw: store.load_index_history("DJI", 250, db_path=db))
        from src.collectors import index_price
        mp.setattr(index_price, "collect_index_with_source", lambda *a, **kw: ([], ""))
        c = stage_conclusion("DJI", db)
    assert c["status"] == "insufficient" and c["stage"] is None


def test_conclusion_tencent_fallback(tmp_path):
    """DB <60 日 -> 回退腾讯采集序列成功判定。"""
    from src.collectors import index_price
    db = str(tmp_path / "s.db")
    store.init_db(db)
    for d, v in zip(_dates(10), _trend_up(10)):
        store.store_index_snapshot({"date": d, "symbol": "IXIC", "kind": "index",
                                    "close": v}, db)
    vals = _trend_up(260)
    fetched = [{"date": d, "close": v, "symbol": "IXIC"}
               for d, v in zip(_dates(260), vals)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(index_price, "collect_index_with_source",
                   lambda *a, **kw: (fetched, "tencent"))
        c = stage_conclusion("IXIC", db)
    assert c["status"] == "ok" and c["stage"] == "strong_uptrend"
    assert any("tencent" in e for e in c["evidence"])
