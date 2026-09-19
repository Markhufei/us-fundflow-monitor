"""单版块详情：RS 走势 + 历史事件 + 概率查询。"""
import streamlit as st
import pandas as pd

from web import common
from web.rrg_chart import render_flow_indicators

common.init()
st.title("🔍 版块详情")

sym = st.selectbox("选择版块", list(common.SECTORS.keys()),
                   format_func=lambda s: f"{s} ({common.SECTORS[s]})")

# RS 历史走势（双轴，公共组件）
st.subheader(f"{sym} RS 指标历史走势（近120天）")
hist = common.load_sector_history(sym, days=120)
if hist:
    hdf = pd.DataFrame(hist)
    st.plotly_chart(common.render_rs_chart(hdf), width="stretch")
    st.caption(f"数据截至: {hist[-1].get('date', '未知')}")

    # 资金流指标子图（MFI / CMF / vol_zscore）
    st.subheader(f"{sym} 资金流指标（近120天）")
    flow_fig = render_flow_indicators(hdf)
    if flow_fig is not None:
        st.plotly_chart(flow_fig, width="stretch")
    else:
        st.info("该版块无资金流指标数据（MFI/CMF/vol_zscore 均为空）")
else:
    st.info(f"无 {sym} 历史快照")

# 该版块历史事件
events = common.query_history(sym)
st.subheader(f"{sym} 历史事件（{len(events)} 条）")
if events:
    edf = pd.DataFrame(events)
    for col in ["ret_5d", "ret_10d", "ret_20d", "ret_60d"]:
        if col in edf.columns:
            edf[col] = (edf[col] * 100).round(2)
    show = ["event_date", "trigger_type", "rs_momentum", "vol_zscore",
            "ret_5d", "ret_10d", "ret_20d", "ret_60d", "duration_days"]
    st.dataframe(edf[[c for c in show if c in edf.columns]], width="stretch", hide_index=True)
else:
    st.info(f"无 {sym} 历史事件")

st.markdown("---")

# 概率查询
st.subheader("概率查询")
st.caption("输入假设的 RS-Momentum 值，查历史相似事件（±20%）的持续概率")
rm = st.number_input("RS-Momentum 值", -50.0, 50.0, 5.0, 0.5)
similar = common.query_history(sym, rs_momentum=rm)
if similar:
    sdf = pd.DataFrame(similar)
    n = len(sdf)
    sustained = int(((sdf["duration_days"] >= 10) & (sdf["ret_10d"] >= 0.03)).sum())
    prob = sustained / n * 100
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("相似事件数", n)
    with col2:
        st.metric("持续概率", f"{prob:.1f}%")
    with col3:
        st.metric("平均持续", f"{sdf['duration_days'].mean():.1f} 天")
    with col4:
        st.metric("平均10日超额", f"{sdf['ret_10d'].mean()*100:.2f}%")
    conf = "高" if n >= 30 else ("中" if n >= 10 else "低")
    if conf == "低":
        st.warning(f"置信度: {conf}（样本 {n} < 10，结论仅供参考）")
    else:
        st.info(f"置信度: {conf}（样本 {n}）")
else:
    st.warning(f"无 RS-Momentum ∈ [{rm*0.8:.2f}, {rm*1.2:.2f}] 的历史事件")
