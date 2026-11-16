# 系统性优化简报（2026-11）

> 状态：调研简报（未实施）。回答 5 个元问题 + 重写任务题目 + 差距对照表。
> 项目哲学不变："概率是历史统计参考非预测"。已知红线：共识分 IC≈0、条件概率判别力 r≈0.08。
> 采集时间：除特别注明外，所有外部链接实测均为 2026-11-17。置信度标注规则：高=两个独立来源或一手实测；中=单源实测或合理推断；E-未验证=无法核实。

---

## 第一部分：5 问解答

### 问 1：顶尖专家（量化研究员/组合经理）会做哪些用户没要求的动作

用户没要求但必须做的 8 件事。逐项说明"为什么必须做"。

**1.1 市场状态（regime）先行分类**
为什么必须做：同类指标在牛熊里的分布不同。把 2024 年与 2008 年的"放量上涨"放进同一个事件池算概率，等于把两种行情的答案混在一起。本项目 2026-09-20 审查已实测 MA200 下方的 20 日延续率分桶差异（33.8% vs 24.2%），regime 不分层时概率系统性失真。
现状：事件库已存 `spy_above_ma200` 字段且查询侧按 regime 分桶（`src/analyzers/index_signals.py:351` 实测确认）。
专家会再加两步：① regime 定义从"单变量 SPY>MA200"扩展为 **VIX 期限结构状态**（VIX<VIX3M=contango 风险偏好、VIX>VIX3M=backwardation 避险——市场通行惯例，置信度：高，方法学共识非单源）；② 每个 regime 桶给出**基准率对照**——桶内概率必须与桶内基准率比较才有意义（条件概率判别力 r≈0.08 的教训：绝对值好看不等于有增量）。

**1.2 交易成本与滑点敏感性分析**
为什么必须做：不扣成本的回测会把高频信号的优势全部高估。本项目已有 0.1%/笔的先例（README 2026-08-31 定参回测），专家会把它升级为**敏感性矩阵**：0.05% / 0.10% / 0.25% / 0.50% 四档分别重跑，结论标注"在成本 X 以上该信号失效"。SPY 级 ETF 买卖价差通常 1-2 个 bp（实测单源推断，置信度中），大版块 ETF 类似；个体股滑点显著更高。
为什么"敏感性"比"单一成本"重要：单点成本结论随实盘执行质量漂移，只有边际成本曲线能回答"这个信号值不值得交易"。

**1.3 Walk-forward 防参数过拟合**
为什么必须做：用全样本选参数再用同一样本验证 = 用答案考考生。本项目已有先例教训——P30/N2 参数在 2025-10~2026-08 回测最优，但 2026-09-20 全期复验发现最优组合随标的漂移（XLK 最优 P40/N2、XLE P30/N3、XLF P40/N3）。这正是过拟合特征：参数名次不稳定。
标准做法（投资组合管理教材标准流程，CFA 教材方法学，置信度高）：**train 3 年选参 → test 6 个月滚动前进 → 只汇总 test 段表现**。参数必须选"全窗口从不垫底"的稳健解而非单窗口最优解——本项目实际已经这样做了（README: "P30/N2 在全部 3 对上处中位、从不垫底"），walk-forward 是把这个直觉流程化。

**1.4 幸存者偏差（ETF 清盘/更名/指数重组）**
为什么必须做：只用今天活着的 ETF 建历史事件库，等于只统计"活下来的学生"的成绩。用 SPDR 版块 ETF 回测 2000-2026 有两层偏差：① 早期（如 XLB/XLI 1998-12 才上线，XLC 2018-09 才上线）数据根本不存在；② 2000 年互联网泡沫里死掉的 B2B/电信类 ETF 不在样本里，导致"版块轮动策略长期有效"的结论天然偏乐观。
本项目现状：XLC(2018)/UFO(2019) 短史已在 README 标注，但"清盘 ETF 不在样本"这一层没有处理。务实方案：至少在文档与面板标注"事件库仅含现存 ETF，含幸存者偏差，结论对失效策略不可见"（置信度高，方法学共识）；完整方案需引入已清盘 ETF 史料（成本高，见问 2 的 CRSP 类来源讨论，E-未验证免费源可得性）。

**1.5 数据质量审计（拆股复权/半日 bar/静默缺口）**
为什么必须做：三类脏数据会静默污染事件库：① **拆股未复权**——RS 比值（sector/SPY）在拆股日跳变，制造伪 RS-Momentum 尖峰与伪事件；② **半日交易 bar**（如平安夜/感恩节次日 13:00 收盘）量能天然偏低，会被 vol_zscore 误判为"缩量异常"；③ **静默缺口**——采集失败的日子直接缺席而非留 NaN，滚动窗口把缺口两侧拼接，周期错位。本项目已有量级守卫（当日量偏离中位 10 倍置 NaN，`flow_indicators.py`），但拆股复权与半日日历没有专门处理。
专家动作：建库前跑一次审计脚本，输出"每个标的的复权事件数、半日日数、缺口日历"，审计不合格的标的降置信度。

