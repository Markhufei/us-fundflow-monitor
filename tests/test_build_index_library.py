"""build_index_event_library 建库脚本测试（红态验收；已按 2026-10-18 实现对齐）。

覆盖: pick_by_volume 纯函数（量最大者/缺数据剔除/并列决胜）；map_yf_symbol 裸符号映射；
fetch mock 下的特征帧构建与事件落库；覆盖缺口 -> SystemExit（防静默缺指数）。

对齐的实现形态:
  - pick_by_volume(candidates: {symbol: [hist 行]}, window=N) -> symbol|None，
    按末 window 日均量选最大；无记录/不足 window 半数/均量 0 剔除；并列取首位
  - main() 用相对路径 data/fundflow.db（测试 monkeypatch.chdir(tmp) 隔离，不碰真实库）
  - 配置经 _load_config() 读取（测试 monkeypatch 注入）；指数本体走
    index_price.fetch_full_history_with_source，ETF/SPY 走 fetch_etf_full_history
"""
import sys
import os
import importlib.util
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from src.storage import store

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_index_event_library.py"

TH = {
    "pullback_dd": -0.05,
    "pullback_lookback": 15,
    "cmf_withdrawal": -0.10,
    "rebound_min_ret": 0.015,
    "confirm_days": 2,
    "vol_confirm": 1.5,
    "sustained_amplitude": 0.02,
    "momentum_tol_abs": 0.5,
    "prob_min_sample": 10,
    "prob_high_sample": 30,
}
CONFIG = {
    "index_watch": [
        {"symbol": "GSPC", "name": "标普500", "flow_proxy": "SPY",
         "rs_framework": False, "leveraged_long": "SPXL", "leveraged_short": "SPXU"},
    ],
    "index_thresholds": TH,
    "index_periods": {"rs_ratio_period": 30, "rs_momentum_period": 30},
}


def _load_build_module():
    """按文件路径加载建库脚本（scripts/ 非 package）。未创建时 skip（红态）。"""
    if not SCRIPT.exists():
        pytest.skip("scripts/build_index_event_library.py 未创建（红态验收）")
    spec = importlib.util.spec_from_file_location("build_index_event_library", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)


# ===================== pick_by_volume =====================

def test_pick_by_volume_picks_max():
    """按真实成交量实测选均量最大者。"""
    mod = _load_build_module()
    cands = {"SPXL": [{"volume": 500_000}] * 3,
             "SSO": [{"volume": 900_000}] * 3,
             "SPXU": [{"volume": 700_000}] * 3}
    assert mod.pick_by_volume(cands, window=3) == "SSO"


def test_pick_by_volume_skips_missing_data():
    """缺数据剔除: 无记录 / 不足 window 半数 / 均量为 0；全缺 -> None（不硬选）。"""
    mod = _load_build_module()
    cands = {"SPXL": [{"volume": None}] * 3,      # 均量 0 -> 剔除
             "SSO": [{"volume": 600_000}] * 3,
             "SPXU": []}                          # 无记录 -> 剔除
    assert mod.pick_by_volume(cands, window=3) == "SSO"
    # 不足 window 半数（window=6 需 >=3 行，A 仅 2 行）
    short = {"A": [{"volume": 999_999}] * 2, "B": [{"volume": 100_000}] * 6}
    assert mod.pick_by_volume(short, window=6) == "B"
    assert mod.pick_by_volume({"A": [{"volume": None}] * 3, "B": []}, window=3) is None
    assert mod.pick_by_volume({}, window=3) is None


def test_pick_by_volume_tie_breaks_deterministic():
    """并列同量 -> 取输入顺序首位（可复现，日志证据可追溯）。"""
    mod = _load_build_module()
    cands = {"SPXL": [{"volume": 800_000}] * 3,
             "SSO": [{"volume": 800_000}] * 3}
    assert mod.pick_by_volume(cands, window=3) == "SPXL"


# ===================== map_yf_symbol =====================

def test_map_yf_symbol_bare_to_caret():
    """裸指数符号 -> ^ 前缀（yfinance 口径）；已带 ^ 幂等。

    契约依据: yfinance 直传裸符号会静默拿错数据（计划风险清单 #2）。
    """
    index_price = pytest.importorskip(
        "src.collectors.index_price",
        reason="src/collectors/index_price.py 未落地（红态验收）",
    )
    assert index_price.map_yf_symbol("GSPC") == "^GSPC"
    assert index_price.map_yf_symbol("IXIC") == "^IXIC"
    assert index_price.map_yf_symbol("DJI") == "^DJI"
    assert index_price.map_yf_symbol("^GSPC") == "^GSPC"


# ===================== 建库主流程（fetch mock） =====================

def _pullback_df():
    """63 日合成 GSPC 帧: 56 日横盘 + 4 日连跌（末日 dd20=-0.07，倒数第 2 日确认）+ 3 日反弹。

    确认日不在最后一天（其后有 3 根 bar）-> 事件后跟踪字段（max_dd_20d 等）可计算。
    """
    closes = [100.0] * 56 + [99.0, 96.0, 94.0, 93.0] + [97.0, 100.0, 104.0]
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(len(closes))]
    return pd.DataFrame({
        "date": dates, "close": closes,
        "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "volume": [None] * len(closes),
    })


def _flat_df(days=70, base=500.0):
    return pd.DataFrame({
        "date": [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(days)],
        "close": [base] * days,
        "high": [base * 1.01] * days, "low": [base * 0.99] * days,
        "volume": [1_000_000] * days,
    })


def test_build_stores_events_with_mocked_fetch(tmp_path, monkeypatch):
    """mock fetch 下: 特征帧构建 -> 事件识别 -> 落库 index_history_events，覆盖无缺口。

    main() 用相对路径 data/fundflow.db -> chdir(tmp) 隔离，不碰真实库。
    """
    mod = _load_build_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    def fake_index_fetch(symbol):
        df = _pullback_df() if symbol == "GSPC" else _flat_df()
        return df, "tencent"   # 腾讯主源改造后 fetch_full_history_with_source 返回 (df, source)

    monkeypatch.setattr("src.collectors.index_price.fetch_full_history_with_source", fake_index_fetch)
    monkeypatch.setattr(mod, "fetch_etf_full_history", lambda symbol: _flat_df())
    monkeypatch.setattr(mod, "_load_config", lambda: CONFIG)

    mod.main()

    db = "data/fundflow.db"   # 相对路径，落在 tmp CWD
    assert store.count_index_events(db) >= 1
    rows = store.query_index_events("GSPC", "pullback_start", db_path=db)
    assert rows, "GSPC 合成回撤序列应产出 pullback_start 事件"
    ev = rows[0]
    assert ev["event_date"] == _pullback_df()["date"].iloc[59]   # 4 日连跌的确认日
    assert ev["max_dd_20d"] == 0.0   # 确认日后 3 日连涨 -> 窗口内运行峰回撤 0（已计算非 None）


def test_build_coverage_gap_exits(tmp_path, monkeypatch):
    """指数覆盖缺口（某指数 0 事件）-> SystemExit 非零退出，不静默（镜像 sector 建库断言）。"""
    mod = _load_build_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    def fake_index_fetch(symbol):
        return pd.DataFrame(columns=["date", "close", "high", "low", "volume"]), ""

    monkeypatch.setattr("src.collectors.index_price.fetch_full_history_with_source", fake_index_fetch)
    monkeypatch.setattr(mod, "fetch_etf_full_history", lambda symbol: _flat_df())
    monkeypatch.setattr(mod, "_load_config", lambda: CONFIG)

    with pytest.raises(SystemExit):
        mod.main()
