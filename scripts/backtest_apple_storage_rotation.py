"""RS 轮动回测：周期(10/20/30) × 去抖(1/2/3天) 参数对比（复用 sector_rotation.py 的 JdK RS 公式）

背景: 08-14 回测（period=10, 无去抖, 2025-10-01~2026-08-13）证 RS-Mom 切换 whipsaw 严重
（AAPLvsMU 切 54 次胜率 55% 仍大幅跑输持有）。本脚本为 detect_rotation 参数选择提供数据。

逻辑:
  RS-Ratio = 100 * (A/B) / rolling.mean(A/B, period)
  RS-Mom   = 100 * (RS-Ratio / RS-Ratio.shift(period) - 1)
  信号: RS-Mom > 0 持 A；< 0 持 B
  去抖: 上穿需连续 N 天 RS-Mom > 0 才切换到 A（下穿同理 N 天 < 0 切 B）；N=1 即无去抖
  执行: T 日收盘信号, T+1 持仓（避免前视）；always-in 单边做多
  成本: 每次切换扣 0.1%（佣金+价差+滑点近似）

对比组:
  个股对（与 08-14 记录可比）: AAPL vs SOXX / AAPL vs MU
  版块对（detect_rotation 实际语义 = 版块 vs SPY 轮动）: XLK vs SPY / XLE vs SPY / XLF vs SPY
基准: 买入持有A / 买入持有B / 50-50每日再平衡

数据源: Twelve Data 1day, 每标的拉 500 条（含 START 前 warm-up）
运行: cd /Users/huangshifei/us-fundflow-monitor && .venv/bin/python scripts/backtest_apple_storage_rotation.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
sys.path.insert(0, str(ROOT))

from src.collectors import twelvedata
import pandas as pd
import numpy as np

DAYS = 500
START = "2025-10-01"
PERIODS = [10, 20, 30]      # RS 归一化窗口 = ROC 窗口
DEBOUNCES = [1, 2, 3]       # 连续确认天数（1=无去抖）
COST_PER_SWITCH = 0.001     # 0.1%/笔
PAIRS = [("AAPL", "SOXX"), ("AAPL", "MU"),
         ("XLK", "SPY"), ("XLE", "SPY"), ("XLF", "SPY")]
FETCH_TS = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")


def fetch(sym):
    data = twelvedata.collect_price_volume(sym, DAYS)
    if not data:
        return None
    s = pd.Series({d["date"]: d["close"] for d in data}, name=sym)
    s.index = pd.to_datetime(s.index)
    return s.sort_index().dropna()


def backtest_pair(a_name, a_series, b_name, b_series, period, debounce):
    """A vs B 的 RS 轮动回测（period=RS 窗口，debounce=切换确认天数）。返回指标 dict。"""
    df = pd.DataFrame({a_name: a_series, b_name: b_series}).dropna()
    ratio = df[a_name] / df[b_name]
    rs_ratio = 100 * ratio / ratio.rolling(period, min_periods=period).mean()
    rs_mom = 100 * (rs_ratio / rs_ratio.shift(period) - 1)

    # 去抖信号: 连续 debounce 天 mom>0 才切 A；连续 debounce 天 mom<0 才切 B；否则维持原持仓
    pos = rs_mom > 0
    neg = rs_mom < 0
    if debounce <= 1:
        up_ok, down_ok = pos, neg
    else:
        up_ok = pos & (pos.rolling(debounce).sum() == debounce)
        down_ok = neg & (neg.rolling(debounce).sum() == debounce)
    sig = pd.Series(np.nan, index=df.index, dtype=object)
    sig[up_ok] = a_name
    sig[down_ok] = b_name   # pos/neg 互斥，同日不可能同时触发
    # T+1 持仓（避免前视）
    held = sig.shift(1).ffill()

    ret = df.pct_change()
    strat_ret = pd.Series(np.where(held == a_name, ret[a_name], ret[b_name]),
                          index=df.index)
    strat_ret = strat_ret.where(held.notna(), 0.0)

    # 扣成本：每次换标的扣 COST_PER_SWITCH
    switch = held != held.shift(1)
    switch_cost = pd.Series(np.where(switch, COST_PER_SWITCH, 0.0), index=df.index)
    strat_ret_net = strat_ret - switch_cost

    # 截取回测区间
    mask = (df.index >= START)
    r_bt = ret[mask]
    s_gross = strat_ret[mask]
    s_net = strat_ret_net[mask]
    held_bt = held[mask]

    def stats(r):
        nav = (1 + r).cumprod()
        cum = nav.iloc[-1] - 1
        days = len(r)
        ann = (1 + cum) ** (252 / days) - 1 if days > 0 else np.nan
        dd = (nav / nav.cummax() - 1).min()
        return cum, ann, dd

    c_g, a_g, d_g = stats(s_gross)
    c_n, a_n, d_n = stats(s_net)
    c_a, a_a, d_a = stats(r_bt[a_name])
    c_b, a_b, d_b = stats(r_bt[b_name])
    half = r_bt[[a_name, b_name]].mean(axis=1)
    c_h, a_h, d_h = stats(half)

    # 切换次数 + 段胜率（每次切换点净值是否高于上一切换点）
    switches = (held_bt != held_bt.shift(1)).sum()
    nav_all = (1 + s_gross).cumprod()
    switch_pts = held_bt[held_bt != held_bt.shift(1)].index
    wins = 0
    total = 0
    prev_nav = None
    for pt in switch_pts:
        cur_nav = nav_all.loc[pt]
        if prev_nav is not None and cur_nav > prev_nav:
            wins += 1
        if prev_nav is not None:
            total += 1
        prev_nav = cur_nav
    win_rate = wins / total if total > 0 else np.nan

    return {
        "pair": f"{a_name} vs {b_name}", "period": period, "debounce": debounce,
        "strat_gross": (c_g, a_g, d_g),   # 未扣费
        "strat_net": (c_n, a_n, d_n),     # 扣 0.1%/笔
        "buy_a": (c_a, a_a, d_a), "buy_b": (c_b, a_b, d_b), "half_half": (c_h, a_h, d_h),
        "switches": int(switches), "win_rate": win_rate,
    }


def fmt(c, a, d):
    return f"累计{c*100:+7.1f}% 年化{a*100:+7.1f}% 回撤{d*100:5.1f}%"


print(f"=== 数据采集 (源: Twelve Data time_series 1day, 拉取: {FETCH_TS}) ===")
closes = {}
for sym in sorted({s for p in PAIRS for s in p}):
    s = fetch(sym)
    closes[sym] = s
    rng = f"{s.index.min().date()}~{s.index.max().date()} ({len(s)}条)" if s is not None else "无数据"
    print(f"  {sym}: {rng}")

end_date = min(s.index.max() for s in closes.values() if s is not None).date()
print(f"\n回测区间: {START} ~ {end_date} | 周期: {PERIODS} | 去抖: {DEBOUNCES} 天 | 成本: {COST_PER_SWITCH*100:.1f}%/笔")

# 汇总表: 每 pair 一张 周期×去抖 对比表
for a_name, b_name in PAIRS:
    a, b = closes.get(a_name), closes.get(b_name)
    if a is None or b is None:
        print(f"\n=== {a_name} vs {b_name}: 数据缺失，跳过 ===")
        continue
    print(f"\n{'='*72}\n=== {a_name} vs {b_name} ===")
    print(f"{'参数':>14} | {'切换':>4} {'段胜率':>6} | 策略(扣费) | 买入持A | 买入持B | 50/50")
    for period in PERIODS:
        for debounce in DEBOUNCES:
            r = backtest_pair(a_name, a, b_name, b, period, debounce)
            c_n, a_n, d_n = r["strat_net"]
            wr = f"{r['win_rate']*100:3.0f}%" if r['win_rate'] == r['win_rate'] else "  -"
            # 参照基准（同 period 无关，只算一次也行，重复算开销小，保持对齐简单）
            c_a = r["buy_a"][0]; c_b = r["buy_b"][0]; c_h = r["half_half"][0]
            tag = f"P{period}/N{debounce}"
            print(f"{tag:>14} | {r['switches']:>4} {wr:>6} | {c_n*100:+7.1f}%/{d_n*100:4.1f}% "
                  f"| {c_a*100:+6.1f}% | {c_b*100:+6.1f}% | {c_h*100:+6.1f}%")

print(f"\n=== 数据来源: Twelve Data | 拉取: {FETCH_TS} | 回测区间: {START}~{end_date} ===")
print("注: 未含分红/税收；扣成本按每次切换 0.1% 估；always-in 单边做多；列『策略(扣费)』为累计/最大回撤。")