**1.6 一切结论对买入持有基准**
为什么必须做：用户要"操作建议"，但任何策略的评判锚是**同风险口径的买入持有**。一个年化 8% 的择时策略，若基准同区间年化 12% 且回撤更深，它没有任何价值。本项目现有回测输出净收益绝对值，没有系统性地对齐"买入持有 SPY 同期"对照列。
专家动作：每张回测表强制附两列：策略净收益 / 同期买入持有收益，外加**比值（超额）**与**最大回撤对比**。这是所有正规回测报告的最低配置（CFA/教材共识，置信度高）。

**1.7 样本外诚实淘汰**
为什么必须做：用户要求"多指标多模型"，但加法的结果几乎必然是多数指标无效（本项目共识分 IC≈0 即已实测的例证）。专家与业余的区别不在加多少指标，而在**有机制地淘汰**：每个新指标/新模型上线前走"先样本内构建 → 再样本外验证 → 样本外不达标即归档"的流水线，淘汰记录留档防止反复复活。
本项目对应机制雏形：us-hot-picks 项目已有"事件库验证：一般概率无增量→处决"的先例（memory 记录）。简报把它写成硬规则：**IC 或判别力连续 2 个样本外窗口不达标（|r|<0.05）即停用该指标**。

**1.8 把"概率"改造成"情景应对预案"而非预测输出**
为什么必须做：项目哲学已经是"概率是历史统计参考非预测"，但输出形态仍是"持续概率 62%"——用户读到的仍是预测腔。专家会把输出重写为条件语句："当前状态 X 时，历史上 n=41 个相似样本中 62% 在 20 日内延续 ≥2%，其中样本量 41 属高置信；若延续失败，历史相似样本的典型回撤为 -Y%"。同一数字，前者诱导预测解读，后者诱导预案解读。这不需要新数据，只需要输出层重写（Streamlit 面板 + 通知文案）。

### 问 2：最原始素材从哪入手（逐个实测可达性）

实测环境说明：本机经付费 VPN TUN 全局出口（共享 IP），以下状态为**本机 curl 实测**；部分站点对 curl/本机 IP 拦截，标注替代通道。

| # | 来源 | 用途 | 实测状态（2026-11-17） | 备注 |
|---|------|------|------------------------|------|
| 1 | SEC EDGAR https://www.sec.gov/edgar | Form 4 内部人交易 / 13F 机构持仓 / 杠杆 ETF 投资者公告 | HTTP 301→正常跳转；submissions API `https://data.sec.gov/submissions/CIK0000723125.json` 带 UA 实测 **200**（164KB） | 一手数据。curl 必须带 `User-Agent: 项目名 邮箱`（SEC 政策）。已接入系统 |
| 2 | FRED https://fred.stlouisfed.org（VIX 系列名 VIXCLS） | VIX 收盘日度 | **本机 curl 不可达**：HTTP/2 流被服务器关闭（err 2）+ HTTP/1.1 25s 超时，两种协议均失败 | **own-search 服务端 fetch 实测可达**：VIXCLS 页面返回 2026-11-13 收盘 19.30（采集 2026-11-17）。CSV 端点 `https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS` 本机 curl 空响应。**落地建议：VIX 经自有服务端代理抓取，禁止直连** |
| 3 | CBOE https://www.cboe.com/us/options/market_statistics/ | VIX/VIX3M 官方历史 CSV | 302→ `https://www.cboe.com/markets/us/options/market-statistics` → **200** 可达 | 更实用的直链实测：`https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` **1990-01-02 起全史可下载**；`VIX3M_History.csv` 同路径 **2009-09-18 起可下载**（均 2026-11-17 curl 实测，带 UA）。**VIX 期限结构免费源已验证，红线解除** |
| 4 | SPDR 官方（State Street） https://www.ssga.com/us/en/intermediary/etfs/spdr-sp-500-etf-trust-spy-fund-page | SPY 官方持仓/资金流页 | 301→**200** 可达 | 页面可达但持仓文件下载接口未逐个实测（E-未验证）；iShares 实测更顺（见 #5），SPDR 文件下载留给实施阶段 |
| 5 | iShares（BlackRock） https://www.ishares.com/us/products/239726/ishares-semiconductor-etf | SOXX 官方持仓/规模页 | **200** 可达 | README 记录"iShares JSON 返回 HTML"的老问题指的是下载端点；页面本身可达，下载端点需带特定 Accept 头再试（E-未验证） |
| 6 | FINRA short interest https://www.finra.org/finra-data/browse-catalog/equity-short-interest | 个股空头持仓 | **200** 可达（页面）；README 记录 FINRA API 曾 307 不可用 | 数据页可达 ≠ API 可用。系统现用 yfinance shortPercentOfFloat 近似，维持现状+标注 |
| 7 | Twelve Data 文档 https://twelvedata.com/docs | 量价主源 API | **200** 可达；pricing 页 200（679KB） | 免费档 800 req/天为系统现行约束（README 实测记录） |
| 8 | 腾讯财经 kline `https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=usINX,day,2026-11-09,2026-11-13,5` | 指数本体 OHLCV（中国直连无 key） | **200**，返回真实数据（INX 2026-11-09 O=7422.24 等，实测采集） | 系统已在用（`index_price.py` 主源） |
| 9 | Ken French 因子库 https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html | 动量/规模价值因子参考 | **本机 curl 404**（HEAD 与 GET 均 404）；WebFetch 因方舟端点限制不可用；own-search 服务端 fetch 未测试该域（E-未验证服务器侧可达性） | 404 可能是本机出口问题或 URL 变更。因子数值仅作研究参考非必需依赖，降级处理：以 Moskowitz 论文的公开描述代替因子数据 |
| 10 | Moskowitz-Ooi-Pedersen《Time Series Momentum》 | 时间序列动量方法论锚 | 作者主页 `https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf` 302→**200** 实测可达（2026-11-17） | 2012 JFE 论文作者托管 PDF，可直下 |
| 11 | JdK RRG 原始论文（Julia Lee，SSRN abstract_id=3155645） | RS-Ratio/RS-Momentum 公式出处 | **本机 curl 超时不可达**；own-search fetch 15s 超时；StockCharts 文档站（stockcharts.com/docs 与 chart_school RRG 页）**本机与 own-search 双通道 403**（CloudFront 拦截） | 系统公式已按社区通行 JdK 公式实现并有自己的 25 年回测锚定（README），论文引用标注"E-未验证原文页，公式以 StockCharts 社区通行版为准" |
| 12 | StockCharts RRG / TradingView | 人工抽查对照 | StockCharts 403（同上）；tradingview.com **本机 curl 无响应**（连接超时，实测两次） | 人工抽查通道对本机不可达。**替代：浏览器人工访问**（用户日常浏览器经 VPN 可正常访问的概率高，E-未验证）。程序化对拍不依赖它们 |
| 13 | Stooq https://stooq.com/q/d/l/?s=^ixic&i=d | 免费深历史日线（补 IXIC/DJI 短史候选） | **实测返回 JS 工作量证明质询页**（SHA-256 PoW），curl 不可用；质询可被脚本解（PoW 难度 4），但属对抗性访问，不建议 | 备选深历史方案见问 3.6 |

