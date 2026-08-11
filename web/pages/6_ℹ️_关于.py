"""关于页：数据源、指标、反幻觉声明、使用方法。"""
import streamlit as st

st.title("ℹ️ 关于本工具")

st.header("数据源（免费 + 真实）")
st.markdown("""
- **Twelve Data API**（免费 800 req/天）：11 个 SPDR 版块 ETF + SPY 基准日线量价
- **yfinance**：Twelve Data 限流时的 fallback
- **不使用任何付费数据**（Bloomberg/EPFR 等真实板块资金流为付费，本工具用 SPDR ETF 量价间接推算）
- **不生成模拟数据**：所有数值来自真实行情，概率来自真实历史回测
""")

st.header("监控版块（11 个 SPDR ETF）")
st.markdown("""
| 代号 | 版块 | 代号 | 版块 |
|------|------|------|------|
| XLK | 科技 | XLI | 工业 |
| XLE | 能源 | XLB | 材料 |
| XLF | 金融 | XLRE | 房地产 |
| XLV | 医疗 | XLU | 公用事业 |
| XLY | 可选消费 | XLC | 通信 |
| XLP | 必需消费 | | |
""")

st.header("指标说明")
st.markdown("""
- **RS-Ratio** = `100 × (版块/SPY) / (版块/SPY).rolling(period).mean()`（JdK 标准公式）。>100 表示跑赢大盘
- **RS-Momentum** = `100 × (RS-Ratio / RS-Ratio.shift(period) − 1)`。>0 表示版块强度上升
- **MFI** Money Flow Index / **CMF** Chaikin Money Flow：资金流指标
- **vol_zscore** 成交量 Z-Score：当日量相对均量的偏离
- **RRG 四象限**: 领先 / 改善 / 落后 / 恶化
""")

st.header("轮动信号触发条件")
st.markdown("""
当某版块满足以下任一条件且同时有版块转弱时，触发「A -> B 版块轮动」告警：
1. **momentum_cross**: RS-Momentum 上穿 0 + 量能 Z-Score > 1.5
2. **rs_cross**: RS-Ratio 上穿 100 + RS-Momentum > 0

输出: `from（当日最弱版块）-> to（转强版块）` + 持续概率 + 历史均持续天数 + 波动幅度 + 样本量
""")

st.header("持续概率计算")
st.markdown("""
1. 历史事件库（约 20 年 SPDR 回测）识别所有触发事件
2. 对每个事件，计算事件后 5/10/20/60 日超额收益 + 持续天数
3. 新事件触发时，按同版块 + RS-Momentum 量级相近（±20%）找历史相似事件
4. 持续概率 = 相似事件中「持续≥10天 且 10日超额≥3%」的比例
5. 置信度: 样本 ≥30 高 / 10-29 中 / <10 低
""")

st.header("⚠️ 反幻觉声明")
st.warning("""
- **RS 指标本质是滞后指标**（基于历史量价推算），本工具做「确认 + 分级 + 概率」，**不宣称领先预警**
- 概率反映**历史相似情境的统计规律**，不保证未来重演
- 样本 <10 自动标「低置信度」，请谨慎对待
- 真实板块资金流（Bloomberg/EPFR）为付费数据，本工具用 SPDR ETF 量价**间接推算**，非直接资金流
""")

st.header("使用方法")
st.markdown("""
```bash
# 启动面板
cd /Users/huangshifei/us-fundflow-monitor
.venv/bin/streamlit run web/app.py

# 手动扫描一次（美股收盘后）
PYTHONPATH=. .venv/bin/python src/pipeline.py --mode sector

# 重建历史事件库（季度刷新）
PYTHONPATH=. .venv/bin/python scripts/build_sector_event_library.py

# 装 cron 自动扫描
./cron_setup.sh
```
cron 每日 06:15 自动扫描，轮动信号通过 macOS 通知 + 本 Web 面板双通道输出。
""")

st.header("限制")
st.markdown("""
- Twelve Data 免费 800 req/天，超出需等待次日
- 历史事件库受 Twelve Data outputsize=5000 限制，约 20 年数据
- 概率模型为条件概率统计，非机器学习预测，不含宏观/情绪因子
""")
