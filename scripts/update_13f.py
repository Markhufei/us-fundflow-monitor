"""季度更新 13F 机构持仓缓存。

下载 SEC 13F 数据集（~95MB zip），解压 INFOTABLE.tsv（~396MB），
按 issuer name 聚合每只股票的机构持仓 shares，追加到 data/13f_cache/{symbol}.json。

sec_edgar.collect_inst 读缓存计算季度环比，daily pipeline 不重复下载。

运行（季度初：2/5/8/11 月数据发布后）:
    PYTHONPATH=. .venv/bin/python3 scripts/update_13f.py

数据源: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
"""
import io
import os
import re
import sys
import json
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from src.collectors import sec_edgar

_USER_AGENT = "huangshifei@example.com"
_HEADERS = {"User-Agent": _USER_AGENT}
_INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
_CACHE_DIR = "data/13f_cache"
_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _latest_dataset_url():
    """从 SEC 页面解析最新 13F 数据集 URL 和文件名。"""
    r = requests.get(_INDEX_URL, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    m = re.search(r'href="(/files/structureddata/data/form-13f-data-sets/[^"]*form13f\.zip)"', r.text)
    if not m:
        return None, None
    path = m.group(1)
    return "https://www.sec.gov" + path, path.split("/")[-1]


def _parse_quarter_date(filename):
    """从文件名 01mar2026-31may2026_form13f.zip 提取季度起始日期 YYYY-MM-01。"""
    m = re.match(r"\d{2}([a-z]{3})(\d{4})-", filename)
    if m:
        mon, year = m.groups()
        return f"{year}-{_MONTH_MAP.get(mon, '01')}-01"
    return None


def _aggregate_holdings(zip_content, issuer_names):
    """从 zip 解压 INFOTABLE.tsv，按 issuer name 聚合 shares。

    issuer_names: {symbol: legal_name}
    Returns: {symbol: total_shares}
    """
    result = {sym: 0 for sym in issuer_names}
    targets = {sym: name.upper() for sym, name in issuer_names.items() if name}
    if not targets:
        return result
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        with zf.open("INFOTABLE.tsv") as f:
            header = f.readline().decode("utf-8", errors="ignore").split("\t")
            try:
                name_idx = header.index("NAMEOFISSUER")
                shares_idx = header.index("SSHPRNAMT")
            except ValueError:
                return result
            for line in f:
                parts = line.decode("utf-8", errors="ignore").split("\t")
                if name_idx >= len(parts) or shares_idx >= len(parts):
                    continue
                name = parts[name_idx].upper()
                for sym, target in targets.items():
                    if target in name:
                        try:
                            result[sym] += int(parts[shares_idx])
                        except ValueError:
                            pass
    return result


def update():
    url, filename = _latest_dataset_url()
    if not url:
        print("❌ 无法获取 13F 数据集 URL")
        return
    quarter_date = _parse_quarter_date(filename) or "unknown"
    print(f"最新数据集: {filename} (季度起始 {quarter_date})")

    # 廉价早退（周检 launchd 用）：全部缓存已含该季度则跳过 95MB 下载
    if os.path.isdir(_CACHE_DIR):
        cache_files = [f for f in os.listdir(_CACHE_DIR) if f.endswith(".json")]
        if cache_files and all(
            any(h.get("date") == quarter_date
                for h in json.load(open(os.path.join(_CACHE_DIR, f))).get("history", []))
            for f in cache_files
        ):
            print(f"✅ 缓存已含 {quarter_date}（{filename}），无需下载")
            return

    # 读 watchlist
    import yaml
    with open("config/watchlist.yaml") as f:
        config = yaml.safe_load(f)
    symbols = [s["symbol"] for s in config.get("watchlist", {}).get("stocks", [])]

    # 获取 issuer name
    issuer_names = {}
    for sym in symbols:
        name = sec_edgar._get_issuer_name(sym)
        if name:
            issuer_names[sym] = name
            print(f"  {sym} -> {name}")
        else:
            print(f"  {sym} -> ⚠️ 无法获取 issuer name，跳过")

    if not issuer_names:
        print("❌ 无有效 issuer name，退出")
        return

    # 下载 zip（~95MB，1-2 分钟）
    print(f"下载 {url}（~95MB）...")
    r = requests.get(url, headers=_HEADERS, timeout=300)
    r.raise_for_status()
    print(f"下载完成 ({len(r.content) // 1024 // 1024}MB)，聚合持仓...")

    holdings = _aggregate_holdings(r.content, issuer_names)

    # 存缓存
    os.makedirs(_CACHE_DIR, exist_ok=True)
    for sym, shares in holdings.items():
        cache_path = os.path.join(_CACHE_DIR, f"{sym}.json")
        history = []
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                history = json.load(f).get("history", [])
        # 幂等：同季度不重复追加
        if not any(h.get("date") == quarter_date for h in history):
            history.append({"date": quarter_date, "shares": shares, "source": filename})
        with open(cache_path, "w") as f:
            json.dump({"history": history}, f, indent=2, ensure_ascii=False)
        print(f"  ✅ {sym}: {shares:,} shares -> {cache_path}")

    print("\n13F 缓存更新完成。collect_inst 可读缓存算环比。")


if __name__ == "__main__":
    update()