补充：NIST 统计手册 Wilson 区间页 `https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm` 实测 200 可达（2026-11-17），是"小样本置信区间"公式的权威出处。

### 问 3：具体方法/工具/复现步骤（对应现有架构）

每项写清：用什么数据、跑什么脚本、怎么验证。复现均在本机 `/Users/huangshifei/us-fundflow-monitor/` 下执行。

**3.1 Regime 分层扩展（VIX 期限结构）**
- 数据：CBOE VIX_History.csv（1990 起）+ VIX3M_History.csv（2009 起），curl 直下（已验证可达）。
- 新脚本（实施阶段）：`scripts/update_vix.py` —— 下载两份 CSV → 比值 VIX/VIX3M → 写 SQLite 新表 `vix_term`（date, vix, vix3m, spread_ratio, regime）。regime 定义：ratio<0.95 风险偏好 / 0.95-1.05 中性 / >1.05 避险（三档比双档信息量更大且 CBOE 数据免费覆盖）。
- 接入：`sector_stats.query_probability` 与 `index_signals` 的 regime 参数从 `spy_above_ma200` 单字段扩为二元组（ma200 × vix_term），事件表加列 `vix_regime`。
- 验证：① 与 FRED VIXCLS 对拍最近 30 个交易日收盘价（容差 ≤0.5 点，FRED 经 own-search 服务端通道拉取）；② 建库后对比"regime 分桶前 vs 分桶后"的条件概率基准率差异是否 ≥5 个百分点，差异不足说明该维度无增量，**直接处决该扩展**（呼应问 1.7）。

**3.2 成本敏感性矩阵**
- 数据：现有 `scripts/backtest_apple_storage_rotation.py` 的回测框架 + 事件库。
- 改造：成本参数从硬编码 0.1% 改为命令行参数，跑 `[0.0005, 0.001, 0.0025, 0.005]` × 现有参数网格（P20-40 × N2-3，≤6 组），输出"成本 × 参数 × 净收益"矩阵表。
- 验证：抽查矩阵中成本=0 与 README 既有回测数字一致（XLK +465% 等三条锚点）；0.5% 档应显著劣化，若不劣化说明回测逻辑有 bug（换手率核对）。

