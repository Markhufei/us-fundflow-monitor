"""版块轮动信号页：筛选 + 详情。"""
import streamlit as st
import pandas as pd

from web import common

common.init()
st.title("📡 版块轮动信号")
latest = common.latest_snapshot_date(common.load_snapshots())
if latest:
    st.caption(f"数据截至: {latest}")

days = st.slider("查看天数", 7, 180, 30, help="回看近 N 天的轮动信号")
signals = common.load_signals(days=days)

if not signals:
    st.info(f"近 {days} 天无轮动信号")
    st.stop()

df = pd.DataFrame(signals)
# probability 可为 None（事件库无相似样本），显 "N/A" 而非 nan
df["概率%"] = df["probability"].apply(
    lambda p: f"{p*100:.0f}%" if pd.notna(p) else "N/A")

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
    title = f"{r['date']} | {r['from_sector']} -> {r['to_sector']} | 概率 {r['概率%']}"
    # 概率/持续/波动可为 None（无历史相似样本），缺值显 N/A
    p = r["probability"]
    p_str = f"{p*100:.1f}%" if pd.notna(p) else "N/A"
    dur = r["avg_duration"]
    dur_str = f"{dur} 天" if pd.notna(dur) else "N/A"
    amp = r["avg_amplitude"]
    amp_str = f"±{amp:.2f}%" if pd.notna(amp) else "N/A"
    with st.expander(title):
        st.markdown(f"""
- **消息**: {r['message']}
- **持续概率**: {p_str}
- **历史平均持续**: {dur_str}
- **历史平均波动**: {amp_str}
- **样本量**: {r['sample_size'] if pd.notna(r['sample_size']) else 0}
- **置信度**: {r['confidence']}
- **严重度**: {r['severity']}
""")
