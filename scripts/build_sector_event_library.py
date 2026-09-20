"""一次性回测脚本：拉 12 版块 ETF + SPY 长历史，建版块轮动历史事件库。

用法:
    cd /Users/huangshifei/us-fundflow-monitor
    PYTHONPATH=. .venv/bin/python scripts/build_sector_event_library.py

数据源: 优先 Twelve Data（outputsize 5000 ≈ 2006-2026），fallback yfinance。
  2026-09-01 修复: 脚本此前不加载 .env，Twelve Data 判不可用而全量回退 yfinance，
  拉到字典末位的 UFO 时被限流静默跳过 -> UFO 0 事件（实证 DB COUNT=0）。
  现在与回测脚本一样先加载 .env。
产出: sector_history_events 表（幂等：先 DELETE 再 INSERT）
耗时: 约 2-5 分钟（13 symbol × 限流间隔 + 拉取）

事件触发（与 detect_rotation 同一配置，防语义错位）:
  - momentum_cross: RS-Momentum 去抖确认上穿 0 + 量能确认（vol_zscore > 阈值）
  - rs_cross: RS-Ratio 上穿 100 + RS-Momentum 连续 confirm_days 天为正
事件后跟踪: 5/10/20/60 天相对 SPY 超额收益（T+1 收盘入场）+ 持续天数
量能 Z-Score: 与 flow_indicators.vol_zscore_series 同一实现（基线不含当日 + 量级守卫）
"""
import sys
import os
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# 让 from src 能 import（脚本在 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 与回测脚本一致：先加载 .env（修复 Twelve Data key 不可见导致的 UFO 静默跳过）
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))

import yaml
import pandas as pd

from src.storage import store
from src.analyzers import sector_rotation, sector_stats, flow_indicators
from src.collectors import twelvedata

SECTORS = {
    "XLK": "科技", "XLE": "能源", "XLF": "金融", "XLV": "医疗",
    "XLY": "可选消费", "XLP": "必需消费", "XLI": "工业", "XLB": "材料",
    "XLRE": "房地产", "XLU": "公用事业", "XLC": "通信", "UFO": "太空",
}
BENCHMARK = "SPY"
VOL_PERIOD = 20      # 成交量基线窗口（与 flow_indicators 默认一致）
BUILD_LOG = ROOT / "logs" / "event_library_build.log"