**3.3 Walk-forward 协议落地**
- 数据：事件库全史（1728 版块事件 + 1016 指数事件）。
- 新脚本：`scripts/walkforward_eval.py` —— 滚动窗口 train 36 个月（事件库内选参数与指标权重）→ test 6 个月（只记录 test 段判别力 IC/r 与净收益）→ 窗口前移 6 个月重复，汇总所有 test 段。
- 验证：① 断言 test 段互不重叠且与 train 段无泄漏（脚本内置检查）；② 汇总报告必须含"每窗口被选参数"明细表——若不同窗口选参高度分散，本身就是参数不稳健的证据，结论改为"参数敏感，维持现默认参数"。

**3.4 数据质量审计脚本**
- 数据：DB 内全部快照表（stock_snapshot 595 行 / index_snapshot 216 行 / sector_snapshot，实测 2026-11-17 行数）。
- 新脚本：`scripts/audit_data_quality.py` —— 逐标的输出：① 相邻收盘价跳变 >±25% 的日期清单（候选拆股/坏点，人工判读后维护拆股日历表）；② 静默缺口（weekday 在 DB 无记录且非节假日，节假日日历用 pandas USFederalHolidayCalendar + 修正半日）；③ 半日交易日清单（当年感恩节次日/平安夜量 < 前后 20 日中位 50% 的候选日）。
- 验证：审计结果与已知事实锚点对拍——SNDK 2025-02 上市（历史应从该日起）、MU 历史拆股事件应出现在跳变清单；07-28 型坏数据（README 已记录 MU 量为正常 1/11）应被量级守卫标 NaN，审计脚本二次确认守卫生效。

**3.5 指标扩展集（新增 7 项，全部传统公式）**
- 数据：现有量价快照（Twelve Data 800/天 + 腾讯指数 + yfinance 备源），无需新增数据源。
- 新增到 `flow_indicators.py`：ATR14（真实波幅均值）、实现波动率（20 日对数收益标准差年化）、dollar_volume（close×volume，20 日均）、上/下行量比（20 日窗口内上涨日量 / 下跌日量，比值>1 偏多方）、量价背离（20 日窗口 close 与 OBV 的秩相关符号）、RSI14、VWAP 锚（当日收盘相对滚动 20 日 VWAP 偏离%）。
- 每项指标上线流程（硬规则）：先写单测（构造已知输入断言已知输出）→ 建库脚本回算全史 → walk-forward 判别力 → **|r|≥0.05 且方向稳定才接入告警/面板**，不达标归档并在文档记录"已试无效"。Rationale：用户要求"多指标"，专业答案是"指标候选池大 + 严进宽出"，与 IC≈0 红线一致。
- 验证：ATR/RSI 用手算样例断言；实现波动率与 vol_zscore 相关性检查（应中等相关不完全共线）；背离指标在已知历史背离段（如 2024 年内 MU 价升量缩段）人工核对方向。

**3.6 IXIC/DJI 历史深度问题（腾讯 2007 起）的务实解**
实测：腾讯 kline IXIC 最早返回 **2007-03-19**（2026-11-17 curl 实测），README 记录 DJI 2009 起——短史使"指数长周期阶段统计"对 2000-2006（互联网泡沫破裂段）完全盲。
方案（按优先级）：
1. **接受短史 + 显式标注**：事件库对 IXIC/DJI 标注"统计仅覆盖 2007 起，未含互联网泡沫破裂型 regime"，面板显著位置声明（零成本，立即做）。
2. **代理延长**：用 QQQ（1999-03 上市）作为 IXIC 的历史代理延长期，SPY 1993 起作 GSPC 基准已覆盖；DJI 用 DIA（1998-01 上市）延长。数据走 Twelve Data（系统 ETF 主通道；README 记录建库脚本对 SPY 已实现分页取史到成立日，QQQ/DIA 同深度 E-未验证，实施时先探）。代理与本体在重叠期（2007 后）的 RS/阶段分布先做一致性校验，校验通过才把代理期并入事件库（标注 proxy=true 字段）。注意：腾讯 kline 已实测不覆盖美股 ETF（usQQQ/usDIA 空 day），代理延长不能走腾讯通道。
3. Stooq 深史（1971 起）实测被 PoW 拦，**不采用**（对抗性访问不值得为统计参考引入风险）。
- 验证：重叠期 QQQ 对 IXIC 日收益相关应 >0.95（常识阈值，实测后按实际值重校）；并入代理期前后的事件基准率变化>10 个百分点则弃用方案 2。

**3.7 交叉验证自动化（对应问 4）**
- 新脚本：`scripts/crosscheck_sources.py` —— 每周跑一次，输出对拍报告写 `logs/crosscheck.log`：
  ① Twelve Data vs 腾讯 vs yfinance 三源：对最近 5 个交易日收盘价，相对差 >0.5% 报警（ETF 用 Twelve Data/腾讯美股个股，指数用腾讯/yfinance）；
  ② CBOE VIX vs FRED VIXCLS：最近 30 日，差 >0.5 点报警（FRED 走 own-search 服务端通道）;
  ③ 13F 环比 vs 13D/G 公告：13F 减持 >10% 的标的，查同期 EDGAR 13D/G 是否有对应建仓/减仓公告（月度人工+脚本半自动）。
