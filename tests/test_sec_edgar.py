"""SEC EDGAR 采集器测试（Form 4 + 13F 缓存读取）。"""
import sys
import os
import json
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.collectors import sec_edgar


def test_get_cik():
    """测试 symbol -> CIK 映射（mock SEC company_tickers.json）。"""
    mock_response = {
        "0": {"cik_str": "723125", "ticker": "MU", "title": "Micron Technology Inc"},
    }
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_get.return_value = mock_resp
        cik = sec_edgar._get_cik("MU")
        assert cik == "0000723125"  # 10位补零


def test_collect_insider_no_data():
    """测试无 Form 4 数据时返回 None + 空明细。"""
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": [], "primaryDocument": []}}}
        mock_get.return_value = mock_resp
        with patch("src.collectors.sec_edgar._get_cik") as mock_cik:
            mock_cik.return_value = "0000723125"
            result = sec_edgar.collect_insider("MU")
            assert result["insider_net"] is None
            assert result["insider_transactions"] == []


def test_collect_insider_with_transactions():
    """collect_insider 返回净额 + 交易明细（含 date/code/shares/price/amount）。"""
    fake_filings = [("2026-07-20", "url1"), ("2026-07-21", "url2")]
    parse_results = {
        "url1": [{"code": "A", "shares": 1000, "price": 100, "amount": 100000},
                 {"code": "D", "shares": 500, "price": 110, "amount": 55000}],
        "url2": [{"code": "D", "shares": 2000, "price": 105, "amount": 210000}],
    }
    with patch("src.collectors.sec_edgar._get_cik") as mock_cik, \
         patch("src.collectors.sec_edgar._fetch_form4_filings") as mock_filings, \
         patch("src.collectors.sec_edgar._parse_form4") as mock_parse:
        mock_cik.return_value = "0000723125"
        mock_filings.return_value = fake_filings
        mock_parse.side_effect = lambda url: parse_results[url]
        result = sec_edgar.collect_insider("MU")
    # net = +100000 -55000 -210000 = -165000
    assert result["insider_net"] == -165000
    assert len(result["insider_transactions"]) == 3
    dates = [t["date"] for t in result["insider_transactions"]]
    assert "2026-07-20" in dates and "2026-07-21" in dates


def test_collect_inst_no_cache():
    """无 13F 缓存时返回 None。"""
    with patch("os.path.exists", return_value=False):
        result = sec_edgar.collect_inst("MU")
    assert result["inst_change"] is None


def test_collect_inst_insufficient_history():
    """缓存历史不足两期时返回 None。"""
    cache = {"history": [{"date": "2026-03-01", "shares": 1000}]}
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(cache))):
        result = sec_edgar.collect_inst("MU")
    assert result["inst_change"] is None


def test_collect_inst_with_cache():
    """两期缓存计算环比（正=增持）。"""
    cache = {"history": [
        {"date": "2026-03-01", "shares": 1100},
        {"date": "2025-12-01", "shares": 1000},
    ]}
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(cache))):
        result = sec_edgar.collect_inst("MU")
    assert result["inst_change"] == 0.1  # (1100-1000)/1000


def test_collect_inst_reduction():
    """减持场景（环比为负）。"""
    cache = {"history": [
        {"date": "2026-03-01", "shares": 900},
        {"date": "2025-12-01", "shares": 1000},
    ]}
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(cache))):
        result = sec_edgar.collect_inst("MU")
    assert result["inst_change"] == -0.1


def test_get_issuer_name():
    """测试 issuer name 获取（mock submissions API）。"""
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"name": "MICRON TECHNOLOGY INC"}
        mock_get.return_value = mock_resp
        with patch("src.collectors.sec_edgar._get_cik") as mock_cik:
            mock_cik.return_value = "0000723125"
            name = sec_edgar._get_issuer_name("MU")
    assert name == "MICRON TECHNOLOGY INC"


if __name__ == "__main__":
    print("运行 SEC EDGAR 测试...")
    test_get_cik()
    print("✓ test_get_cik")
    test_collect_insider_no_data()
    print("✓ test_collect_insider_no_data")
    test_collect_insider_with_transactions()
    print("✓ test_collect_insider_with_transactions")
    test_collect_inst_no_cache()
    print("✓ test_collect_inst_no_cache")
    test_collect_inst_insufficient_history()
    print("✓ test_collect_inst_insufficient_history")
    test_collect_inst_with_cache()
    print("✓ test_collect_inst_with_cache")
    test_collect_inst_reduction()
    print("✓ test_collect_inst_reduction")
    test_get_issuer_name()
    print("✓ test_get_issuer_name")
    print("\n所有 SEC EDGAR 测试通过！")
