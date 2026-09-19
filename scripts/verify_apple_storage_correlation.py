"""苹果 ↔ 存储 相关性验证脚本（一次性，复用 twelvedata collector，不改生产架构）

目的：判断苹果(AAPL) 与存储标的(MU/SNDK/SOXX) 的净效应是
      负相关(跷跷板:成本端对冲占优) 还是 正相关(同向:需求端占优)。

数据源：Twelve Data time_series (免费版, outputsize 上限 500 ≈ 2年日线)
运行：cd /Users/huangshifei/us-fundflow-monitor && .venv/bin/python scripts/verify_apple_storage_correlation.py
"""
import os
import sys
from pathlib import Path

# 加载 .env（Twelve Data key）—— Streamlit/cron 外独立运行需手动 source
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

# days=outputsize，免费版上限 500（约 2 年交易日 252×2≈504）
DAYS = 500
SYMBOLS = {"AAPL": "苹果", "MU": "美光(存储)", "SNDK": "闪迪(存储)", "SOXX": "半导体ETF"}
SRC = "Twelve Data time_series 1day"
FETCH_TS = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")

print(f"=== 数据采集 (源: {SRC}, 拉取时间: {FETCH_TS}) ===")
series = {}
for sym in SYMBOLS:
    data = twelvedata.collect_price_volume(sym, DAYS)
    if not data:
        print(f"  {sym}({SYMBOLS[sym]}): 拉取失败/空")
        continue
    s = pd.Series({d["date"]: d["close"] for d in data}, name=sym)
    s.index = pd.to_datetime(s.index)
    s = s.sort_index().dropna()
    series[sym] = s
    print(f"  {sym}({SYMBOLS[sym]}): {s.index.min().date()} ~ {s.index.max().date()}  ({len(s)}条)")

if "AAPL" not in series or ("MU" not in series and "SOXX" not in series):
    print("\n[终止] 苹果或存储标的数据缺失，无法算相关性。检查 TWELVEDATA_API_KEY / 网络。")
    sys.exit(1)

df = pd.DataFrame(series).dropna()
print(f"\n共同交易日: {df.index.min().date()} ~ {df.index.max().date()} ({len(df)}条)")

ret = df.pct_change().dropna()
print(f"\n=== 日收益率全样本相关矩阵 ({len(ret)}日) ===")
print(ret.corr().round(3).to_string())

print("\n=== 分年相关性 ===")
print(f"  {'年份':<8}{'样本':<6}{'AAPL-MU':<10}{'AAPL-SOXX':<10}{'AAPL-SNDK':<10}")
for year, g in ret.groupby(ret.index.year):
    if len(g) < 20:
        continue
    def c(a, b):
        return f"{g[a].corr(g[b]):.3f}" if a in g and b in g else "  -  "
    print(f"  {year:<8}{len(g):<6}{c('AAPL','MU'):<10}{c('AAPL','SOXX'):<10}{c('AAPL','SNDK'):<10}")

print("\n=== 60日滚动相关性 (捕捉时变关系) ===")
for b in ["MU", "SOXX", "SNDK"]:
    if b not in ret or "AAPL" not in ret:
        continue
    roll = ret["AAPL"].rolling(60).corr(ret[b])
    roll = roll.dropna()
    if roll.empty:
        continue
    print(f"  AAPL-{b}: 最新={roll.iloc[-1]:+.3f} 均值={roll.mean():+.3f} "
          f"区间[{roll.min():+.3f}, {roll.max():+.3f}] "
          f"负相关占比={ (roll<0).mean()*100:.0f}%")

print("\n=== 结论判定 (基于 AAPL-MU 全样本，含 S0XX 佐证) ===")
verdicts = []
for b in ["MU", "SOXX", "SNDK"]:
    if b not in ret:
        continue
    c = ret["AAPL"].corr(ret[b])
    if c < -0.2:
        v = f"负相关(跷跷板倾向)"
    elif c > 0.2:
        v = f"正相关(同向)"
    else:
        v = f"弱相关(关系不稳)"
    verdicts.append((b, c, v))
    print(f"  AAPL-{b}: r={c:+.3f} -> {v}")

# 主判定取 MU（个股对个股，最能反映苹果↔存储成本跷跷板）
if verdicts:
    b, c, v = verdicts[0]
    if "负相关" in v:
        concl = "净效应为负相关 —— 存储涨价时苹果承压的跷跷板逻辑在数据上成立，可设计子行业轮动/配对策略。"
    elif "正相关" in v:
        concl = "净效应为正相关 —— 需求端同涨同跌占优，跷跷板逻辑不成立，不宜做空一边做多另一边。"
    else:
        concl = "净效应弱相关 —— 关系时变，需分段/滚动信号驱动，静态配对不可靠。"
    print(f"\n>> {concl}")
    print(f">> 数据来源: {SRC} | 拉取: {FETCH_TS} | 样本: {len(ret)}日")