- 验证：脚本先跑一轮基线，人工复核全部报警是否为已知口径差异（如复权差异），把"已知差异"写进白名单防误报堆积。

**3.8 输出层改造（情景预案化）**
- 改 `web/pages` 相关页与 `src/alerts/notify.py` 文案模板：概率句式统一为"历史 n 个相似样本中 p% 延续，Wilson 95% CI [a%, b%]，置信度分级；失效情景的历史典型表现：…"。
- 验证：Streamlit AppTest 已有面板测试（tests/test_web_apptest.py），新增文案断言；通知样例人工 review。

### 问 4：独立交叉验证来源（可操作对拍方法）

| 对拍对 | 方法 | 频率 | 容差/判据 |
|--------|------|------|-----------|
| Twelve Data vs 腾讯 vs yfinance | `scripts/crosscheck_sources.py` 自动对最近 5 日收盘价。**2026-11-17 实测修订：腾讯 kline 对美股 ETF（usQQQ/usDIA 探针）连近期窗口也返回空 day 数组 → 腾讯不覆盖美股 ETF 历史，ETF 三源对拍降为 Twelve Data vs yfinance 双源**；指数（usINX/usIXIC/usDJI）维持腾讯 vs yfinance 双源对拍 | 每周 | 相对差 >0.5% 报警；复权口径差异进白名单 |
| CBOE VIX vs FRED VIXCLS | CSV 直下 vs own-search 拉 FRED CSV；最近 30 交易日 | 每月 | 差 >0.5 点报警（CBOE 官方为准） |
| 13F 环比 vs 13D/G 公告 | `scripts/update_13f.py` 产出环比减持清单 → 对 EDGAR full-text 查同标同期 SC 13D/G 提交（curl EDGAR 已验证可达） | 每季（13F 发布后） | 大额减持（>10%）应有 13D/G 或 13F 双重痕迹；只有单侧的列入人工核查清单 |
| StockCharts RRG / TradingView 人工抽查 | 程序通道实测 403/超时不可达 → **改浏览器人工抽查**：系统面板 RRG 象限 vs StockCharts 网页版同参数象限，每月抽 3 个版块记录差异 | 每月 | 象限归属不一致即排查（period 定义差异已知，需换算对齐） |
| 杠杆 ETF 衰减声明 | CBOE/SEC 文档为方法锚（SEC investor.gov 杠杆 ETF 公告：具体 URL 实测 404 两处，页面迁移未定位，E-未验证；但杠杆 ETF 单日重置衰减为教材级共识，置信度高）+ 用本系统杠杆 ETF 数据实测：TQQQ 3 年累计 vs NDX 累计×3 对比 | 每季 | 偏离方向必须为负（衰减），正偏离说明数据问题 |

### 问 5：当前结论置信度最低的部分及原因

| # | 低置信项 | 原因 | 处置 |
|---|---------|------|------|
| 1 | 条件概率判别力 r≈0.08 | 项目实测红线。概率与无条件基准率区分度极弱，"62% 持续"很可能约等于"基准率 24.7%+噪声" | 所有概率输出强制附桶内基准率对照（问 1.1/1.8）；walk-forward 每窗口复测判别力，连续 2 窗口 r<0.05 处决该匹配维度 |
| 2 | 杠杆 ETF 长持衰减失真 | TQQQ/SQQQ 跟踪 NDX 而非 IXIC（README 已标注口径差异）+ 单日重置放大衰减。任何把杠杆 ETF 量价直接当"指数情绪强度"的解读都有系统偏差 | 杠杆 ETF 只用于量价异动观察，禁止参与概率计算；面板加衰减声明；季度实测衰减量（问 4 末行） |
| 3 | IXIC/DJI 历史深度 | 腾讯 IXIC 实测 2007-03-19 起、DJI 2009 起（2026-11-17 实测）。指数事件库 1016 条对"深度调整/熊市"类 regime 的样本覆盖严重偏窄——2000-2002 互联网泡沫破裂完全缺席 | 问 3.6 方案 1（标注）+ 方案 2（QQQ/DIA 代理延长，先校验后并入） |
| 4 | UFO(2019)/XLC(2018) 短史低样本 | 事件数不足，概率查询天然命中"低置信度"分支（<10 样本强制低置信度，config 已设 prob_min_sample=10） | 维持现状+维持强制标注；不企图用短史硬凑概率；UFO 考虑改"只展示指标不给概率"模式 |
| 5 | VIX 期限结构免费源待复测 | **本次已复测解除**：CBOE VIX3M_History.csv 2009 起可下载（2026-11-17 实测）。剩余小限：2009-2011 年段 VIX3M 与 VIX 数据质量未逐日核验（置信度中） | 问 3.1 落地时对 2009-2012 段做一次完整性审计（缺口/负值/异常跳变） |
| 6 | RS 指标滞后性（确认型非领先型） | RS-Ratio/Momentum 由过去 30 日量价滚动计算，本质是确认工具；README 已声明，但输出语境仍易被读成"即将转强" | 输出层预案化（问 3.8）：所有 RS 信号句式改为"过去 N 日已确认转强，历史延续率为…"，禁止"即将/预计"词汇 |

