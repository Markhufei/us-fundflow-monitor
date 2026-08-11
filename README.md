# 美股资金流动监控系统

监控美股标的资金流动，每日扫描 + 2日对比 + 周对比，大资金/机构异动时 macOS 通知 + 本地日志。

## 监控标的

| 代号 | 名称 | 说明 |
|------|------|------|
| MU | 美光科技 | NASDAQ，存储芯片 |
| SNDK | 闪迪 SanDisk | 2025/02 从 WDC 分拆上市 |
| SKHXY | SK海力士 OTC ADR | 韩股 000660.KS 备选（流动性低时 fallback）|
| SOXX | iShares 半导体 ETF | 跟踪费城半导体指数 SOX |
| SMH | VanEck 半导体 ETF | 辅助确认板块资金 |

## 数据源（免费）

| 模块 | 数据源 | 状态 |
|------|--------|------|
| price_volume | Twelve Data（优先）/ yfinance（fallback）| ✅ Twelve Data 接入，免费 800 req/天；yfinance 限流时自动 fallback |
| sec_edgar Form 4 | SEC EDGAR submissions API | ✅ **真实验证**（MU 近30天内部人净卖出 $9208万）|
| 13F 机构持仓 | sec_edgar.collect_inst | ⏳ SEC EDGAR 13F 数据集，数据不足时返回 None 降级 |
| finra_short | yfinance shortPercentOfFloat | ⚠️ 近似（FINRA API 返回 307 不可用）|
| etf_flow | — | ⚠️ 免费源不可得，ETF 通过量价异动间接监控 |

## 安装

```bash
cd /Users/huangshifei/us-fundflow-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Twelve Data API key 配置（量价，免费 800 req/天）

1. 注册: https://twelvedata.com/（邮箱注册无需信用卡）
2. Dashboard -> API Keys 获取 key
3. 配置环境变量:
```bash
export TWELVEDATA_API_KEY=你的key                    # 当前 shell
echo 'export TWELVEDATA_API_KEY=你的key' >> ~/.zshrc  # 持久化
```
4. 验证: `PYTHONPATH=. TWELVEDATA_API_KEY=你的key .venv/bin/python3 src/pipeline.py --mode daily`

未配置 key 时，price_volume 自动 fallback yfinance（限流风险）。

## 使用

```bash
# 每日扫描（美股收盘后）
PYTHONPATH=. .venv/bin/python3 src/pipeline.py --mode daily

# 2日对比
PYTHONPATH=. .venv/bin/python3 src/pipeline.py --mode compare2d

# 周对比
PYTHONPATH=. .venv/bin/python3 src/pipeline.py --mode weekly

# 安装 cron（每日 06:00 daily, 06:30 compare2d, 周一 07:00 weekly）
./cron_setup.sh

