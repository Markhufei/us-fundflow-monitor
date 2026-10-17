"""一次性回测脚本：拉 3 大指数 + 代理 ETF + SPY 长历史，建指数历史事件库。

用法:
    cd /Users/huangshifei/us-fundflow-monitor
    PYTHONPATH=. .venv/bin/python scripts/build_index_event_library.py          # 建库
    PYTHONPATH=. .venv/bin/python scripts/build_index_event_library.py --select-etfs  # 杠杆 ETF 实测选择

数据源: 指数本体走 yfinance（Twelve Data 免费档不支持指数，2026-10-16 实测 404）；
  代理 ETF / SPY 走 Twelve Data 分页取史（复用 build_sector_event_library.fetch_full_history），
  失败 fallback yfinance。先加载 .env（与 sector 建库同款修复）。
产出: index_history_events 表（幂等：先 DELETE 再 INSERT）
耗时: 约 5-10 分钟（yfinance 指数 3 + TD ETF 4 个分页 + 限流间隔）

事件触发: 与 detect_index_signals 共用 identify_index_events（同一配置同一函数，防语义错位）。
事件后跟踪: 5/10/20/60 天绝对收益（T+1 收盘入场，非超额口径）+ max_dd_20d + 持续天数。
"""
import sys
import os
import time
import sqlite3
from pathlib import Path
from collections import Counter
from typing import Dict, List, Any, Optional

# 让 from src 能 import（脚本在 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 与 sector 建库一致：先加载 .env（修复 Twelve Data key 不可见导致的静默跳过）
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))

# 复用 sector 建库的 ETF 分页取史（Twelve Data 优先 + yfinance fallback，勿重写）
sys.path.insert(0, str(ROOT / "scripts"))
from build_sector_event_library import fetch_full_history as fetch_etf_full_history

import yaml
import pandas as pd

from src.storage import store
from src.analyzers import index_signals
from src.collectors import index_price

INDEXES_LOG = ROOT / "logs" / "index_etf_selection.log"
VOL_WINDOW = 90  # 杠杆 ETF 均量实测窗口（交易日）


def _load_config() -> dict:
    """读主配置：事件库与 detect_index_signals 必须共用同一套周期/阈值，防语义错位。"""
    with open(ROOT / "config" / "watchlist.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pick_by_volume(candidates: Dict[str, List[Dict[str, Any]]],
                   window: int = VOL_WINDOW) -> Optional[str]:
    """候选 ETF 中选近 window 日均量最大者（纯函数，可单测）。

    缺数据剔除: 无记录/不足 window 半数/均量为 0 的候选不参与。
    全部不可用时返回 None。
    """
    best, best_vol = None, 0.0
    for sym, hist in candidates.items():
        vols = [h.get("volume") or 0 for h in hist[-window:]]
        if len(vols) < window // 2:
            continue
        avg = sum(vols) / len(vols)
        if avg > best_vol:
            best, best_vol = sym, avg
    return best


def select_etfs(config: dict):
    """--select-etfs 模式：实测各候选杠杆 ETF 均量，选择证据追加写日志（不改配置）。

    结果人工回填 config/watchlist.yaml（禁止脚本 safe_dump 回写，毁注释）。
    """
    from src.collectors import price_volume

    watch = config.get("index_watch", [])
    lines = [f"=== 杠杆 ETF 均量实测 {pd.Timestamp.now().isoformat()} "
             f"窗口={VOL_WINDOW} 交易日 ==="]
    for item in watch:
        symbol = item["symbol"]
        for direction, cands in (("leveraged_long",
                                  ["SSO", "SPXL", "UDOW", "DDM", "TQQQ"]),
                                 ("leveraged_short",
                                  ["SPXU", "SDS", "SDOW", "DXD", "SQQQ"])):
            if not item.get(direction):
                continue
            hist_map: Dict[str, list] = {}
            for c in cands:
                h = price_volume.collect(c, days=VOL_WINDOW + 10)
                if h:
                    hist_map[c] = h
                time.sleep(1)
            picked = pick_by_volume(hist_map)
            vols = {c: (sum(x.get("volume") or 0 for x in h[-VOL_WINDOW:]) / len(h[-VOL_WINDOW:])
                        if h else None) for c, h in hist_map.items()}
            vols_str = " / ".join(f"{c} {v/1e6:.2f}M" if v else f"{c} 无数据"
                                  for c, v in vols.items())
            cur = item.get(direction)
            lines.append(f"{symbol} {direction}: 选中={picked} 当前配置={cur} | {vols_str}")
    text = "\n".join(lines)
    print(text)
    INDEXES_LOG.parent.mkdir(exist_ok=True)
    with open(INDEXES_LOG, "a", encoding="utf-8") as lf:
        lf.write(text + "\n")
    print(f"\n证据已追加 {INDEXES_LOG}（如与 config 不符请人工回填，脚本不回写配置）")