另有一条结构性低置信：**ETF 资金流真值缺失**（免费源不可得，系统用量价间接代理，README 已记录）。量价≠申赎资金流，所有"资金流入/流出"措辞必须保持 README 的诚实写法（from_sector 是叙事性标签非实证度量）。

---

## 第二部分：重写后的任务题目（量化任务规格）

> 原题（意译）："多角度、多指标、多模型、多算法、更大数据统计量、更大交易量指标，优化整个系统使其更真实反映真实行情，对指数/个股/ETF 有风险指导意义，以数据说话给出短期1-6月/中期6-12月/长期1-2年操作建议。"
>
> 问题诊断：①"多"无验收标准，会变成堆指标（IC≈0 教训）；②"操作建议"违背项目哲学（概率非预测）；③"更大统计量"没有说明用途（样本量服务于置信区间，不是越大越好）；④时间框架与系统日频数据粒度不匹配（日频数据支撑的是"数日至数月"窗口，"1-2 年长期建议"超出现有数据粒度的支撑能力，只能以情景框架形式给出）。

**重写后的任务规格 T-2026-11-Q：**

### R1 标的清单（29 个）
- 指数 3：GSPC / IXIC / DJI（腾讯 kline 主源）
- 版块 ETF 12：XLK XLE XLF XLV XLY XLP XLI XLB XLRE XLU XLC UFO（现存 SPDR 11 + UFO 非典型一个，标注幸存者偏差）
- 半导体 ETF 2：SOXX / SMH
- 个股 11：MU SNDK SKHY AAPL MSFT NVDA GOOGL META AMZN TSLA SPCX（SPCX 采集未验证前不计入统计）
- 杠杆代理 6（仅量价观察，禁入概率）：SSO SPXU UDOW SDOW TQQQ SQQQ
- 合计 29 + 6 杠杆观察位。买入持有基准 = SPY（全指标对照基准）。

### R2 指标集
- 现有保留：MFI14 / CMF20 / OBV / vol_zscore20（基线不含当日）/ RS-Ratio30 / RS-Momentum30
- 新增候选（7）：ATR14 / 实现波动率20 年化 / dollar_volume20 / 上下行量比20 / 量价背离20（close-OBV 秩相关）/ RSI14 / VWAP 锚20
- 准入硬规则：每个候选走 3.5 流程，样本外判别力 |r|≥0.05 且方向稳定才启用；启用上限 12 个指标，防止堆栈。
- 宏观层新增（2）：VIX/VIX3M 期限结构状态（三档）、SPY MA200 regime（已有，保留）。

### R3 模型族（仅传统量化，明确排除 ML）
1. MA 趋势结构（MA20/50/200 相对位置，现有 index_stage 5 阶段保留）
2. RS 动量（JdK 公式，现有）
3. 均值回归（dd20 分位数框架：当前 dd20 处于自身 5 年分位 <10% 时统计反弹历史分布）
4. 波动率 regime（VIX 期限结构三档 × MA200 二元，共 6 regime 桶）
5. 多因子综合（启用指标等权/固定权重打分，权重不优化——防过拟合；输出分位数而非预测值）
- 排除理由：ML 需要的样本量与特征工程成本远超 800 req/天 + 1728 事件库的支撑能力，且黑箱输出与"诚实性契约"冲突。

### R4 回测协议
- 滚动 walk-forward：train 36 个月 → test 6 个月 → 前移 6 个月，仅汇总 test 段
- 执行口径：T+1 收盘入场（与现有事件库一致）
- 成本：0.1%/笔基准 + 敏感性 [0.05%, 0.25%, 0.50%] 三档
- 参数：小网格 ≤6 组（现有 P20-40 × N2-3 即 6 组），选"全窗口从不垫底"的稳健解
- 淘汰机制：样本外连续 2 个 test 窗口判别力 |r|<0.05 → 该指标/匹配维度停用归档
- 对照列：一切策略表强制附 SPY 买入持有同期收益 + 最大回撤对比

