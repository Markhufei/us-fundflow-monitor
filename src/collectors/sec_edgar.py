"""SEC EDGAR 数据采集器：Form 4 内部人交易（日级）+ 13F 机构持仓（季度）。

数据源（免费、公开、已验证可用）：
- ticker->CIK 映射: https://www.sec.gov/files/company_tickers.json
- filings 列表: https://data.sec.gov/submissions/CIK{cik}.json
- Form 4 文档: https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dash}/primarydocument.xml
- 13F 数据集: https://www.sec.gov/files/structureddata/data/form-13f-data-sets/{start}-{end}_form13f.zip
  (TSV 格式，INFOTABLE.tsv 含 NAMEOFISSUER/CUSIP/SSHPRNAMT，~95MB zip / 396MB 解压)

SEC 政策要求所有请求带 User-Agent 标识身份，频率 ≤ 10 req/s。

- Form 4（内部人交易）日级更新，已实现解析（已验证 MU 近30天内部人净卖出 $9208万）。
- 13F（机构持仓）：季度数据集，由 scripts/update_13f.py 季度下载 + 按 issuer name 聚合 +
  缓存到 data/13f_cache/{symbol}.json。collect_inst 读缓存算环比，daily pipeline 不重复下载。
"""
import os
import re
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple, Any

_USER_AGENT = "huangshifei@example.com"
_HEADERS = {"User-Agent": _USER_AGENT}
_CIK_CACHE: Dict[str, str] = {}
_REQUEST_DELAY = 0.15  # SEC 限 10 req/s，保守 0.15s


def _get_cik(symbol: str) -> Optional[str]:
    """symbol -> CIK（10位补零），带缓存。"""
    if symbol in _CIK_CACHE:
        return _CIK_CACHE[symbol]
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS, timeout=15,
        )
        r.raise_for_status()
        for v in r.json().values():
            if v.get("ticker", "").upper() == symbol.upper():
                cik = str(v["cik_str"]).zfill(10)
                _CIK_CACHE[symbol] = cik
                return cik
    except Exception:
        pass
    return None


def _get_issuer_name(symbol: str) -> Optional[str]:
    """从 SEC submissions API 获取公司法定名（用于 13F NAMEOFISSUER 匹配）。"""
    cik = _get_cik(symbol)
    if not cik:
        return None
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_HEADERS, timeout=15,
        )
        r.raise_for_status()
        return r.json().get("name")
    except Exception:
        return None


def collect_inst(symbol: str) -> Dict[str, Optional[float]]:
    """13F 机构持仓季度环比（从季度脚本生成的缓存读取）。

    缓存由 scripts/update_13f.py 季度生成（下载 SEC 13F 数据集 ~95MB，
    解压 INFOTABLE.tsv ~396MB，按 issuer name 聚合机构持仓 shares）。
    未运行季度脚本或历史不足两期时返回 None（pipeline 降级不报错）。
    """
    import json
    cache_path = f"data/13f_cache/{symbol}.json"
    if not os.path.exists(cache_path):
        return {"inst_change": None}
    try:
        with open(cache_path) as f:
            history = json.load(f).get("history", [])
        if len(history) < 2:
            return {"inst_change": None}
        history.sort(key=lambda x: x.get("date", ""), reverse=True)
        latest = history[0].get("shares")
        prev = history[1].get("shares")
        if not latest or not prev:
            return {"inst_change": None}
        return {"inst_change": (latest - prev) / prev}
    except Exception:
        return {"inst_change": None}


def _fetch_form4_filings(cik: str, days: int = 30) -> List[Tuple[str, str]]:
    """submissions API 取近 N 天 Form 4 的 (filingDate, doc_url) 列表。"""
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_HEADERS, timeout=15,
        )
        r.raise_for_status()
        rec = r.json().get("filings", {}).get("recent", {})
        forms = rec.get("form", [])
        dates = rec.get("filingDate", [])
        accs = rec.get("accessionNumber", [])
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        result = []
        for i, f in enumerate(forms):
            if f == "4" and dates[i] >= cutoff:
                acc_no_dash = accs[i].replace("-", "")
                # 根目录 primarydocument.xml 是 XBRL 数据；xslF345X06/ 下的是渲染版 HTML
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dash}/primarydocument.xml"
                result.append((dates[i], url))
        return result
    except Exception:
        return []


