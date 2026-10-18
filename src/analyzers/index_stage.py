"""指数市场阶段判定 + 历史类比统计（纯展示层分析，不进告警/事件库）。

5 阶段量化定义（判定优先级从上到下，全部基于 close/MA/dd20，可回测可验证）:
  1. deep_correction 深度调整: MA200 有效且 close < MA200（熊市段，期间反弹不另分类防抖动）
  2. active_pullback  活跃回调: MA200 上方且 dd20 <= stage_dd_active(-0.05)
  3. rebound_repair   反弹修复: 近 stage_rebound_lookback(20) 日 min(dd20) <= -0.05 且 close > MA20
  4. early_pullback   高位回调初期: close < MA20 或 dd20 <= stage_dd_early(-0.03)
  5. strong_uptrend   强势上涨: 兜底剩余（close >= MA20 健康段）

诚实性边界: 历史同类阶段统计（未来 20/60 日收益分布）为**描述性类比非预测**，
展示时必须带样本量与"非预测"标注（项目概率判别力 r≈0.08 教训）。
"""
from typing import Dict, Optional

import pandas as pd

from src.storage.store import load_index_history

STAGE_LABELS = {
    "strong_uptrend": "强势上涨",
    "early_pullback": "高位回调初期",
    "active_pullback": "活跃回调",
    "deep_correction": "深度调整",
    "rebound_repair": "反弹修复",
}

STAGE_EMOJI = {
    "strong_uptrend": "🟢",
    "early_pullback": "🟡",
    "active_pullback": "🟠",
    "deep_correction": "🔴",
    "rebound_repair": "🔵",
}

_DEFAULT_THRESHOLDS = {
    "stage_dd_early": -0.03,
    "stage_dd_active": -0.05,
    "stage_rebound_lookback": 20,
}


def _merged_thresholds(thresholds: Optional[dict]) -> dict:
    t = dict(_DEFAULT_THRESHOLDS)
    if thresholds:
        for k in t:
            if thresholds.get(k) is not None:
                t[k] = thresholds[k]
    return t


def _stage_features(close: pd.Series, thresholds: Optional[dict] = None) -> pd.DataFrame:
    """计算阶段判定所需的指标列（ma20/ma50/ma200/dd20/dd_min_20）。"""
    t = _merged_thresholds(thresholds)
    f = pd.DataFrame({"close": close.astype(float)})
    f["ma20"] = close.rolling(20).mean()
    f["ma50"] = close.rolling(50).mean()
    f["ma200"] = close.rolling(200).mean()
    f["dd20"] = close / close.rolling(20).max() - 1
    f["dd_min_20"] = f["dd20"].rolling(int(t["stage_rebound_lookback"])).min()
    return f


def classify_stages(close: pd.Series, thresholds: Optional[dict] = None) -> pd.Series:
    """逐日判定市场阶段（向量化），返回阶段名 Series（指标未热的 leading 行为 None）。"""
    t = _merged_thresholds(thresholds)
    f = _stage_features(close, thresholds)
    s, ma20, ma50, ma200 = f["close"], f["ma20"], f["ma50"], f["ma200"]
    dd20, dd_min = f["dd20"], f["dd_min_20"]

    valid = ma20.notna()  # 前 19 日指标未热，不判定
    below200 = ma200.notna() & (s < ma200)
    active = ~below200 & (dd20 <= t["stage_dd_active"])
    rebound = ~below200 & ~active & dd_min.notna() & (dd_min <= t["stage_dd_active"]) & (s > ma20)
    early = ~below200 & ~active & ~rebound & ((s < ma20) | (dd20 <= t["stage_dd_early"]))

    stage = pd.Series(None, index=close.index, dtype=object)
    stage[valid & below200] = "deep_correction"
    stage[valid & active] = "active_pullback"
    stage[valid & rebound] = "rebound_repair"
    stage[valid & early] = "early_pullback"
    stage[valid & ~below200 & ~active & ~rebound & ~early] = "strong_uptrend"
    return stage


def _avg_duration(stages: pd.Series) -> Dict[str, float]:
    """各阶段连续段平均长度（交易日；含被历史截断的尾段）。"""
    runs: Dict[str, list] = {}
    cur_stage, cur_len = None, 0
    for v in stages:
        if v != cur_stage:
            if cur_stage is not None:
                runs.setdefault(cur_stage, []).append(cur_len)
            cur_stage, cur_len = v, 1
        else:
            cur_len += 1
    if cur_stage is not None:
        runs.setdefault(cur_stage, []).append(cur_len)
    return {k: sum(v) / len(v) for k, v in runs.items()}


