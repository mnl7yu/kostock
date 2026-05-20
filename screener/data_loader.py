"""
데이터 로더 — FDR + Naver Finance 기반
3단계 접근: 유니버스 → 히스토리 → 상세(후보 종목만)
"""
from __future__ import annotations

import pickle
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    import FinanceDataReader as fdr
    FDR_OK = True
except ImportError:
    FDR_OK = False

CACHE_DIR = Path(__file__).parent.parent / ".cache" / "prices"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

# ── 1단계: 전체 유니버스 ─────────────────────────────────────────────────────

def get_universe(min_marcap_bil: int = 500) -> pd.DataFrame:
    """
    KOSPI + KOSDAQ 전 종목 현재 시세.
    min_marcap_bil: 최소 시가총액 (억원). 기본 500억 이상만 포함.
    반환: Code, Name, Market, Close, Volume, Amount, Marcap, Shares
    """
    if not FDR_OK:
        raise RuntimeError("FinanceDataReader 미설치: pip install finance-datareader")

    df_k = fdr.StockListing("KOSPI")
    df_d = fdr.StockListing("KOSDAQ")
    df = pd.concat([df_k, df_d], ignore_index=True)

    # 컬럼 표준화
    df = df.rename(columns={
        "Code": "ticker",
        "Name": "name",
        "Market": "market",
        "Close": "close",
        "Volume": "volume",
        "Amount": "amount",
        "Marcap": "marcap",
        "Stocks": "shares",
        "Changes": "change",
        "ChagesRatio": "change_pct",
    })

    # 필터: 시가총액 500억+, 우선주·ETF 제외
    df["marcap"] = pd.to_numeric(df["marcap"], errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df["close"] = pd.to_numeric(df["close"], errors="coerce").fillna(0)

    # 우선주(6자리 코드 마지막이 0·5 이외) 및 ETF 제거
    df = df[df["ticker"].str.match(r"^\d{6}$")]
    df = df[~df["ticker"].str.endswith(("5", "7", "9"))]    # 우선주 제거
    df = df[df["marcap"] >= min_marcap_bil * 1e8]           # 시가총액 필터
    df = df[df["volume"] > 10_000]                          # 거래량 최소
    df = df[df["close"] > 500]                              # 주가 최소

    df = df.set_index("ticker")
    df.index.name = "ticker"
    return df[["name", "market", "close", "change_pct", "volume", "amount", "marcap", "shares"]]


# ── 2단계: OHLCV 히스토리 ────────────────────────────────────────────────────

def get_price_history(ticker: str, days: int = 120, use_cache: bool = True) -> pd.DataFrame:
    """
    종목 OHLCV 히스토리. 캐시 있으면 재사용 (당일 한정).
    반환: DataFrame[Open, High, Low, Close, Volume, Change] index=Date
    """
    cache_file = CACHE_DIR / f"{datetime.today().strftime('%Y%m%d')}_{ticker}.pkl"

    if use_cache and cache_file.exists():
        try:
            return pickle.load(open(cache_file, "rb"))
        except Exception:
            pass

    if not FDR_OK:
        return pd.DataFrame()

    end = datetime.today()
    start = end - timedelta(days=days * 1.6)  # 주말·공휴일 감안

    try:
        df = fdr.DataReader(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        df = df.tail(days)
        if use_cache and not df.empty:
            pickle.dump(df, open(cache_file, "wb"))
        return df
    except Exception:
        return pd.DataFrame()


def get_price_history_range(
    ticker: str, start: str, end: str
) -> pd.DataFrame:
    """백테스팅용 - 특정 기간 히스토리."""
    cache_key = f"bt_{ticker}_{start[:4]}"
    cache_file = CACHE_DIR / f"{cache_key}.pkl"
    if cache_file.exists():
        try:
            return pickle.load(open(cache_file, "rb"))
        except Exception:
            pass
    try:
        df = fdr.DataReader(ticker, start, end)
        if not df.empty:
            pickle.dump(df, open(cache_file, "wb"))
        return df
    except Exception:
        return pd.DataFrame()


# ── 3단계: 상세 재무 (후보 종목 한정) ────────────────────────────────────────

def _naver_get(url: str) -> requests.Response:
    time.sleep(0.15)  # 네이버 과부하 방지
    return requests.get(url, headers=HEADERS, timeout=12)


def get_per_pbr_eps(ticker: str) -> dict[str, float]:
    """
    네이버 모바일 금융 JSON API에서 PER, PBR, EPS, BPS 수집.
    endpoint: m.stock.naver.com/api/stock/{ticker}/finance/annual
    """
    result = {"per": 0.0, "pbr": 0.0, "eps": 0.0, "bps": 0.0}
    try:
        r = _naver_get(
            f"https://m.stock.naver.com/api/stock/{ticker}/finance/annual"
        )
        data = r.json()
        info = data.get("financeInfo", {})
        titles = info.get("trTitleList", [])
        rows   = info.get("rowList", [])

        # 가장 최근 확정 기간 (isConsensus == 'N')
        confirmed = [t["key"] for t in titles if t.get("isConsensus") == "N"]
        if not confirmed:
            return result
        latest_key = confirmed[-1]

        def _val(row_title: str) -> float:
            for row in rows:
                if row.get("title") == row_title:
                    v = row.get("columns", {}).get(latest_key, {}).get("value", "")
                    try:
                        return float(str(v).replace(",", "").strip())
                    except Exception:
                        return 0.0
            return 0.0

        result["per"] = _val("PER")
        result["pbr"] = _val("PBR")
        result["eps"] = _val("EPS")
        result["bps"] = _val("BPS")

    except Exception:
        pass
    return result


def get_foreign_buying(ticker: str, days: int = 5) -> dict[str, Any]:
    """
    외국인 순매수 데이터 (최근 N일).
    returns: {
        'daily_net': [일별 외국인 순매수 (주)],
        'consecutive_buy': 연속 순매수 일수,
        'ownership_ratio': 외국인 지분율 (%),
    }
    """
    result = {"daily_net": [], "consecutive_buy": 0, "ownership_ratio": 0.0}
    try:
        r = _naver_get(f"https://finance.naver.com/item/frgn.naver?code={ticker}")
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type2 tr")

        daily_net = []
        for row in rows:
            cells = row.select("td")
            if len(cells) < 8:
                continue
            date_txt = cells[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue
            # cells[4] = 외국인 순매수
            net_txt = cells[4].get_text(strip=True).replace(",", "").replace("+", "")
            try:
                daily_net.append(int(net_txt))
            except Exception:
                daily_net.append(0)
            if len(daily_net) >= days:
                break

        result["daily_net"] = daily_net

        # 연속 순매수 계산
        consec = 0
        for v in daily_net:
            if v > 0:
                consec += 1
            else:
                break
        result["consecutive_buy"] = consec

        # 외국인 지분율
        ratio_m = re.search(r"외국인\s*지분\s*율[^\d]*([\d.]+)%", soup.get_text())
        if ratio_m:
            result["ownership_ratio"] = float(ratio_m.group(1))

    except Exception:
        pass
    return result


def get_institutional_trend(ticker: str) -> dict[str, Any]:
    """
    기관 순매수 동향 (이번 주 vs 전주 비교).
    """
    result = {"this_week": 0, "last_week": 0, "turned_positive": False}
    try:
        # 기관 매매 데이터는 네이버 투자자별 매매동향 페이지
        r = _naver_get(f"https://finance.naver.com/item/sise_investor.naver?code={ticker}")
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type2 tr")

        inst_vals = []
        for row in rows:
            cells = row.select("td")
            if len(cells) < 6:
                continue
            date_txt = cells[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue
            # cells[2] = 기관 순매수
            try:
                val = int(cells[2].get_text(strip=True).replace(",", "").replace("+", "") or "0")
                inst_vals.append(val)
            except Exception:
                inst_vals.append(0)
            if len(inst_vals) >= 10:
                break

        if inst_vals:
            result["this_week"] = sum(inst_vals[:5])
            result["last_week"] = sum(inst_vals[5:10])
            result["turned_positive"] = (
                result["this_week"] > 0 and result["last_week"] <= 0
            )
    except Exception:
        pass
    return result


def get_stock_news(ticker: str, limit: int = 3) -> list[str]:
    """최근 뉴스 헤드라인."""
    headlines = []
    try:
        r = _naver_get(f"https://finance.naver.com/item/news_news.naver?code={ticker}&page=1")
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("td.title a"):
            title = a.get_text(strip=True)
            if title:
                headlines.append(title)
            if len(headlines) >= limit:
                break
    except Exception:
        pass
    return headlines