def _load_config() -> dict:
    """读主配置：事件库与 detect_rotation 必须共用同一套周期/阈值，防语义错位。"""
    with open(ROOT / "config" / "watchlist.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _td_chunk(symbol: str, end_date: str = None) -> list:
    """Twelve Data 单块拉取（最多 5000 根，end_date 往前翻页用）。"""
    params = {"symbol": symbol, "interval": "1day", "outputsize": 5000}
    if end_date:
        params["end_date"] = end_date
    data = twelvedata._get(params)
    if not data or data.get("status") != "ok" or "values" not in data:
        return []
    rows = []
    for r in data["values"]:
        rows.append({
            "date": r.get("datetime"),
            "close": twelvedata._to_float(r.get("close")),
            "high": twelvedata._to_float(r.get("high")),
            "low": twelvedata._to_float(r.get("low")),
            "volume": twelvedata._to_int(r.get("volume")),
        })
    rows.reverse()  # 降序转升序
    return rows


EARLIEST_CAP = "1998-01-01"   # SPDR 版块 ETF 最早成立日（1998-12）之前的硬下界
MAX_CHUNKS = 4                # 5000 根/块，4 块 ≈ 79 年，安全上限


def fetch_full_history(symbol: str) -> pd.DataFrame:
    """拉全部可得历史（分页回溯到 ETF 成立日，免费档 start_date/end_date 已实测可用）。

    Twelve Data 单请求 outputsize 上限 5000 ≈ 20 年；用 end_date 翻页取更早数据，
    2026-09 起事件库从"约 20 年"扩展到 ETF 成立日（多数 SPDR 1998-12 成立）。
    返回 DataFrame（date,close,high,low,volume 升序）。
    """
    # 优先 Twelve Data（本机已配 key，免费 800 req/天，稳定）
    if twelvedata.has_key():
        all_rows: list = []
        end_date = None
        for _ in range(MAX_CHUNKS):
            chunk = _td_chunk(symbol, end_date)
            if not chunk:
                break
            all_rows = chunk + all_rows
            earliest = chunk[0]["date"]
            if earliest <= EARLIEST_CAP or len(chunk) < 5000:
                break  # 到底或该块未满（已到成立日）
            # 下一块取 earliest 前一日（日期字符串比较，避免时区/格式转换）
            y, m, d = (int(x) for x in earliest.split("-"))
            prev = (datetime(y, m, d) - timedelta(days=1)).strftime("%Y-%m-%d")
            end_date = prev
        if all_rows and len(all_rows) > 60:
            df = pd.DataFrame(all_rows)
            print(f"  {symbol}: 分页取史 {len(df)} 天，{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
            return df[["date", "close", "high", "low", "volume"]]

    # fallback yfinance（IP 级限流风险）
    import yfinance as yf
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
    config = _load_config()
    periods_cfg = config.get("sector_periods", {})
    thresholds_cfg = config.get("sector_thresholds", {})
    rs_ratio_period = periods_cfg.get("rs_ratio_period", 30)
    rs_mom_period = periods_cfg.get("rs_momentum_period", 30)
    vol_confirm = thresholds_cfg.get("vol_confirm_zscore", 1.5)
    confirm_days = thresholds_cfg.get("momentum_confirm_days", 2)
    mom_level = thresholds_cfg.get("rs_momentum_cross", 0.0)
    rs_level = thresholds_cfg.get("rs_ratio_cross", 100.0)
    print(f"建库参数: rs_ratio_period={rs_ratio_period} rs_mom_period={rs_mom_period} "
          f"confirm_days={confirm_days} vol_confirm={vol_confirm}")

    db_path = "data/fundflow.db"
    store.init_db(db_path)

    # 幂等：清空旧事件库（重建）
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM sector_history_events")
    conn.commit()
    conn.close()
    print("已清空旧事件库")

    BUILD_LOG.parent.mkdir(exist_ok=True)
    with open(BUILD_LOG, "a", encoding="utf-8") as lf:
        lf.write(f"=== 重建 {pd.Timestamp.now().isoformat()} "
                 f"P={rs_ratio_period}/{rs_mom_period} N={confirm_days} ===\n")

    # 拉基准 SPY
    print(f"拉 {BENCHMARK} 基准数据...")
    spy_df = fetch_full_history(BENCHMARK)
    if spy_df.empty:
        print(f"❌ {BENCHMARK} 数据获取失败，终止")
        sys.exit(1)
    print(f"  {BENCHMARK}: {len(spy_df)} 天，{spy_df['date'].iloc[0]} ~ {spy_df['date'].iloc[-1]}")

    all_events = []
    skipped = {}
    for sym, sector in SECTORS.items():
        print(f"处理 {sym} ({sector})...")
        sec_df = fetch_full_history(sym)
        if len(sec_df) < 60:
            reason = f"数据不足 60 天（{len(sec_df)} 条）"
            print(f"  {sym}: {reason}，跳过")
            skipped[sym] = reason
            with open(BUILD_LOG, "a", encoding="utf-8") as lf:
                lf.write(f"SKIP {sym}: {reason}\n")
            continue
        time.sleep(2)  # 限流保护

        # 按日期对齐
        merged = pd.merge(
            sec_df[["date", "close", "high", "low", "volume"]],
            spy_df[["date", "close"]].rename(columns={"close": "spy_close"}),
            on="date", how="inner",
        )
        if len(merged) < rs_ratio_period + rs_mom_period + 1:
            reason = f"对齐后数据不足 warm-up（{len(merged)} < {rs_ratio_period + rs_mom_period + 1}）"
            print(f"  {sym}: {reason}，跳过")
            skipped[sym] = reason
            with open(BUILD_LOG, "a", encoding="utf-8") as lf:
                lf.write(f"SKIP {sym}: {reason}\n")
            continue

        # RS 时间序列（周期来自 config，与实盘 sector_rotation.calc_sector 一致）
        merged["rs_ratio"] = sector_rotation.calc_rs_ratio(
            merged["close"], merged["spy_close"], period=rs_ratio_period)
        merged["rs_momentum"] = sector_rotation.calc_rs_momentum(
            merged["rs_ratio"], period=rs_mom_period)
        # 成交量 Z-Score（与 flow_indicators 同一实现：基线不含当日 + 量级守卫）
        merged["vol_zscore"] = flow_indicators.vol_zscore_series(
            merged["volume"], VOL_PERIOD)
        # SPY MA200 regime（在 merged 内计算保证行对齐；未热为 NaN）
        merged["spy_ma200"] = merged["spy_close"].rolling(200, min_periods=200).mean()

        # 识别事件（去抖确认，与 detect_rotation 同一语义）
        events = sector_stats.identify_events(
            merged[["date", "rs_ratio", "rs_momentum", "vol_zscore"]],
            vol_confirm=vol_confirm,
            confirm_days=confirm_days,
            mom_cross_level=mom_level,
            rs_cross_level=rs_level,
        )
        print(f"  {sym}: 识别 {len(events)} 事件")

        # 算每个事件后收益（sector_stats.compute_outcomes 为 T+1 基准）
        sec_close = merged["close"].tolist()
        spy_close = merged["spy_close"].tolist()
        date_to_idx = {d: i for i, d in enumerate(merged["date"].tolist())}
        for ev in events:
            idx = date_to_idx.get(ev["event_date"])
            if idx is None:
                continue
            outcomes = sector_stats.compute_outcomes(idx, sec_close, spy_close)
            ev.update(outcomes)
            ma = merged["spy_ma200"].iloc[idx]
            ev["spy_above_ma200"] = None if pd.isna(ma) else int(spy_close[idx] > ma)
            ev["symbol"] = sym
            ev["sector"] = sector
            all_events.append(ev)

    # 覆盖断言：12 个版块必须全部产事件，缺则非零退出（防再次静默缺版块）
    covered = {e["symbol"] for e in all_events}
    missing = set(SECTORS) - covered
    if missing:
        print(f"❌ 事件库覆盖缺口: {sorted(missing)} 未产出任何事件，"
              f"跳过原因: {skipped}")
        with open(BUILD_LOG, "a", encoding="utf-8") as lf:
            lf.write(f"FAIL coverage_gap: {sorted(missing)} skipped={skipped}\n")
        sys.exit(1)

    print(f"\n共 {len(all_events)} 事件，写库...")
    store.store_history_events(all_events, db_path)
    n = store.count_history_events(db_path)
    print(f"✅ 历史事件库构建完成: {n} 条")
    by_symbol = {s: sum(1 for e in all_events if e["symbol"] == s) for s in SECTORS}
    with open(BUILD_LOG, "a", encoding="utf-8") as lf:
        lf.write(f"OK total={n} by_symbol={by_symbol}\n")

    # 统计摘要（建库后自检）
    if all_events:
        rets = [e["ret_20d"] for e in all_events if e.get("ret_20d") is not None]
        if rets:
            print(f"  20日超额收益: 均值 {sum(rets)/len(rets)*100:.2f}%")
        sustained_amp = thresholds_cfg.get("sustained_amplitude", 0.02)
        base = sum(1 for r in rets if r >= sustained_amp) / len(rets)
        print(f"  持续基准率（ret_20d >= {sustained_amp*100:.0f}%）: {base*100:.1f}%")
        from collections import Counter
        by_sector = Counter(e["symbol"] for e in all_events)
        print(f"  各版块事件数: {dict(by_sector)}")
        by_type = Counter(e["trigger_type"] for e in all_events)
        print(f"  按触发类型: {dict(by_type)}")


if __name__ == "__main__":
    main()