def stage_history_stats(close: pd.Series, thresholds: Optional[dict] = None,
                        horizons=(20, 60)) -> Dict[str, Dict[str, Optional[float]]]:
    """历史同类阶段统计：各阶段未来 h 日收益分布（描述性类比，非预测）。

    Returns:
        {stage: {n20, up_ratio_20d, median_ret_20d, n60, up_ratio_60d,
                 median_ret_60d, avg_duration_days}}
        未来窗口不足的尾部日不计入对应 horizon 的样本。
    """
    stages = classify_stages(close, thresholds)
    f = _stage_features(close, thresholds)
    out: Dict[str, Dict[str, Optional[float]]] = {}
    durations = _avg_duration(stages.dropna())

    for h in horizons:
        fwd = close.shift(-h) / close - 1
        for stage in STAGE_LABELS:
            mask = (stages == stage) & fwd.notna()
            n = int(mask.sum())
            key_n, key_up, key_med = f"n{h}", f"up_ratio_{h}d", f"median_ret_{h}d"
            entry = out.setdefault(stage, {"avg_duration_days": None})
            entry[key_n] = n
            if n > 0:
                rets = fwd[mask]
                entry[key_up] = float((rets > 0).mean())
                entry[key_med] = float(rets.median())
            else:
                entry[key_up] = None
                entry[key_med] = None

    for stage in STAGE_LABELS:
        out[stage]["avg_duration_days"] = durations.get(stage)
    return out


def stage_conclusion(symbol: str, db_path: str,
                     thresholds: Optional[dict] = None) -> Dict:
    """当前阶段判定 + 证据明细。

    数据源: DB 最近 250 日快照 close；不足 60 日（部署初期）回退腾讯采集
    （collect_index_with_source, days=260），证据末行注明实际来源。

    Returns:
        {"symbol", "stage", "status": "ok"/"insufficient", "as_of", "evidence": [str]}
    """
    hist = load_index_history(symbol, days=250, db_path=db_path)
    rows = [r for r in hist if r.get("close") is not None]
    source_note = "DB快照"
    if len(rows) < 60:
        # 部署初期 DB 快照不足 -> 腾讯回退（真指数 close，与建库同源）
        from src.collectors import index_price
        fetched, src = index_price.collect_index_with_source(symbol, days=420)
        rows = [{"date": r["date"], "close": r.get("close"), "cmf": None, "mfi": None}
                for r in fetched if r.get("close") is not None]
        source_note = f"{src}采集"
        if len(rows) < 60:
            return {"symbol": symbol, "stage": None, "status": "insufficient",
                    "as_of": rows[-1]["date"] if rows else None, "evidence": []}

    close = pd.Series([float(r["close"]) for r in rows],
                      index=pd.Index([r["date"] for r in rows], name="date"))
    f = _stage_features(close, thresholds)
    stage = classify_stages(close, thresholds).iloc[-1]
    latest = f.iloc[-1]

    def _fmt(v, pct=False):
        if pd.isna(v):
            return "N/A"
        return f"{v * 100:.1f}%" if pct else f"{v:,.2f}"

    ma20_rising = latest["ma20"] > f["ma20"].iloc[-6] if len(f) > 6 else None
    evidence = [
        f"收盘 {_fmt(latest['close'])}",
        f"MA20 {_fmt(latest['ma20'])}{'↑' if ma20_rising else ('↓' if ma20_rising is False else '')}",
        f"MA50 {_fmt(latest['ma50'])}",
        f"MA200 {_fmt(latest['ma200'])}",
        f"dd20 {_fmt(latest['dd20'], pct=True)}",
    ]
    # 快照里的资金流指标（有则附，来自代理 ETF 口径）
    snap = rows[-1]
    for k, label in (("cmf", "CMF"), ("mfi", "MFI")):
        if snap.get(k) is not None:
            evidence.append(f"{label} {_fmt(snap[k])}")
    evidence.append(f"数据源 {source_note}（截至 {rows[-1]['date']}）")
    return {"symbol": symbol, "stage": stage, "status": "ok",
            "as_of": rows[-1]["date"], "evidence": evidence}
