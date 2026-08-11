"""美股版块轮动监控 - 总览 Dashboard（首页）。

启动:
    cd /Users/huangshifei/us-fundflow-monitor
    .venv/bin/streamlit run web/app.py

左侧栏自动生成多页面导航：RRG轮动 / 轮动信号 / 历史事件库 / 版块详情 / 回测验证 / 关于
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from web import common
from web.rrg_chart import render_rrg, quadrant

st.set_page_config(page_title="美股版块轮动监控", page_icon="📊", layout="wide")

common.init()

st.title("📊 美股版块轮动监控")
st.caption(
    "数据源: Twelve Data / yfinance（免费） | "
    "概率基于历史事件库回测（约20年 SPDR 数据） | "
    "⚠️ RS 指标为滞后确认，非领先预警；样本<10 标低置信度"
)
st.markdown("👈 左侧栏选择页面：RRG轮动 / 轮动信号 / 历史事件库 / 版块详情 / 回测验证 / 关于")
st.markdown("---")

# 立即扫描按钮（无需终端操作）
col_btn, col_info = st.columns([1, 3])
with col_btn:
    if st.button("🔄 立即扫描版块", type="primary",
                 help="拉取 11 版块 + SPY 最新数据，计算 RS 指标并检测轮动"):
        with st.status("正在扫描版块...", expanded=True) as status:
            try:
                import yaml
                st.write("加载配置...")
                with open("config/watchlist.yaml") as f:
                    config = yaml.safe_load(f)
                st.write("拉取 11 版块 + SPY 数据（Twelve Data，约 10-30 秒）...")
                from src.pipeline import _run_sector
                ok = _run_sector(config, common.DB_PATH)
                if not ok:
                    status.update(
                        label="❌ 扫描失败：SPY 数据获取失败（数据源限流或网络），稍后重试",
                        state="error",
                    )
                else:
                    new_signals = common.load_signals(days=1)
                    snapshots = common.load_snapshots()
                    latest = max((s.get("date") for s in snapshots if s.get("date")), default="?")
                    status.update(
                        label=f"✅ 扫描完成 · 最新日期 {latest} · 今日 {len(new_signals)} 条轮动信号",
                        state="complete",
                    )
            except Exception as e:
                status.update(label=f"❌ 扫描失败: {e}", state="error")
        st.rerun()
with col_info:
    st.caption("点击按钮立即拉取最新版块数据并检测轮动（无需终端）。按钮为手动触发，长期定时仍建议装 cron。")

st.markdown("---")

# 加载数据
snapshots = common.load_snapshots()
signals = common.load_signals(days=30)
n_events = common.count_events()
has_rs = [s for s in snapshots if s.get("rs_ratio") is not None and s.get("symbol") != "SPY"]

# 关键指标卡片
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("监测版块数", len(has_rs))
with col2:
    st.metric("近30天轮动信号", len(signals))
with col3:
    st.metric("历史事件库", n_events)
with col4:
    leading = [s for s in has_rs if quadrant(s.get("rs_ratio"), s.get("rs_momentum")) == "领先"]
    st.metric("当前领先版块", len(leading))

st.markdown("---")

# 左右两栏：RRG 图 + 最新信号
left, right = st.columns([3, 2])

with left:
    st.subheader("版块相对强度轮动图 (RRG)")
    if has_rs:
        fig = render_rrg(has_rs)
        st.plotly_chart(fig, width="stretch")
    else:
        st.warning("暂无 RS 数据。请先运行: `PYTHONPATH=. .venv/bin/python src/pipeline.py --mode sector`")

with right:
    st.subheader("最新轮动信号")
    if signals:
        for r in signals[:5]:
            conf = f" · {r['confidence']}" if r.get("confidence") else ""
            low = " · 低置信度" if r.get("confidence") == "低" else ""
            st.markdown(
                f"**{r['date']}**  {r['from_sector']} → {r['to_sector']}  \n"
                f"持续概率 **{r['probability']*100:.0f}%** · 均持续 {r['avg_duration']}天 · "
                f"±{r['avg_amplitude']:.1f}% · 样本 {r['sample_size']}{conf}{low}"
            )
            st.caption("")
    else:
        st.info("近30天无轮动信号")

st.markdown("---")

# 各版块状态速览
st.subheader("各版块当前状态")
if has_rs:
    df = pd.DataFrame(has_rs)
    df["象限"] = df.apply(lambda r: quadrant(r.get("rs_ratio"), r.get("rs_momentum")), axis=1)
    show = ["date", "symbol", "sector", "象限", "close", "rs_ratio", "rs_momentum", "vol_zscore"]
    st.dataframe(df[[c for c in show if c in df.columns]], width="stretch", hide_index=True)
    st.caption(f"数据日期: {has_rs[0].get('date', '未知')}")