# 跑测试
.venv/bin/python -m pytest tests/ -v
```

## 异动规则（阈值见 config/watchlist.yaml）

| 规则 | 阈值 | 级别 |
|------|------|------|
| 成交量异常 | vol_zscore > 1.5（MEDIUM）/ 2.5（HIGH）| MEDIUM/HIGH |
| 价格偏离 VWAP | > 5% / > 7.5% | MEDIUM/HIGH |
| 资金流反转 | \|CMF\| > 0.3 / > 0.45 | MEDIUM/HIGH |
| 机构减持 | 13F 减持 > 10% | HIGH |
| 内部人卖出 | 近30天净卖出 > $100万 | HIGH |
| 短量飙升 | 占比 > 40% | HIGH |

## 当前状态（2026-07-25）

- ✅ price_volume 已切换到 Twelve Data（优先），免费 800 req/天，端到端验证通过
- ✅ sec_edgar Form 4 真实数据验证（MU CIK=0000723125，近30天内部人净卖出 $92,084,930）
- ✅ 13F 机构持仓缓存已建立（MU 13.95亿股 / SNDK 1.67亿股，2026-Q1 基线）
- ✅ alert 增强含交易时间+明细（message 加 [MM-DD]，detail 含内部人逐笔交易 / 量价当日量vs均值倍数）
- ✅ 单元测试通过（33/33）

## 限制与 TODO

1. **13F 机构持仓**：季度脚本 `scripts/update_13f.py` 下载 SEC 13F 数据集（按 issuer name 聚合），追加到 `data/13f_cache/{symbol}.json`。当前仅 1 期基线（2026-Q1），下季度（2026-09 SEC 发布 Q2 数据集）再跑生成 2 期可算环比。运行：`PYTHONPATH=. .venv/bin/python3 scripts/update_13f.py`
2. **yfinance 限流**：Yahoo IP 级限流，作为 Twelve Data 的 fallback。Twelve Data key 配置后优先用 Twelve Data。
3. **ETF 资金流**：免费源（iShares JSON 返回 HTML）不可得，当前通过量价异动间接监控。
4. **SK海力士**：OTC:SKHXY 在 Twelve Data 免费版不支持 + yfinance 限流，pipeline 降级跳过（不报错）。如需监控可升级 Twelve Data 付费版或从 watchlist 移除 SKHXY。

## 架构

```
config/watchlist.yaml     标的 + 阈值
src/collectors/           数据采集（price_volume/sec_edgar/finra_short/etf_flow/twelvedata）
src/analyzers/            指标计算(OBV/CMF/MFI/VWAP) + 异动检测 + 对比
src/storage/store.py      SQLite 存储（幂等 UPSERT + alerts 去重）
src/alerts/notify.py      macOS 通知 + 日志
src/pipeline.py           主调度（daily/sector/compare2d/weekly）
src/schema.py             接口约定
cron_setup.sh             cron 安装（幂等，4 任务）
scripts/update_13f.py     13F 季度更新脚本（SEC 数据集 -> data/13f_cache/）
src/analyzers/sector_rotation.py  版块 RS-Ratio / RS-Momentum 计算
src/analyzers/sector_stats.py     历史事件库 + 条件概率查询
src/analyzers/sector_detector.py  实时轮动检测 + 告警
web/app.py                Streamlit 多页面仪表盘（总览/RRG/信号/事件库/详情/回测/关于）
```

## 版块轮动监控（Sector Rotation）

基于 11 个 SPDR 版块 ETF（XLK XLE XLF XLV XLY XLP XLI XLB XLRE XLU XLC）+ SPY 基准，
检测大资金在版块间的移仓，给出持续概率与历史波动幅度/持续时间。

### 原理与反幻觉声明

- **数据源**：Twelve Data API（免费 800 req/天）拉取 ETF 日线量价，yfinance 作 fallback；不虚构数据
- **RS-Ratio** = `100 × (版块/SPY) / (版块/SPY).rolling(period).mean()`（JdK 标准公式）
- **RS-Momentum** = `100 × (RS-Ratio / RS-Ratio.shift(period) − 1)`
- **持续概率**：基于自建历史事件库（3187 事件，2006–2026，约 20 年回测）的条件概率统计，
  非主观打分；样本 <10 自动标「低置信度」，样本 ≥30 为高置信度
- **RS 指标本质是滞后确认**（基于历史量价推算），非领先预警；概率反映历史相似情境的统计规律，不保证未来重演

### 使用

```bash
# 单次扫描（美股收盘后运行）
PYTHONPATH=. .venv/bin/python3 src/pipeline.py --config config/watchlist.yaml --mode sector

# 重建历史事件库（首次部署或季度刷新）
PYTHONPATH=. .venv/bin/python3 scripts/build_sector_event_library.py

# 启动 Web 面板（多页面：总览 / RRG轮动 / 轮动信号 / 历史事件库 / 版块详情 / 回测验证 / 关于）
.venv/bin/streamlit run web/app.py
```

### 输出示例

```
[2026-08-11] 版块轮动: 能源->科技 持续概率62% 历史均持续12天 ±4.1% 样本41
[2026-08-11] 版块轮动: 金融->公用事业 持续概率18% 历史均持续3天 ±1.2% 样本33 低置信度
```

cron 每日 06:15（美股收盘后）自动扫描，轮动信号通过 macOS 通知 + Streamlit Web 面板双通道输出。