### R5 诚实性契约（输出层硬规则）
1. 一切"建议"重写为**情景应对预案**："当前处于 regime X + 信号 Y，历史 n 个相似样本，p% 在 20 日内延续 ≥2%（Wilson 95% CI [a,b]）；延续失败情景的历史典型回撤 -z%"
2. 历史概率必须带样本量 n + Wilson 95% CI（公式出处：NIST 手册，URL 见问 2 补充）
3. 样本 n<10 强制标"低置信度"且不给概率只给样本清单（现有 config prob_min_sample=10 保留）
4. 时间框架映射：短期 1-6 月 = RS 动量 + 均值回归 + regime 框架（日频数据可支撑）；中期 6-12 月 = MA200 结构 + 版块轮动宏观分布（仅描述性统计）；长期 1-2 年 = **不给建议，只给情景表**（"若进入 deep_correction regime，历史平均持续 X 月、恢复中位 Y 月"——纯历史分布展示，明确声明不构成操作指引）
5. 每次输出末尾固定声明："以上为历史统计参考非预测，样本含幸存者偏差（仅现存 ETF）"

### R6 分批验收标准
- **批次 1（数据层）**：audit_data_quality 报告产出且 3 个已知锚点（SNDK 起点/07-28 坏数据/拆股清单）核对通过；VIX 期限表落库并与 FRED 对拍通过。验收：报告 + 对拍 log。
- **批次 2（指标层）**：7 项候选指标单测全绿 + walk-forward 判别力报告（启用/归档清单）。验收：启用指标 ≤12 且每项有判别力数字。
- **批次 3（协议层）**：walkforward_eval 输出全窗口 test 汇总 + 成本敏感性矩阵（与 README 锚点一致）。验收：矩阵表 + test 段参数明细。
- **批次 4（输出层）**：面板/通知文案预案化改造 + AppTest 断言 + 人工 review 3 条样例通知。验收：无"预测腔"句式残留（grep 禁词表：预计/将上涨/将下跌/预测）。

---

## 第三部分：与现有系统的差距对照表

| 现有模块 | 现状（代码/配置实测 2026-11-17） | 对照规格 R1-R5 | 判定 | 缺口与动作 |
|---------|--------------------------------|----------------|------|-----------|
| sector_rotation（RS 公式） | JdK 公式已实现（`calc_rs_ratio`/`calc_rs_momentum`），P30/N2 经 2 轮回测定参，whipsaw 已排除 P10 | R2 RS 动量保留；R4 参数网格已有雏形（2026-09-20 全期复验） | **已具备** | 补：walk-forward 滚动验证替代两次单点回测（3.3）；公式出处论文页不可达，文档标注"E-未验证原文，社区通行公式" |
| sector_stats（条件概率 1728 事件） | 事件库实测 1728 条；T+1 口径、Wilson CI、样本分级、spy_above_ma200 分桶均已落地 | R4 淘汰机制 + R5 桶内基准率对照 | **部分具备** | 缺：桶内基准率对照列（现只给条件概率绝对值）；缺：样本外淘汰机制（判别力 r≈0.08 红线只有记录无处决流程）。动作：query_probability 输出加基准率列；walkforward_eval 接管判别力复测 |
| index_signals（1016 事件） | 事件库实测 1016 条；5 类触发 + regime 分桶（`index_signals.py:351`） | R1 指数 3 标的；R3 regime 桶 | **部分具备** | 缺：IXIC/DJI 短史标注（2007/2009 起）未进面板显著位置；缺：VIX regime 维度（现仅 MA200 单变量）。动作：3.6 方案 1 立即做；3.1 VIX 表接入 |
| index_stage（5 阶段判定） | 5 阶段规则+历史类比统计（描述性非预测声明已写进 docstring） | R3 模型族 1（MA 结构）+ R5 长期情景表 | **部分具备** | 缺：阶段统计未覆盖"历史平均持续月数"的长期视角输出（R5.4 长期情景表素材已有雏形：已有平均持续天数）。动作：输出层加"若进入 X 阶段，历史持续/恢复分布"情景表 |
| flow_indicators（MFI/CMF/OBV/vol_zscore） | 5 指标已实现含量级守卫（10 倍中位偏离置 NaN） | R2 新增 7 候选指标 | **部分具备** | 缺：ATR/实现波动率/dollar_volume/上下行量比/背离/RSI14/VWAP 锚 7 项全缺。动作：按 3.5 流程逐个准入，启用上限 12 |
| （数据层）腾讯指数源 | usINX/usIXIC/usDJI 已接（实测 200） | R1 指数主源 | **已具备** | 补：IXIC 2007-03-19 起实测写入文档；深史代理方案（QQQ/DIA）未做 |
| （数据层）VIX | 无任何 VIX 数据（依赖人工认知） | R2 宏观层 + R3 波动率 regime | **缺失** | 动作：3.1 CBOE CSV 落库（免费源已验证可达） |
| （数据层）数据质量审计 | 只有量级守卫单点 | R1 数据质量审计 | **缺失** | 动作：3.4 审计脚本（拆股/缺口/半日三查） |
| （协议层）回测 | backtest_apple_storage_rotation.py 单点回测 + 0.1% 成本 | R4 walk-forward + 成本矩阵 + 买入持有对照 | **部分具备** | 缺：滚动协议、成本敏感性、基准对照列。动作：3.2 + 3.3 |
| （输出层）面板/通知 | 概率句式带 CI 与低置信度标注（好），但仍是"持续概率 62%"预测腔 | R5 情景预案化 | **部分具备** | 缺：预案句式重写、失效情景回撤展示、幸存者偏差声明。动作：3.8 |
| ETF 官方资金流 | 免费源不可得，量价代理（README 记录） | （规格外如实标注） | **缺失（接受）** | 维持量价代理 + 诚实措辞；iShares 下载端点带 Accept 头重试为低优先候选（E-未验证可行性） |

