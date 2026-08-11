"""数据采集器冒烟测试（真实调用，需网络）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.collectors import price_volume, sec_edgar, finra_short, etf_flow


def test_price_volume_collect():
    """测试价格成交量采集（yfinance，可能因限流返回空）。"""
    data = price_volume.collect("MU", days=5)
    assert isinstance(data, list)
    if data:
        item = data[-1]
        assert "date" in item and "close" in item and "volume" in item
        assert "high" in item and "low" in item  # 新增字段


def test_sec_edgar_collect():
    """测试 SEC EDGAR Form 4 采集（已验证真实可用）。"""
    insider = sec_edgar.collect_insider("MU", days=30)
    assert isinstance(insider, dict)
    assert "insider_net" in insider
    # MU 近期有 Form 4，insider_net 应为数值（可能为 None 若解析失败）
    print(f"  MU insider_net: {insider['insider_net']}")


def test_sec_edgar_cik_lookup():
    """测试 CIK 查询。"""
    cik = sec_edgar._get_cik("MU")
    assert cik == "0000723125"


def test_finra_short_collect():
    """测试短量采集（yfinance 限流时返回 None）。"""
    data = finra_short.collect("MU")
    assert isinstance(data, dict)
    assert "short_ratio" in data


def test_etf_flow_collect():
    """测试 ETF 资金流（当前返回 None，标注数据源不可得）。"""
    data = etf_flow.collect("SOXX")
    assert isinstance(data, dict)
    assert "flow" in data
    assert data["flow"] is None  # 当前免费源不可得


if __name__ == "__main__":
    print("运行采集器冒烟测试...")
    test_sec_edgar_cik_lookup()
    print("✓ sec_edgar CIK 查询通过")
    test_sec_edgar_collect()
    print("✓ sec_edgar Form 4 采集通过")
    test_price_volume_collect()
    print("✓ price_volume 采集通过")
    test_finra_short_collect()
    print("✓ finra_short 采集通过")
    test_etf_flow_collect()
    print("✓ etf_flow 采集通过")
    print("\n所有冒烟测试通过！")
