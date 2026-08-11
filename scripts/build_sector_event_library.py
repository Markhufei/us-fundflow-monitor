"""一次性回测脚本：拉 11 SPDR Sector ETF + SPY 长历史，建版块轮动历史事件库。

用法:
    cd /Users/huangshifei/us-fundflow-monitor
    PYTHONPATH=. .venv/bin/python scripts/build_sector_event_library.py

数据源: yfinance period='max'（可拉 20+ 年；Twelve Data outputsize 限制 5000≈20年，故建库用 yfinance）
产出: sector_history_events 表（幂等：先 DELETE 再 INSERT）
耗时: 约 2-5 分钟（12 symbol × 限流间隔 2s + 拉取）

事件触发:
  - momentum_cross: RS-Momentum 上穿 0 + 量能确认（vol_zscore > 1.5）
  - rs_cross: RS-Ratio 上穿 100 + RS-Momentum > 0
事件后跟踪: 5/10/20/60 天相对 SPY 超额收益 + 持续天数
"""
import sys
import os
import time
import sqlite3

# 让 from src 能 import（脚本在 scripts/ 下）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd

from src.storage import store
from src.analyzers import sector_rotation, sector_stats

SECTORS = {
    "XLK": "科技", "XLE": "能源", "XLF": "金融", "XLV": "医疗",
    "XLY": "可选消费", "XLP": "必需消费", "XLI": "工业", "XLB": "材料",
    "XLRE": "房地产", "XLU": "公用事业", "XLC": "通信",
}
BENCHMARK = "SPY"
VOL_CONFIRM = 1.5   # 量能确认阈值
VOL_PERIOD = 20     # 成交量均线周期


def fetch_full_history(symbol: str) -> pd.DataFrame:
    """拉长历史（约 20 年），优先 Twelve Data（稳定），fallback yfinance（限流风险）。

    Twelve Data outputsize 上限 5000 ≈ 20 年交易日，覆盖约 2006-2026。
    返回 DataFrame（date,close,high,low,volume 升序）。
    """
    from src.collectors import twelvedata
    # 优先 Twelve Data（本机已配 key，免费 800 req/天，稳定）
    if twelvedata.has_key():
        data = twelvedata.collect_price_volume(symbol, days=5000)
        if data and len(data) > 60:
            df = pd.DataFrame(data)
            return df[["date", "close", "high", "low", "volume"]]

    # fallback yfinance（IP 级限流风险）
    print(f"  {symbol} Twelve Data 不可用，fallback yfinance...")
    hist = None
    for attempt in range(3):
        try:
            hist = yf.Ticker(symbol).history(period="max")
            break
        except Exception as e:
            if attempt < 2 and "RateLimit" in type(e).__name__:
                print(f"  {symbol} 限流，30s 后重试...")
                time.sleep(30)
                continue
            print(f"  {symbol} 拉取失败: {e}")
            return pd.DataFrame()
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.reset_index()
    hist["date"] = hist["Date"].dt.strftime("%Y-%m-%d")
    return hist[["date", "Close", "High", "Low", "Volume"]].rename(
        columns={"Close": "close", "High": "high", "Low": "low", "Volume": "volume"}
    )


def main():
    db_path = "data/fundflow.db"
    store.init_db(db_path)

    # 幂等：清空旧事件库（重建）
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM sector_history_events")
    conn.commit()
    conn.close()
    print("已清空旧事件库")

    # 拉基准 SPY
    print(f"拉 {BENCHMARK} 基准数据...")
    spy_df = fetch_full_history(BENCHMARK)
    if spy_df.empty:
        print(f"❌ {BENCHMARK} 数据获取失败，终止")
        return
    print(f"  {BENCHMARK}: {len(spy_df)} 天，{spy_df['date'].iloc[0]} ~ {spy_df['date'].iloc[-1]}")

    all_events = []
    for sym, sector in SECTORS.items():
        print(f"处理 {sym} ({sector})...")
        sec_df = fetch_full_history(sym)
        if len(sec_df) < 60:
            print(f"  {sym} 数据不足 60 天（{len(sec_df)}），跳过")
            continue
        time.sleep(2)  # yfinance 限流保护

        # 按日期对齐
        merged = pd.merge(
            sec_df[["date", "close", "high", "low", "volume"]],
            spy_df[["date", "close"]].rename(columns={"close": "spy_close"}),
            on="date", how="inner",
        )
        if len(merged) < 60:
            continue

        # RS 时间序列
        merged["rs_ratio"] = sector_rotation.calc_rs_ratio(
            merged["close"], merged["spy_close"], period=10)
        merged["rs_momentum"] = sector_rotation.calc_rs_momentum(
            merged["rs_ratio"], period=10)
        # 成交量 Z-Score
        vol_mean = merged["volume"].rolling(VOL_PERIOD, min_periods=5).mean()
        vol_std = merged["volume"].rolling(VOL_PERIOD, min_periods=5).std()
        merged["vol_zscore"] = (merged["volume"] - vol_mean) / vol_std

        # 识别事件
        events = sector_stats.identify_events(
            merged[["date", "rs_ratio", "rs_momentum", "vol_zscore"]],
            vol_confirm=VOL_CONFIRM,
        )
        print(f"  {sym}: 识别 {len(events)} 事件")

        # 算每个事件后收益
        sec_close = merged["close"].tolist()
        spy_close = merged["spy_close"].tolist()
        date_to_idx = {d: i for i, d in enumerate(merged["date"].tolist())}
        for ev in events:
            idx = date_to_idx.get(ev["event_date"])
            if idx is None:
                continue
            outcomes = sector_stats.compute_outcomes(idx, sec_close, spy_close)
            ev.update(outcomes)
            ev["symbol"] = sym
            ev["sector"] = sector
            all_events.append(ev)

    print(f"\n共 {len(all_events)} 事件，写库...")
    store.store_history_events(all_events, db_path)
    n = store.count_history_events(db_path)
    print(f"✅ 历史事件库构建完成: {n} 条")

    # 统计摘要（建库后自检）
    if all_events:
        durs = [e["duration_days"] for e in all_events if e.get("duration_days") is not None]
        rets = [e["ret_10d"] for e in all_events if e.get("ret_10d") is not None]
        if durs:
            durs_sorted = sorted(durs)
            print(f"  持续天数: 中位数 {durs_sorted[len(durs_sorted)//2]}，均值 {sum(durs)/len(durs):.1f}")
        if rets:
            print(f"  10日超额收益: 均值 {sum(rets)/len(rets)*100:.2f}%")
        # 按版块统计
        from collections import Counter
        by_sector = Counter(e["symbol"] for e in all_events)
        print(f"  各版块事件数: {dict(by_sector)}")


if __name__ == "__main__":
    main()
