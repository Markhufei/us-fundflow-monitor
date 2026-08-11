"""版块轮动信号页：筛选 + 详情。"""
import streamlit as st
import pandas as pd

from web import common

common.init()
st.title("📡 版块轮动信号")

days = st.slider("查看天数", 7, 180, 30, help="回看近 N 天的轮动信号")
signals = common.load_signals(days=days)

if not signals:
    st.info(f"近 {days} 天无轮动信号")
    st.stop()

df = pd.DataFrame(signals)
df["概率%"] = (df["probability"] * 100).round(0)

# 筛选
col1, col2 = st.columns(2)
with col1:
    confs = ["全部"] + sorted(df["confidence"].dropna().unique().tolist())
    conf = st.selectbox("置信度", confs)
with col2:
    involved = sorted(set(df["from_sector"].dropna().tolist() + df["to_sector"].dropna().tolist()))
    sec = st.selectbox("涉及版块", ["全部"] + involved)

mask = pd.Series([True] * len(df))
if conf != "全部":
    mask &= df["confidence"] == conf
if sec != "全部":
    mask &= (df["from_sector"] == sec) | (df["to_sector"] == sec)
view = df[mask].copy().sort_values("date", ascending=False)

st.metric("筛选后信号数", len(view))

show = ["date", "from_sector", "to_sector", "概率%", "avg_duration",
        "avg_amplitude", "sample_size", "confidence", "severity", "message"]
st.dataframe(view[[c for c in show if c in view.columns]], width="stretch", hide_index=True)

st.markdown("---")
st.subheader("信号详情")
for _, r in view.head(20).iterrows():
    title = f"{r['date']} | {r['from_sector']} -> {r['to_sector']} | 概率 {r['概率%']:.0f}%"
    with st.expander(title):
        st.markdown(f"""
- **消息**: {r['message']}
- **持续概率**: {r['probability']*100:.1f}%
- **历史平均持续**: {r['avg_duration']} 天
- **历史平均波动**: ±{r['avg_amplitude']:.2f}%
- **样本量**: {r['sample_size']}
- **置信度**: {r['confidence']}
- **严重度**: {r['severity']}
""")