# 容忍 XBRL 命名空间前缀（<ns4:transactionShares> 或 <transactionShares>）
_RE_SHARES = re.compile(r"<[\w:]*transactionShares>\s*<[\w:]*value>([\-\d.]+)</[\w:]*value>")
_RE_PRICE = re.compile(r"<[\w:]*transactionPricePerShare>\s*<[\w:]*value>([\d.]+)</[\w:]*value>")
_RE_CODE = re.compile(r"<[\w:]*transactionAcquiredDisposedCode>\s*<[\w:]*value>([AD])</[\w:]*value>")
_RE_NAME = re.compile(r"<[\w:]*rptOwnerName>\s*(?:<[\w:]*value>)?\s*([^<\s][^<]*)")
_RE_TITLE = re.compile(r"<[\w:]*officerTitle>\s*(?:<[\w:]*value>)?\s*([^<\s][^<]*)")
_RE_TCODE = re.compile(r"<[\w:]*transactionCode>\s*(?:<[\w:]*value>)?\s*([A-Z])")
_RE_TDATE = re.compile(r"<[\w:]*transactionDate>\s*<[\w:]*value>([^<]+)")


def _find_one(pattern: "re.Pattern", text: str) -> Optional[str]:
    """正则取首个匹配组，无匹配返回 None。"""
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _parse_form4(url: str) -> Optional[List[Dict]]:
    """下载并解析单个 Form 4，返回交易列表，None=解析失败。

    每笔: {code(A买/D卖), tcode(P/S/M/F/G/A 交易类型), shares, price, amount,
           insider(姓名), title(职位), tdate(交易日期 transactionDate)}
    一份 Form 4 一个 reporting owner，insider/title 对该份所有交易相同。
    tcode: P=公开买入 S=公开卖出 A=归属(RSU) M=期权行权 F=税收代扣 G=赠与 D=其他处置
    """
    try:
        time.sleep(_REQUEST_DELAY)
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text
        insider = _find_one(_RE_NAME, text) or ""
        title = _find_one(_RE_TITLE, text) or ""
        shares = _RE_SHARES.findall(text)
        prices = _RE_PRICE.findall(text)
        codes = _RE_CODE.findall(text)
        tcodes = _RE_TCODE.findall(text)
        tdates = _RE_TDATE.findall(text)
        if not shares:
            return None
        txns = []
        for i, s in enumerate(shares):
            sh = float(s)
            price = float(prices[i]) if i < len(prices) else 0.0
            txns.append({
                "code": codes[i] if i < len(codes) else "D",
                "tcode": tcodes[i] if i < len(tcodes) else "",
                "shares": sh, "price": price, "amount": sh * price,
                "insider": insider, "title": title,
                "tdate": tdates[i][:10] if i < len(tdates) else "",
            })
        return txns
    except Exception:
        return None


def collect_insider(symbol: str, days: int = 30) -> Dict[str, Any]:
    """近 N 天内部人净买卖 USD + 交易明细。

    返回 {"insider_net": float|None, "insider_transactions": list}
    insider_transactions: [{date, code(A买/D卖), tcode(P/S/M/F/G/A), shares, price, amount, insider, title}]
    date 优先取 transactionDate（实际交易日），缺失才回退 filingDate（提交日）。
    数据源：SEC EDGAR Form 4（内部人交易报告，日级提交）。
    限制最多解析最近 10 份 Form 4（控制请求量）。
    """
    cik = _get_cik(symbol)
    if not cik:
        return {"insider_net": None, "insider_transactions": []}
    filings = _fetch_form4_filings(cik, days)
    if not filings:
        return {"insider_net": None, "insider_transactions": []}
    total = 0.0
    all_txns = []
    found = False
    for filing_date, url in filings[:10]:
        txns = _parse_form4(url)
        if txns is None:
            continue
        found = True
        for t in txns:
            t["date"] = t.get("tdate") or filing_date  # 优先 transactionDate
            total += t["amount"] if t["code"] == "A" else -t["amount"]
            all_txns.append(t)
    return {
        "insider_net": total if found else None,
        "insider_transactions": all_txns,
    }
