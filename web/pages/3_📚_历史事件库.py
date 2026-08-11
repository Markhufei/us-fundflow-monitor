"""历史事件库浏览页：统计 + 分页查询。"""
import streamlit as st
import pandas as pd

from web import common

common.init()
st.title("📚 历史事件库（概率查询基础）")

n = common.count_events()
if n == 0:
    st.warning("事件库为空。请先运行: `PYTHONPATH=. .venv/bin/python scripts/build_sector_event_library.py`")
    st.stop()

# 概览
min_d, max_d = common.event_date_span()
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("事件总数", n)
with col2:
    st.metric("日期跨度", f"{min_d} ~ {max_d}")
with col3:
    st.metric("版块数", len(common.SECTORS))

st.markdown("---")

# 各版块统计
st.subheader("各版块事件统计")
stats = common.history_stats_by_symbol()
st.dataframe(stats, width="stretch", hide_index=True)
st.caption("持续定义: 事件后持续天数>=10 且 10日超额收益>=3%")

st.markdown("---")

# 事件列表（分页 + 筛选）
st.subheader("事件列表")
col1, col2 = st.columns(2)
with col1:
    sym = st.selectbox("版块", ["全部"] + list(common.SECTORS.keys()))
with col2:
    page_size = st.selectbox("每页条数", [50, 100, 200], index=0)
page = st.number_input("页码", 1, 10000, 1)

df = common.query_events_page(symbol=sym if sym != "全部" else None,
                              page_size=page_size, offset=(page - 1) * page_size)

# 收益率转 %
for col in ["ret_5d", "ret_10d", "ret_20d", "ret_60d"]:
    if col in df.columns:
        df[col] = (df[col] * 100).round(2)
df.rename(columns={c: c.replace("ret_", "R") + "%" for c in ["ret_5d", "ret_10d", "ret_20d", "ret_60d"]},
          inplace=True)
st.dataframe(df, width="stretch", hide_index=True)
st.caption(f"显示第 {(page-1)*page_size+1} - {(page-1)*page_size+len(df)} 条")