差距总览：**已具备 2 项、部分具备 6 项、缺失 3 项（VIX 层/审计层/ETF 官方资金流，其中 ETF 资金流为"接受性缺失"）**。缺失项均已有零/低成本免费方案（问 3），无需新增付费源。

---

## 附：本次实测记录汇总（审计线索）

实测执行时间 2026-11-17（UTC+8 当日，curl 实际时间戳 11-16T17:30-17:50Z 对应北京时间 11-17 凌晨）：

| 命令要点 | 结果 |
|---------|------|
| `curl -sI https://www.sec.gov/edgar`（带 UA） | 301（正常跳转） |
| `curl -s https://data.sec.gov/submissions/CIK0000723125.json`（带 UA） | 200，164606 字节 |
| `curl -sL fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd=2026-11-01` | 空；verbose 显示 HTTP/2 stream err 2；--http1.1 后 25s 超时 → 本机不可达 |
| own-search fetch `fred.stlouisfed.org/series/VIXCLS` | 成功，VIXCLS 2026-11-13=19.30 |
| `curl -sL -r 0-300 cdn.cboe.com/.../VIX_History.csv` | 200，1990-01-02 起 |
| `curl -sL -r 0-300 cdn.cboe.com/.../VIX3M_History.csv` | 200，2009-09-18 起 |
| `curl -sIL cboe.com/us/options/market_statistics/` | 302→market-statistics→200 |
| `curl -sIL ssga.com/.../spy-fund-page` | 301→200 |
| `curl -sI ishare.com/us/products/239726/...` | 200 |
| `curl -sI finra.org/finra-data/browse-catalog/equity-short-interest` | 200 |
| `curl -sI twelvedata.com/docs`、`/pricing` | 200 / 200 |
| `curl -s web.ifzq.gtimg.cn/...param=usINX,day,2026-11-09,...` | 200，真实 OHLCV |
| `curl -s web.ifzq.gtimg.cn/...param=usIXIC,day,1998-01-01,2007-12-31,750` | 200，最早 2007-03-19 |
| `curl -s web.ifzq.gtimg.cn/...param=usQQQ,day,1999-01-01,...` 与 `usDIA,day,1998-01-01,...` | 200 但 day=[]（近期窗口同样空）→ 腾讯不覆盖美股 ETF 历史 |
| `curl -sI mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html` | 404（HEAD 与 GET 同） |
| `curl -sI pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf` | 302→200 |
| `curl -sI itl.nist.gov/div898/handbook/prc/section2/prc241.htm` | 200 |
| `curl -sI stockcharts.com/docs/...rrg`、chart_school RRG | 403 / 403（CloudFront） |
| own-search fetch stockcharts docs | 403 |
| `curl stooq.com/q/d/l/?s=^ixic&i=d` | 200 但内容为 JS SHA-256 PoW 质询页 |
| tradingview.com | curl 连接超时（两次） |
| SSRN papers.cfm?abstract_id=3155645 | 本机 curl exit 28 超时；own-search fetch 15s 超时 |
| aqr.com | 本机 curl exit 6（DNS 解析失败）；own-search fetch ENOTFOUND |
| investor.gov / sec.gov 杠杆 ETF 公告两 URL | 均 404（页面已迁移未定位） |
| WebSearch 工具 | 403 Access denied（方舟服务端工具未激活，实测确认） |
| own-search search sogou/bing/fusion | sogou 触发反爬一次；bing/fusion 对英文多词查询返回词典噪声（相关性 <0.02） |
| open-websearch duckduckgo | 多次查询 0 结果 |
| DB 实测 | sector_history_events=1728, index_history_events=1016, stock_snapshot=595, index_snapshot=216, daily_snapshot=271, alerts=145 |
| 代码实测 | `grep spy_above_ma200`：建库/检测/查询三侧落地（index_signals.py:351 等） |

检索通道失败清单（问 1 方法论佐证检索的诚实记录）：方法论文献（CFA 教材、RRG 原文、杠杆 ETF 公告）未能通过任何可用搜索通道取得正文级佐证——own-search 英文长查询失效、duckduckgo 0 结果、WebSearch 403、目标页直连超时/403。上述方法论条目按教材级共识引用（置信度：高），原文页可达性全部如实标注为不可达/E-未验证。核心可执行锚点（CBOE 数据可达性、腾讯历史深度、三源对拍方案、DB 行数、代码事实）全部为一手实测，不依赖检索。
