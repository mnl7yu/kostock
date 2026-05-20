"""
네이버 금융 API / HTML 기반 KOSPI·KOSDAQ 시장 데이터 수집
(pykrx KRX API 불안정 이슈로 네이버 금융으로 대체)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _get(url: str, **kwargs) -> requests.Response:
    return requests.get(url, headers=_HDR, timeout=12, **kwargs)


def _clean_num(s: str) -> float:
    """'1,234.56' → 1234.56, '-1.07%' → -1.07"""
    s = s.replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


# ── 지수 ─────────────────────────────────────────────────────────────────────

def get_index_summary(date: str | None = None) -> dict[str, Any]:
    """KOSPI / KOSDAQ / KOSPI200 지수 현황 반환."""
    codes = {
        "KOSPI":   ("KOSPI",   "KOSPI"),
        "KOSDAQ":  ("KOSDAQ",  "KOSDAQ"),
        "KOSPI200":("KPI200",  "KOSPI 200"),
    }
    result = {}
    for key, (api_code, label) in codes.items():
        try:
            r = _get(f"https://m.stock.naver.com/api/index/{api_code}/basic")
            d = r.json()
            close  = _clean_num(d.get("closePrice", "0"))
            change = _clean_num(d.get("compareToPreviousClosePrice", "0"))
            pct    = _clean_num(d.get("fluctuationsRatio", "0"))
            result[key] = {
                "name":       label,
                "close":      close,
                "change":     change,
                "change_pct": pct,
                "volume":     0,
            }
        except Exception as e:
            print(f"[market_data] {key} 조회 실패: {e}")
            result[key] = {}
    return result


def get_last_trading_date(reference: datetime | None = None, offset: int = 0) -> str:
    """네이버 API 기준 최근 거래일 반환."""
    try:
        r = _get("https://m.stock.naver.com/api/index/KOSPI/basic")
        d = r.json()
        traded_at = d.get("localTradedAt", "")  # "2026-04-30T13:55:00+09:00"
        if traded_at:
            return traded_at[:10].replace("-", "")
    except Exception:
        pass
    return datetime.today().strftime("%Y%m%d")


# ── 시장 폭 ───────────────────────────────────────────────────────────────────

def get_market_breadth(date: str | None = None, market: str = "KOSPI") -> dict[str, int]:
    """
    상승/하락/보합 종목 수.
    네이버 상승/하락 페이지 rows 수 + 전체 종목 수로 계산.
    """
    sosok = "0" if market == "KOSPI" else "1"
    try:
        r_rise = _get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}")
        soup = BeautifulSoup(r_rise.text, "html.parser")
        rows = soup.select("table.type_2 tr")
        data_rows = [r for r in rows if r.select("td.number")]
        advance = len(data_rows)

        r_fall = _get(f"https://finance.naver.com/sise/sise_fall.naver?sosok={sosok}")
        soup2 = BeautifulSoup(r_fall.text, "html.parser")
        rows2 = soup2.select("table.type_2 tr")
        data_rows2 = [r for r in rows2 if r.select("td.number")]
        decline = len(data_rows2)

        # 전체 종목 수 추정 (KOSPI ≈ 800, KOSDAQ ≈ 1,600)
        total_est = 800 if market == "KOSPI" else 1600
        unchanged = max(0, total_est - advance - decline)

        return {
            "advance":   advance,
            "decline":   decline,
            "unchanged": unchanged,
            "total":     total_est,
        }
    except Exception as e:
        print(f"[market_data] breadth 조회 실패: {e}")
        return {}


# ── 상위 등락 종목 ────────────────────────────────────────────────────────────

def _parse_movers_page(sosok: str, page_type: str, top_n: int) -> list[dict]:
    """
    page_type: 'sise_rise' | 'sise_fall'
    """
    url = f"https://finance.naver.com/sise/{page_type}.naver?sosok={sosok}"
    try:
        r = _get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type_2 tr")
        results = []
        for row in rows:
            cols = row.select("td")
            if len(cols) < 5:
                continue
            try:
                rank_td = cols[0].get_text(strip=True)
                if not rank_td.isdigit():
                    continue
                name_el = row.select_one("a.tltle") or row.select_one("td.name a")
                name = name_el.get_text(strip=True) if name_el else "?"
                href = name_el.get("href", "") if name_el else ""
                ticker = re.search(r"code=(\d+)", href)
                ticker = ticker.group(1) if ticker else ""
                close     = _clean_num(cols[2].get_text(strip=True))
                change_pct_str = cols[4].get_text(strip=True)
                change_pct = _clean_num(change_pct_str)
                if page_type == "sise_fall" and change_pct > 0:
                    change_pct = -change_pct
                volume = _clean_num(cols[5].get_text(strip=True))
                results.append({
                    "ticker":     ticker,
                    "name":       name,
                    "close":      int(close),
                    "change_pct": round(change_pct, 2),
                    "volume":     int(volume),
                })
                if len(results) >= top_n:
                    break
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"[market_data] movers 조회 실패 ({page_type}): {e}")
        return []


def get_top_movers(
    date: str | None = None, market: str = "KOSPI", top_n: int = 10
) -> dict[str, list[dict]]:
    sosok = "0" if market == "KOSPI" else "1"
    return {
        "gainers": _parse_movers_page(sosok, "sise_rise", top_n),
        "losers":  _parse_movers_page(sosok, "sise_fall", top_n),
    }


# ── 업종별 시세 ──────────────────────────────────────────────────────────────

def get_sector_performance(date: str | None = None) -> list[dict[str, Any]]:
    """네이버 금융 업종별 시세 파싱."""
    try:
        r = _get("https://finance.naver.com/sise/")
        soup = BeautifulSoup(r.text, "html.parser")
        sectors = []
        # 업종별 시세 테이블
        tbl = soup.find("table", string=re.compile("업종")) or soup.select_one("table")
        tbl0 = soup.select("table")[0] if soup.select("table") else None
        if not tbl0:
            return []
        for row in tbl0.select("tr"):
            cols = row.select("td")
            if len(cols) < 2:
                continue
            name = cols[0].get_text(strip=True)
            pct_txt = cols[1].get_text(strip=True).replace("%", "")
            try:
                pct = float(pct_txt)
            except ValueError:
                continue
            if name:
                sectors.append({"name": name, "change_pct": pct})
        sectors.sort(key=lambda x: x["change_pct"], reverse=True)
        return sectors
    except Exception as e:
        print(f"[market_data] sector 조회 실패: {e}")
        return []


# ── 관심종목 ──────────────────────────────────────────────────────────────────

def get_stock_detail(ticker: str, date: str | None = None) -> dict[str, Any]:
    """네이버 금융 API로 개별 종목 현재가 조회."""
    try:
        # basic: 종목명, 현재가, 등락률
        r = _get(f"https://m.stock.naver.com/api/stock/{ticker}/basic")
        d = r.json()
        close      = _clean_num(d.get("closePrice", "0"))
        change_pct = _clean_num(d.get("fluctuationsRatio", "0"))
        name       = d.get("stockName", ticker)

        # price: 거래량, OHLC
        volume = high52 = low52 = 0
        open_ = high = low = 0
        try:
            r2 = _get(f"https://m.stock.naver.com/api/stock/{ticker}/price")
            prices = r2.json()
            if isinstance(prices, list) and prices:
                p = prices[0]
                volume = int(p.get("accumulatedTradingVolume", 0))
                open_  = int(_clean_num(p.get("openPrice", "0")))
                high   = int(_clean_num(p.get("highPrice", "0")))
                low    = int(_clean_num(p.get("lowPrice", "0")))
        except Exception:
            pass

        return {
            "ticker":     ticker,
            "name":       name,
            "open":       open_,
            "high":       high,
            "low":        low,
            "close":      int(close),
            "change_pct": change_pct,
            "volume":     volume,
            "high52":     high52,
            "low52":      low52,
        }
    except Exception as e:
        print(f"[market_data] {ticker} 조회 실패: {e}")
        return {"ticker": ticker, "name": "조회실패", "close": 0, "change_pct": 0.0}


def get_watchlist_data(watchlist: list[dict], date: str | None = None) -> list[dict[str, Any]]:
    return [get_stock_detail(item["ticker"]) for item in watchlist]


# ── 통합 스냅샷 ───────────────────────────────────────────────────────────────

def get_market_snapshot(watchlist: list[dict], mode: str = "closing") -> dict[str, Any]:
    date = get_last_trading_date()
    print(f"[market_data] 기준일: {date} / 모드: {mode}")

    return {
        "date":      date,
        "mode":      mode,
        "index":     get_index_summary(date),
        "breadth":   {
            "KOSPI":  get_market_breadth(date, "KOSPI"),
            "KOSDAQ": get_market_breadth(date, "KOSDAQ"),
        },
        "movers":    {
            "KOSPI":  get_top_movers(date, "KOSPI"),
            "KOSDAQ": get_top_movers(date, "KOSDAQ"),
        },
        "sectors":   get_sector_performance(date) if mode == "closing" else [],
        "watchlist": get_watchlist_data(watchlist, date),
    }