def main():
    config = _load_config()
    periods_cfg = config.get("index_periods", {})
    thresholds_cfg = config.get("index_thresholds", {})
    if "--select-etfs" in sys.argv:
        select_etfs(config)
        return

    rs_ratio_period = periods_cfg.get("rs_ratio_period", 30)
    rs_mom_period = periods_cfg.get("rs_momentum_period", 30)
    print(f"建库参数: rs_ratio_period={rs_ratio_period} rs_mom_period={rs_mom_period} "
          f"thresholds={thresholds_cfg}")

    db_path = "data/fundflow.db"
    os.makedirs("data", exist_ok=True)  # 空目录/CWD 下首跑自建（sqlite 不能在不存在目录建库）
    store.init_db(db_path)

    # 幂等：清空旧事件库（重建）
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM index_history_events")
    conn.commit()
    conn.close()
    print("已清空旧指数事件库")

    # SPY 全史（RS 基准 + spy_above_ma200 regime），Twelve Data 分页优先
    print("拉 SPY 基准数据...")
    spy_df = fetch_etf_full_history("SPY")
    if spy_df.empty:
        print("❌ SPY 数据获取失败，终止")
        sys.exit(1)
    print(f"  SPY: {len(spy_df)} 天，{spy_df['date'].iloc[0]} ~ {spy_df['date'].iloc[-1]}")

    all_events = []
    skipped = {}
    for item in config.get("index_watch", []):
        symbol = item["symbol"]
        name = item.get("name", symbol)
        proxy_sym = item.get("flow_proxy")
        print(f"处理 {symbol} ({name})...")

        idx_df = index_price.fetch_full_history(symbol)
        if len(idx_df) < 60:
            reason = f"指数数据不足 60 天（{len(idx_df)} 条）"
            print(f"  {symbol}: {reason}，跳过")
            skipped[symbol] = reason
            continue
        print(f"  {symbol}: 指数 {len(idx_df)} 天，{idx_df['date'].iloc[0]} ~ {idx_df['date'].iloc[-1]}")

        proxy_df = fetch_etf_full_history(proxy_sym) if proxy_sym else pd.DataFrame()
        if len(proxy_df) < 60:
            reason = f"代理 {proxy_sym} 数据不足 60 天"
            print(f"  {symbol}: {reason}，跳过")
            skipped[symbol] = reason
            continue
        time.sleep(2)  # 限流保护

        features = index_signals.build_features(
            idx_df, proxy_df, spy_df,
            benchmark="SPY" if item.get("rs_framework") else None,
            periods=periods_cfg)
        if features.empty:
            reason = "指数与代理无共同交易日"
            print(f"  {symbol}: {reason}，跳过")
            skipped[symbol] = reason
            continue

        # SPY MA200 regime（在 features 上对齐计算，未热为 NaN）
        spy_aligned = features[["date"]].merge(
            spy_df[["date", "close"]].rename(columns={"close": "spy_close"}),
            on="date", how="left")
        spy_ma200 = spy_aligned["spy_close"].rolling(200, min_periods=200).mean()

        events = index_signals.identify_index_events(features, thresholds_cfg)
        print(f"  {symbol}: 识别 {len(events)} 事件")

        closes = features["close"].tolist()
        date_to_idx = {d: i for i, d in enumerate(features["date"].tolist())}
        for ev in events:
            idx = date_to_idx.get(ev["event_date"])
            if idx is None:
                continue
            outcomes = index_signals.compute_index_outcomes(
                idx, closes, trigger_type=ev["trigger_type"])
            ev.update(outcomes)
            ma = spy_ma200.iloc[idx] if idx < len(spy_ma200) else None
            ev["spy_above_ma200"] = None if pd.isna(ma) else int(closes[idx] > ma)
            ev["symbol"] = symbol
            all_events.append(ev)

    # 覆盖断言：3 个指数必须全部产事件，缺则非零退出（防静默缺指数）
    covered = {e["symbol"] for e in all_events}
    expected = {item["symbol"] for item in config.get("index_watch", [])}
    missing = expected - covered
    if missing:
        print(f"❌ 事件库覆盖缺口: {sorted(missing)} 未产出任何事件，跳过原因: {skipped}")
        sys.exit(1)

    print(f"\n共 {len(all_events)} 事件，写库...")
    store.store_index_history_events(all_events, db_path)
    n = store.count_index_events(db_path)
    print(f"✅ 指数历史事件库构建完成: {n} 条")
    by_symbol = {s: sum(1 for e in all_events if e["symbol"] == s) for s in expected}
    print(f"  各指数事件数: {by_symbol}")
    by_type = Counter(e["trigger_type"] for e in all_events)
    print(f"  按触发类型: {dict(by_type)}")

    # 单 trigger_type 全局 0 事件只警告（低频类型属正常，如 bottom_fishing 样本偏少）
    zero_types = set(index_signals.BULLISH_TRIGGER_TYPES) | {"pullback_start", "capital_withdrawal"}
    for tt in sorted(zero_types):
        if by_type.get(tt, 0) == 0:
            print(f"⚠️  触发类型 {tt} 全局 0 事件，该类型概率查询将退化为无样本（低置信度）")


if __name__ == "__main__":
    main()
