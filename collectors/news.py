"""
네이버 금융 RSS로 증시 뉴스 헤드라인 수집 (선택 모듈)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

_RSS_URLS = [
    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def get_market_news(limit: int = 10) -> list[dict[str, Any]]:
    """최신 증시 뉴스 헤드라인 리스트 반환."""
    headlines: list[dict] = []
    try:
        resp = requests.get(
            "https://finance.naver.com/news/mainnews.naver",
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        # 간단한 정규식으로 헤드라인 추출
        titles = re.findall(r'class="articleSubject"[^>]*>([^<]+)<', resp.text)
        for title in titles[:limit]:
            headlines.append({"title": _strip_html(title)})
    except Exception as e:
        print(f"[news] 뉴스 수집 실패: {e}")
    return headlines
